"""单板回灌执行器 (被 _launch_terminals 调用,每个终端窗口运行一个实例)

流程:
1. SSH 连接开发板
2. 执行 reboot (可选,默认开启)
3. 等待板子重启完成后重连
4. 挂载共享 → cd 回灌目录 → 执行脚本
5. 流式输出到终端
6. (可选) 回灌结束后删除脚本文件
"""

import sys
import os
import time
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from devboard_toolkit.config import load_boards, load_replay_env
from devboard_toolkit.batch_replay import _derive_paths, unc_to_board_path
from devboard_toolkit.ssh_client import build_client, safe_close


def _wait_for_board(board_cfg, timeout=90, interval=3):
    """等待开发板重启完成,通过 SSH 尝试连接判断是否恢复

    Args:
        board_cfg: 开发板配置 dict
        timeout: 最大等待秒数
        interval: 重试间隔秒数
    Returns:
        (client, True) 连接成功 / (None, False) 超时
    """
    print(f"  [*] 等待板子重启 (最多 {timeout}s)...", end="", flush=True)
    elapsed = 0
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        try:
            client = build_client(board_cfg)
            print(f"\n  [+] 板子已恢复 (等待 {elapsed}s)")
            return client, True
        except Exception:
            print(".", end="", flush=True)
    print(f"\n  [!] 等待超时 ({timeout}s),板子未恢复")
    return None, False


