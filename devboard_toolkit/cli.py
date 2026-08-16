"""CLI 入口"""

import os
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import load_boards, config_sources
from .ssh_client import build_client, run_cmd
from .checker import check_board
from .shell import interactive_shell
from .prober import probe_boards, print_results as print_probe_results
from .usage_check import check_usage_one, print_results as print_usage_results
from .script_gen import gen_list_replay_interactive
from .batch_replay import batch_replay_main, full_auto_main


def build_parser():
    parser = argparse.ArgumentParser(
        prog="devboard-toolkit",
        description="开发板工具箱 - SSH 连接 / 连通性测试 / 使用状态检测 / 体检 / 命令执行 / 交互 shell",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python run.py --usage-all                # 并发检测所有板使用状态\n"
               "  python run.py --usage board1 board3      # 检测指定板使用状态\n"
               "  python run.py --test-all                 # 并发测试所有板连通性\n"
               "  python run.py board1 --check             # 体检(回灌前检查)\n"
               "  python run.py board1 -c \"uname -a\"      # 执行单条命令\n"
               "  python run.py board1 -i                  # 进入交互式 shell(默认)\n"
               "  python run.py --gen-list-replay          # 生成列表回灌启动脚本\n"
               "  python run.py --batch-replay             # 一键批量回灌(检测空板+均分txt+生成脚本)\n"
               "  python run.py --auto-build               # Jenkins 自动编译感知包(下载后可衔接回灌)\n"
               "  python run.py --full-auto                # 全流程: 选回灌目录→自动编译→自动回灌\n"
               "  python run.py --data-preproc             # 数据预处理(Jira提取/视频复制+ADAS预处理)\n",
    )
    parser.add_argument("board", nargs="?", help="开发板名称")
    parser.add_argument("-l", "--list", action="store_true", help="列出已配置开发板")
    parser.add_argument("-c", "--cmd", help="执行单条命令并返回输出")
    parser.add_argument(
        "--check", action="store_true",
        help="检测单块开发板状态(是否有人用 / 自启脚本)",
    )
    parser.add_argument(
        "-i", "--interactive", action="store_true", help="进入交互式 shell(默认模式)",
    )
    parser.add_argument(
        "--show-sources", action="store_true",
        help="显示配置文件搜索路径",
    )
    parser.add_argument(
        "--test-all", action="store_true",
        help="并发测试所有已配置开发板的 SSH 连通性",
    )
    parser.add_argument(
        "--test", nargs="+", metavar="BOARD",
        help="并发测试指定开发板的 SSH 连通性(可传多个)",
    )
    parser.add_argument(
        "--usage-all", action="store_true",
        help="并发检测所有已配置开发板的使用状态(有没有在跑东西)",
    )
    parser.add_argument(
        "--usage", nargs="+", metavar="BOARD",
        help="检测指定开发板的使用状态(可传多个)",
    )
    parser.add_argument(
        "--gen-list-replay", action="store_true",
        help="交互式生成列表回灌启动脚本",
    )
    parser.add_argument(
        "--batch-replay", action="store_true",
        help="一键批量回灌(检测空板 + 均分txt + 批量生成脚本)",
    )
    parser.add_argument(
        "--auto-build", action="store_true",
        help="Jenkins 自动编译感知包(下载后可衔接批量回灌)",
    )
    parser.add_argument(
        "--full-auto", action="store_true",
        help="全流程自动化: 选回灌目录 → 自动编译(产物+runtime放该目录) → 自动回灌",
    )
    parser.add_argument(
        "--data-preproc", action="store_true",
        help="数据预处理: Jira 链接提取视频路径 / 视频路径复制 → 车型分类 → ADAS 预处理",
    )
    parser.add_argument(
        "-j", "--workers", type=int, default=6,
        help="并发数,默认 6",
    )
    return parser


def _print_boards(boards: dict):
    if not boards:
        print("(无已配置开发板)")
        return
    print("已配置开发板:")
    for name, cfg in boards.items():
        port = cfg.get("port", 22)
        user = cfg.get("user", "?")
        host = cfg.get("host", "?")
        pwd = cfg.get("password", "")
        pwd_tag = "已填" if (pwd and pwd != "YOUR_PASSWORD_HERE") else "待填密码"
        print(f"  {name:12s}  {user}@{host}:{port}    ({pwd_tag})")


def _project_root() -> str:
    """顶层项目目录 (D:\\Desktop\\devboard-toolkit),供 import 顶层脚本用"""
    # cli.py 在 devboard_toolkit/cli.py, 项目根是上一级
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _usage_all(boards: dict, names: list, workers: int) -> int:
    """并发检测使用状态"""
    print(f"[*] 并发检测 {len(names)} 块开发板使用状态 (workers={workers}) ...")
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(check_usage_one, n, boards[n]): n for n in names}
        for fut in as_completed(futs):
            results.append(fut.result())
    # 按配置顺序排序
    order = {n: i for i, n in enumerate(names)}
    results.sort(key=lambda r: order.get(r.name, 999))
    print()
    print_usage_results(results)
    return 0  # 使用状态不返回非0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    boards = load_boards()

    # ===== Jenkins 自动编译感知包 =====
    if args.auto_build:
        # jenkins_build.py 是顶层脚本(不在包内),直接 import 对应入口函数
        sys.path.insert(0, _project_root())
        try:
            from jenkins_build import auto_build_main
        except Exception as e:
            print(f"[!] 加载 jenkins_build 模块失败: {e}")
            return 1
        try:
            _app, rc = auto_build_main()
            return rc if isinstance(rc, int) else 1
        except KeyboardInterrupt:
            print("\n[!] 已取消")
            return 1
        except Exception as e:
            print(f"[!] 自动编译失败: {e}")
            return 1

    # ===== 批量回灌(检测空板 + 均分txt + 生成脚本) =====
    if args.batch_replay:
        try:
            return batch_replay_main()
        except KeyboardInterrupt:
            print("\n[!] 已取消")
            return 1
        except Exception as e:
            print(f"[!] 批量回灌失败: {e}")
            return 1

    # ===== 全流程: 自动编译 + 自动回灌 =====
    if args.full_auto:
        sys.path.insert(0, _project_root())
        try:
            return full_auto_main()
        except KeyboardInterrupt:
            print("\n[!] 已取消")
            return 1
        except Exception as e:
            print(f"[!] 全流程失败: {e}")
            return 1

    # ===== 数据预处理 (Jira 提取 / 视频复制 / ADAS 预处理) =====
    if args.data_preproc:
        from .data_preproc import data_preproc_main
        try:
            return data_preproc_main(max_workers=args.workers)
        except KeyboardInterrupt:
            print("\n[!] 已取消")
            return 1
        except Exception as e:
            print(f"[!] 数据预处理失败: {e}")
            return 1

    # ===== 生成列表回灌启动脚本 =====
    if args.gen_list_replay:
        try:
            gen_list_replay_interactive(out_dir=".")
        except KeyboardInterrupt:
            print("\n[!] 已取消")
            return 1
        except Exception as e:
            print(f"[!] 生成失败: {e}")
            return 1
        return 0

    if args.show_sources:
        srcs = config_sources()
        print("配置搜索路径(后者覆盖前者同 name 条目):")
        for key, path in srcs.items():
            tag = "  (存在)" if path.exists() else ""
            print(f"  {key:8s}  {path}{tag}")
        return 0

    # ===== 使用状态检测 =====
    if args.usage_all:
        if not boards:
            print("[!] 没有已配置开发板")
            return 1
        return _usage_all(boards, list(boards.keys()), args.workers)

    if args.usage:
        return _usage_all(boards, args.usage, args.workers)

    # ===== 批量连通性测试 =====
    if args.test_all:
        if not boards:
            print("[!] 没有已配置开发板")
            return 1
        names = list(boards.keys())
        print(f"[*] 并发测试 {len(names)} 块开发板连通性 (workers={args.workers}) ...")
        results = probe_boards(boards, names, max_workers=args.workers)
        print()
        print_probe_results(results)
        return 0 if all(r.ok for r in results) else 1

    if args.test:
        results = probe_boards(boards, args.test, max_workers=args.workers)
        print_probe_results(results)
        return 0 if all(r.ok for r in results) else 1

    if args.list or not args.board:
        _print_boards(boards)
        if not args.board:
            print("\n用法: python run.py <board | --usage-all | --test-all | ...>")
            print("提示: --usage-all 检测使用状态, --test-all 测连通性, --show-sources 看配置路径")
        return 0

    if args.board not in boards:
        print(f"[!] 未知开发板: {args.board}")
        if boards:
            print("    可选: " + " / ".join(boards))
        else:
            print("    没有已配置开发板,请用 --show-sources 查看配置文件路径")
        return 1

    cfg = boards[args.board]
    pwd = cfg.get("password", "")
    if not pwd or pwd == "YOUR_PASSWORD_HERE":
        print(
            f"[!] 开发板 '{args.board}' 密码未设置。"
            f" 请通过以下方式之一填入:\n"
            f"    1) 编辑源码: devboard_toolkit/config.py -> DEFAULT_BOARDS\n"
            f"    2) 写入用户级配置: ~/.devboard_toolkit/config.json\n"
            f"    3) 写入项目级配置: ./config.json\n"
            f"  用 --show-sources 查看路径。"
        )
        return 1

    host = cfg.get("host")
    port = cfg.get("port", 22)
    user = cfg.get("user")
    print(f"[*] 连接 {args.board} ({user}@{host}:{port}) ...")
    try:
        client = build_client(cfg)
    except Exception as e:
        print(f"[!] 连接失败: {e}")
        return 1
    print("[+] 已连接")

    try:
        if args.check:
            check_board(client)
        elif args.cmd:
            out, err, code = run_cmd(client, args.cmd)
            if out:
                print(out)
            if err:
                print("[stderr]", err, file=sys.stderr)
            return code
        else:
            # 默认交互式 shell
            chan = client.invoke_shell()
            try:
                interactive_shell(chan)
            finally:
                chan.close()
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