def main():
    parser = argparse.ArgumentParser(description="单板回灌执行器")
    parser.add_argument("board", help="开发板名称")
    parser.add_argument("replay_folder", help="回灌文件夹名")
    parser.add_argument("script_name", help="脚本文件名")
    parser.add_argument("--log-dir", default="", help="日志保存目录")
    parser.add_argument("--app-suffix", default="", help="感知包后缀")
    parser.add_argument("--delete-script", action="store_true",
                        help="回灌结束后删除脚本")
    parser.add_argument("--no-reboot", action="store_true",
                        help="跳过 reboot 步骤")
    args = parser.parse_args()

    # === 日志重定向: 同时输出到终端 + 文件 ===
    log_file_path = ""
    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr
    if args.log_dir:
        try:
            os.makedirs(args.log_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            safe_board = args.board.replace("/", "_").replace("\\", "_")
            safe_suffix = args.app_suffix.replace("/", "_").replace("\\", "_") \
                if args.app_suffix else "run"
            log_file_name = f"{safe_board}_{safe_suffix}_{ts}.log"
            log_file_path = os.path.join(args.log_dir, log_file_name)

            class _TeeLogger:
                def __init__(self, file_obj, std_stream):
                    self._file = file_obj
                    self._std = std_stream

                def write(self, data):
                    try:
                        self._file.write(data)
                        self._file.flush()
                    except Exception:
                        pass
                    try:
                        self._std.write(data)
                        self._std.flush()
                    except Exception:
                        pass

                def flush(self):
                    try:
                        self._file.flush()
                    except Exception:
                        pass
                    try:
                        self._std.flush()
                    except Exception:
                        pass

            _log_f = open(log_file_path, "w", encoding="utf-8", buffering=1)
            sys.stdout = _TeeLogger(_log_f, _orig_stdout)
            sys.stderr = _TeeLogger(_log_f, _orig_stderr)
            # 把 _log_f 挂到 module 级避免被 GC 关闭
            globals()["_log_file_handle"] = _log_f
        except Exception as e:
            # 日志初始化失败不影响主流程,仅在终端提示
            print(f"[!] 日志文件初始化失败: {e}", file=_orig_stderr)
            log_file_path = ""

    boards = load_boards()
    board_cfg = boards.get(args.board)
    if not board_cfg:
        print(f"[!] 开发板 {args.board} 不存在于配置中")
        input("按回车键退出...")
        return 1

    env = load_replay_env()
    mount_source = env.get("mount_source", "")
    mount_point = env.get("mount_point", "/mnt")
    mount_options = env.get("mount_options", "")

    unc_testbed, linux_testbed_base = _derive_paths(env)
    unc_replay_folder = os.path.normpath(
        os.path.join(unc_testbed, args.replay_folder))
    linux_replay_folder = f"{linux_testbed_base}/{args.replay_folder}"

    script_unc_path = os.path.join(unc_replay_folder, args.script_name)
    board_script_path = unc_to_board_path(
        script_unc_path, mount_source, mount_point)

    host = board_cfg.get("host", "?")
    print(f"{'=' * 60}")
    print(f"  [板] {args.board} ({host})")
    print(f"{'=' * 60}")
    print(f"  [*] 板上脚本路径: {board_script_path}")
    print(f"  [*] 回灌目录(板端): {linux_replay_folder}")
    print(f"  [*] 正在连接开发板...")

    try:
        client = build_client(board_cfg)
    except Exception as e:
        print(f"  [!] SSH 连接失败: {e}")
        input("按回车键退出...")
        return 1

    print(f"  [+] SSH 连接成功")

    # === Reboot 阶段 ===
    if not args.no_reboot:
        print(f"  [*] 执行 reboot ...")
        try:
            client.exec_command("reboot", timeout=5)
        except Exception:
            pass
        safe_close(client)
        client = None

        client, ok = _wait_for_board(board_cfg, timeout=90, interval=3)
        if not ok:
            print(f"  [!] 板子未恢复,无法继续")
            input("按回车键退出...")
            return 1
    else:
        print(f"  [*] 跳过 reboot (--no-reboot)")

    # === 挂载 + 执行脚本 ===
    mount_cmd = (
        f"mount | grep {mount_point} || "
        f"mount -t cifs {mount_source} {mount_point} -o {mount_options}"
    )
    cd_cmd = f"cd {linux_replay_folder}"
    exec_cmd = f"chmod +x {args.script_name} ; ./{args.script_name}"
    full_cmd = f"{mount_cmd} ; {cd_cmd} && {exec_cmd}"

    print(f"  [*] 执行命令: {full_cmd[:200]}")
    print()

    try:
        stdin, stdout, stderr = client.exec_command(
            f"sh -lc '{full_cmd}'", timeout=None, get_pty=True)
    except Exception as e:
        print(f"  [!] 命令执行失败: {e}")
        safe_close(client)
        input("按回车键退出...")
        return 1

    # 流式输出
    while True:
        try:
            line = stdout.readline()
            if line:
                print(line, end="", flush=True)
            elif stdout.channel.exit_status_ready():
                break
            else:
                time.sleep(0.1)
        except Exception:
            break

    try:
        exit_code = stdout.channel.recv_exit_status()
    except Exception:
        exit_code = -1

    safe_close(client)

    print()
    if exit_code == 0:
        print(f"  [+] 回灌完成 (exit_code=0)")
    else:
        print(f"  [!] 回灌结束 (exit_code={exit_code})")

    # === 写 .done 标记 (供 GUI 主循环检测所有板是否回灌完成) ===
    if args.log_dir:
        try:
            safe_board = args.board.replace("/", "_").replace("\\", "_")
            done_path = os.path.join(args.log_dir, f"{safe_board}.done")
            # 写入 exit_code + 时间戳
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with open(done_path, "w", encoding="utf-8") as df:
                df.write(f"board={args.board}\nexit_code={exit_code}\nfinish_at={ts}\n")
        except Exception as e:
            print(f"  [!] 写 done 标记失败: {e}")

    # 可选: 删除脚本文件
    if args.delete_script:
        try:
            if os.path.isfile(script_unc_path):
                os.remove(script_unc_path)
                print(f"  [+] 已删除脚本: {args.script_name}")
        except Exception as e:
            print(f"  [!] 删除脚本失败: {e}")

    if log_file_path:
        print()
        print(f"  [i] 终端日志已保存: {log_file_path}")
        # 显式 flush 并关闭日志文件,确保内容落盘
        try:
            _fh = globals().get("_log_file_handle")
            if _fh:
                _fh.flush()
                _fh.close()
        except Exception:
            pass
        # 恢复 stdout/stderr, 避免 input() 之前出异常
        sys.stdout = _orig_stdout
        sys.stderr = _orig_stderr

    print()
    input("按回车键退出...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
