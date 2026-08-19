r"""批量回灌: 检测空板 + 选回灌文件夹 + 均分txt + 生成脚本 + 多终端启动

完整流程(6 步):
1. 并发检测 6 块开发板使用状态 → 得到 N 块空闲板
2. 扫描回灌环境 testbed 下的子文件夹,让用户选(如 list / test)
3. 输入回灌参数: 素材txt路径 / 感知包 / 用户 / 日期 / 车型(N个脚本共用)
4. 均分素材txt为N份 → 子txt写到回灌目录UNC路径
5. 生成N个脚本 → 写到回灌目录UNC路径
6. 开 N 个独立 cmd 终端,各自 SSH 连一块空闲板: mount → cd → 启动脚本

输出文件统一写到: \\windows_host\share\...\testbed\<选择的回灌文件夹>\
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from .config import (
    load_boards, load_car_models, load_replay_list_template,
    load_replay_sdk_template, load_replay_env,
)
from .usage_check import check_usage_one
from .script_gen import (
    _extract_suffix, _prompt, _select_car_model, _render_template,
)


# ============================================================
# 路径/配置相关
# ============================================================

def _normalize_path(s: str) -> str:
    """规范化用户输入的路径(UNC/本地/带引号)"""
    s = s.strip().strip('"').strip("'").strip()
    if not s:
        return s
    return os.path.normpath(s)


def unc_to_board_path(windows_path: str, mount_source: str, mount_point: str) -> str:
    """将 Windows UNC 脚本路径转换为开发板上的路径

    例:
        windows_path = \\\\hz-iotfs02\\Model_Test\\...\\start.sh
        mount_source = //172.17.12.118/Model_Test/TestSpace/Personal_Space
        mount_point = /mnt
        返回: /mnt/SYC/testbed/.../start.sh
    """
    if "/" in mount_source:
        share_part = mount_source.split("/", 3)[-1] if mount_source.count("/") >= 3 else ""
    else:
        share_part = mount_source
    share_part_win = share_part.replace("/", "\\")
    norm_win = windows_path.replace("/", "\\")
    idx = norm_win.lower().find(share_part_win.lower())
    if idx >= 0:
        relative = norm_win[idx + len(share_part_win):]
    else:
        relative = "\\" + os.path.basename(windows_path)
    relative = relative.replace("\\", "/")
    if not relative.startswith("/"):
        relative = "/" + relative
    return mount_point.rstrip("/") + relative


def _derive_paths(env: dict) -> Tuple[str, str]:
    """基于 replay_env 推导两条关键路径

    Returns:
        (windows_testbed_unc, linux_testbed_full)
        - windows_testbed_unc: Windows 端访问 testbed 的 UNC 路径
          例: \\\\hz-iotfs02\\Model_Test\\TestSpace\\Personal_Space\\SYC\\testbed
        - linux_testbed_base: 开发板端 testbed 路径前缀(不含回灌子文件夹)
          例: /mnt/SYC/testbed
    """
    src = env["mount_source"]  # 例: //172.17.12.118/Model_Test/TestSpace/Personal_Space
    mount_point = env["mount_point"].rstrip("/")  # /mnt
    testbed = env["testbed_subpath"].strip("/")  # SYC/testbed
    host = env["windows_host"]  # hz-iotfs02

    # share_part: 去掉 //IP/ 前缀, 例: Model_Test/TestSpace/Personal_Space
    parts = src.replace("\\", "/").lstrip("/").split("/")
    # parts[0] 是 IP(hostname), 从 [1:] 开始是 share 路径
    if len(parts) < 2:
        share_part = ""
    else:
        share_part = "/".join(parts[1:])

    # Windows UNC: \\host\share_part\SYC\testbed
    windows_testbed_unc = os.path.normpath(
        f"\\\\{host}\\{share_part}\\{testbed}"
    )

    # Linux 端: /mnt/SYC/testbed
    linux_testbed_base = f"{mount_point}/{testbed}"

    return windows_testbed_unc, linux_testbed_base


def _list_folders(unc_testbed: str) -> List[str]:
    """列出 testbed UNC 路径下的所有子文件夹(回灌文件夹)

    排除以 . 开头的隐藏目录
    """
    p = Path(unc_testbed)
    if not p.exists():
        raise FileNotFoundError(
            f"无法访问回灌环境 testbed 路径: {unc_testbed}\n"
            f"  请检查:\n"
            f"    1. 本电脑能否直接在资源管理器打开该路径\n"
            f"    2. config.yaml 中 replay_env.windows_host / mount_source / testbed_subpath 是否正确"
        )
    folders = sorted(
        [d.name for d in p.iterdir()
         if d.is_dir() and not d.name.startswith(".")]
    )
    return folders


def _select_folder(folders: List[str]) -> str:
    """交互式选择回灌文件夹"""
    print("\n[2/6] 选择回灌文件夹(在 testbed 下,输入序号):")
    if not folders:
        print("  (testbed 下没有子文件夹,请先手动创建)")
        return ""
    for i, f in enumerate(folders, 1):
        print(f"  {i:2d}) {f}")
    # 也支持直接输入新文件夹名(手动输入不存在的先询问是否创建)
    while True:
        sel = input("> ").strip()
        try:
            idx = int(sel)
            if 1 <= idx <= len(folders):
                return folders[idx - 1]
            print(f"  [!] 序号范围 1-{len(folders)}")
        except ValueError:
            if sel:
                # 直接输入了文件夹名(可以是新的)
                return sel
            print("  [!] 请输入序号或文件夹名")


def _ensure_folder(unc_folder: str) -> None:
    """确保回灌子文件夹存在(不存在则创建)"""
    p = Path(unc_folder)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)


# ============================================================
# 回灌环境检测与补全
# ============================================================

def _project_tool_dir() -> str:
    """定位项目根目录下的 tool/ 文件夹"""
    if getattr(sys, 'frozen', False):
        project_root = os.path.dirname(sys.executable)
    else:
        project_root = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "tool")


def _copy_tree_overwrite(src: str, dst: str) -> None:
    """整个覆盖:把 src 文件夹内容复制到 dst,已存在的全部覆盖"""
    src_p = Path(src)
    dst_p = Path(dst)
    dst_p.mkdir(parents=True, exist_ok=True)
    for item in src_p.iterdir():
        target = dst_p / item.name
        if item.is_dir():
            _copy_tree_overwrite(str(item), str(target))
        else:
            # 直接覆盖
            import shutil as _sh
            _sh.copy2(str(item), str(target))


def _select_fcf_version(tool_dir: str) -> str:
    """让用户选 fcf 标定版本,返回对应目录路径(默认 default)"""
    fcf_root = os.path.join(tool_dir, "fcf_calibration")
    if not os.path.isdir(fcf_root):
        print(f"[!] tool 目录下找不到 fcf_calibration: {fcf_root}")
        return ""
    versions = sorted([d for d in os.listdir(fcf_root)
                       if os.path.isdir(os.path.join(fcf_root, d))])
    if not versions:
        print(f"[!] fcf_calibration 目录为空: {fcf_root}")
        return ""
    # default 排第一
    if "default" in versions:
        versions.remove("default")
        versions.insert(0, "default")
    print("\n  选择 fcf 标定版本(输入序号,默认 1):")
    for i, v in enumerate(versions, 1):
        tag = " (通用默认)" if v == "default" else " (特定标定)"
        print(f"    {i}) {v}{tag}")
    sel = _prompt("  fcf 版本编号", "1")
    try:
        idx = int(sel)
        if 1 <= idx <= len(versions):
            chosen = versions[idx - 1]
        else:
            chosen = versions[0]
    except ValueError:
        chosen = versions[0]
    print(f"  → 已选: {chosen}")
    return os.path.join(fcf_root, chosen)


def _find_perception_pkgs(replay_dir: str) -> List[str]:
    """在回灌目录查找已存在的感知包(NH_ADAS_PERCEPTION_*)

    优先匹配目录,也兼容文件(压缩包形式)
    """
    matches = []
    if not os.path.exists(replay_dir):
        return matches
    try:
        for name in os.listdir(replay_dir):
            if name.startswith("NH_ADAS_PERCEPTION_"):
                full = os.path.join(replay_dir, name)
                # 目录优先;文件(如 .tar.gz)也算
                if os.path.isdir(full) or os.path.isfile(full):
                    matches.append(name)
    except Exception as e:
        print(f"  [!] 遍历回灌目录失败: {e}")
    matches.sort()
    return matches


def validate_replay_env(replay_dir: str, need_build: bool,
                        fcf_src_dir: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """检测回灌环境完整性,缺失则补全

    Args:
        replay_dir: 回灌目录 UNC 路径
        need_build: 是否使用自动编译
            - True: 只检测 lib + fcf(产物和 runtime 由编译流程补)
            - False: 检测 lib + fcf + runtime + 感知包
        fcf_src_dir: 非交互模式下 fcf 标定源目录(缺哪个版本直接传进来).
            None 时用原有交互选择 (兼容 CLI 全流程)

    Returns:
        (ok, app_name)
        - ok=True, app_name=感知包名(编译模式为空,由后续编译填)
        - ok=True, app_name=感知包名(跳过编译模式,已存在)
        - ok=False, app_name=None → 中止
    """
    tool_dir = _project_tool_dir()
    if not os.path.isdir(tool_dir):
        print(f"[!] 找不到 tool 目录: {tool_dir}")
        return (False, None)

    print(f"\n  检测回灌目录: {replay_dir}")
    _ensure_folder(replay_dir)

    # ---------- 1. lib 库文件夹 ----------
    lib_dst = os.path.join(replay_dir, "lib")
    lib_src = os.path.join(tool_dir, "lib")
    if not os.path.isdir(lib_src):
        print(f"  [!] tool/lib 不存在,无法补全: {lib_src}")
        return (False, None)

    # 检查 lib 是否完整:对比 tool/lib 的文件名集合
    src_files = {f for f in os.listdir(lib_src) if os.path.isfile(os.path.join(lib_src, f))}
    if os.path.isdir(lib_dst):
        dst_files = {f for f in os.listdir(lib_dst) if os.path.isfile(os.path.join(lib_dst, f))}
    else:
        dst_files = set()
    missing = src_files - dst_files
    if not missing and src_files:
        # 文件齐全,跳过
        print(f"  [✓] lib/ 完整({len(dst_files)} 个文件),跳过")
    else:
        print(f"  [!] lib/ 缺失 {len(missing)} 个文件,从 tool/lib 覆盖")
        try:
            _copy_tree_overwrite(lib_src, lib_dst)
            print(f"  [✓] lib/ 已补全")
        except Exception as e:
            print(f"  [!] lib/ 补全失败: {e}")
            return (False, None)

    # ---------- 2. fcf 标定 (方案 B: 直接覆盖, 不校验是否存在) ----------
    veh_dst = os.path.join(replay_dir, "vehConfig.json")
    vru_dst = os.path.join(replay_dir, "vruConfig.json")
    fcf_dir = fcf_src_dir  # 参数名和外层变量冲突,改用局部
    if fcf_dir and os.path.isdir(fcf_dir):
        print(f"  [i] fcf 标定: 使用外部指定的版本 → {os.path.basename(fcf_dir)}")
    else:
        fcf_dir = _select_fcf_version(tool_dir)
        if not fcf_dir:
            return (False, None)
    import shutil as _sh
    try:
        _sh.copy2(os.path.join(fcf_dir, "vehConfig.json"), veh_dst)
        _sh.copy2(os.path.join(fcf_dir, "vruConfig.json"), vru_dst)
        print(f"  [✓] fcf 标定已覆盖 (vehConfig.json + vruConfig.json)")
    except Exception as e:
        print(f"  [!] fcf 标定覆盖失败: {e}")
        return (False, None)

    # ---------- 3. (仅跳过编译时) runtime + 感知包 ----------
    app_name = ""
    if not need_build:
        # runtime 文件夹
        runtime_dst = os.path.join(replay_dir, "runtime")
        if os.path.isdir(runtime_dst) and len(os.listdir(runtime_dst)) > 0:
            print(f"  [✓] runtime/ 已存在")
        else:
            print(f"  [!] runtime/ 缺失,且无法从 tool 补全")
            print(f"      请手动放入 runtime,或改用自动编译模式")
            return (False, None)

        # 感知包目录 NH_ADAS_PERCEPTION_*
        pkgs = _find_perception_pkgs(replay_dir)
        if not pkgs:
            print(f"  [!] 未找到编译后的感知包 (NH_ADAS_PERCEPTION_*)")
            print(f"      请手动放入感知包,或改用自动编译模式")
            return (False, None)
        if len(pkgs) == 1:
            app_name = pkgs[0]
            print(f"  [✓] 感知包: {app_name}")
        else:
            print(f"  [!] 找到多个感知包,请选择:")
            for i, name in enumerate(pkgs, 1):
                print(f"    {i}) {name}")
            while True:
                sel = _prompt("  感知包编号")
                try:
                    idx = int(sel)
                    if 1 <= idx <= len(pkgs):
                        app_name = pkgs[idx - 1]
                        print(f"  → 已选: {app_name}")
                        break
                    print(f"  [!] 序号范围 1-{len(pkgs)}")
                except ValueError:
                    print("  [!] 请输入有效编号")
    else:
        # 编译模式:产物和 runtime 由编译流程补,这里只提示
        print(f"  [i] 自动编译模式:感知包和 runtime 将由编译流程补全")

    print(f"\n  [✓] 回灌环境检测完成")
    return (True, app_name)


# ============================================================
# 开发板检测 + 文件生成(沿用原逻辑)
# ============================================================

def _detect_idle_boards(boards: dict, workers: int = 6) -> List[str]:
    """并发检测使用状态,返回空闲板名列表(按配置顺序)"""
    names = list(boards.keys())
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(check_usage_one, n, boards[n]): n for n in names}
        for fut in as_completed(futs):
            results.append(fut.result())
    order = {n: i for i, n in enumerate(names)}
    results.sort(key=lambda r: order.get(r.name, 999))

    idle = [r.name for r in results if not r.busy]
    print("\n开发板使用状态:")
    print(f"  {'板名':<10s}{'地址':<18s}{'状态'}")
    print("  " + "-" * 50)
    for r in results:
        tag = "空闲" if not r.busy else "使用中"
        print(f"  {r.name:<10s}{r.host:<18s}{tag}")
    return idle


# 路径映射 (与 classify_by_car.py 保持一致, 反向: 板端 Linux 路径 → Windows UNC)
# 用于 _split_txt sort_by_size=True 时从板端路径反查得到 UNC 路径后调用 os.path.getsize
_UNC_TO_LINUX_MAP = {
    r"\\hz-iotfs02\Model_Test\TestSpace\Personal_Space": "/tmp/iot_test/mnt_data",
    r"\\Material\xuekangkang\download": "/tmp/iot_test/mnt_data",
    r"\\hz-iotfs02\Function_Test\Front_Camera": "/tmp/iot_test/mnt_data",
    r"\\Material\chz62985\download": "/tmp/iot_test/mnt_data",
}
_LINUX_TO_UNC_MAP = {v: k for k, v in _UNC_TO_LINUX_MAP.items()}


def _strip_quotes(p: str) -> str:
    """去掉路径两端可能的单/双引号 (txt 行内容有时被引号包裹)."""
    return p.strip().strip("'").strip('"')


def _to_unc_candidates(line_path: str) -> List[str]:
    """把素材行转换为「可能的 UNC 候选列表」(按优先级), 供 getsize 逐一尝试.

    规则:
      1. 先去两端引号
      2. 已是 UNC 格式 → 直接作为单候选
      3. 板端 /tmp/iot_test/mnt_data/...  (多个 UNC 共享同板端前缀)
         → 生成所有 UNC 前缀拼接相对路径的候选
      4. 普通板端 Linux 路径 (一对一映射) → 按 _LINUX_TO_UNC_MAP
    """
    raw = _strip_quotes(line_path)
    s = raw.replace("\\", "/")

    # a) 已是 UNC 格式 (反斜杠开头)
    for unc_pfx in _UNC_TO_LINUX_MAP.keys():
        if raw.startswith(unc_pfx):
            return [raw]
    # b) 已是 UNC 格式 (正斜杠归一化后匹配)
    for unc_pfx in _UNC_TO_LINUX_MAP.keys():
        up = unc_pfx.replace("\\", "/")
        if s.startswith(up):
            return [unc_pfx + s[len(up):]]

    # c) 板端 /tmp/iot_test/mnt_data/...  → 多 UNC 候选(所有共享都挂载到此)
    shared_pfx = "/tmp/iot_test/mnt_data/"
    if s.startswith(shared_pfx):
        relative = s[len(shared_pfx):]
        rel_fs = relative.replace("/", os.sep)
        cands = []
        for unc in _UNC_TO_LINUX_MAP.keys():
            cands.append(os.path.join(unc, rel_fs))
        return cands

    # d) 其他板端 Linux 前缀 (一对一映射)
    for linux_pfx, unc_pfx in _LINUX_TO_UNC_MAP.items():
        lp = linux_pfx.replace("\\", "/")
        if s.startswith(lp):
            tail = s[len(lp):].replace("/", os.sep)
            return [os.path.join(unc_pfx, tail)]

    # e) 不匹配任何映射, 当成本地路径原样返回
    return [raw]


def _to_unc_path(line_path: str) -> str:
    """保留旧 API: 返回首个候选 (调用方自行处理失败)."""
    cands = _to_unc_candidates(line_path)
    return cands[0] if cands else line_path


def _split_txt(txt_path: str, n: int, out_dir: str, sort_by_size: bool = False) -> List[str]:
    """读取 txt, 切分为 n 份, 输出到 out_dir(UNC 路径)下

    Args:
        txt_path: 源 txt 路径
        n: 目标分片数
        out_dir: 输出目录 (UNC)
        sort_by_size: True → 按 .h265/.h264 文件大小降序 LPT 轮询分配 (均衡回灌时长)
                      False → 原始顺序均分 (旧行为)

    Returns:
        生成的子 txt 文件名列表(仅文件名)
    """
    p = Path(txt_path)
    if not p.exists():
        raise FileNotFoundError(
            f"txt 文件不存在(或网络路径不可访问): {txt_path}\n"
            f"  提示: 若为网络路径 \\\\server\\share,请确认可访问"
        )

    with open(txt_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\r\n") for line in f if line.strip()]

    total = len(lines)
    if total == 0:
        raise ValueError("txt 文件内容为空")

    base = p.stem
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    # ---- sort_by_size: 按文件大小降序 LPT 轮询分配到 n 个分片 ----
    if sort_by_size:
        import os as _os
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _get_size(line: str) -> int:
            for cand in _to_unc_candidates(line):
                try:
                    return _os.path.getsize(cand)
                except Exception:
                    continue
            return 0

        # 并行获取所有素材文件大小 (UNC 路径, 网络 IO, 并行快; 每条多候选逐一尝试)
        sizes = [0] * total
        workers = min(32, max(1, total))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_get_size, lines[i]): i for i in range(total)}
            for fut in as_completed(futs):
                idx = futs[fut]
                try:
                    sizes[idx] = fut.result()
                except Exception:
                    sizes[idx] = 0

        total_size = sum(sizes)
        # 全 0 兜底: 所有 UNC 都访问不到 → 退化为原始顺序均分, 避免 LPT 堆全 0 都塞到第 1 片
        if total_size == 0:
            size = total // n
            remainder = total % n
            chunks = []
            start = 0
            for i in range(n):
                cnt = size + (1 if i < remainder else 0)
                chunks.append(lines[start:start + cnt])
                start += cnt
        else:
            # 按大小降序, 大小相同按原索引保序
            order = sorted(range(total), key=lambda i: (-sizes[i], i))

            # LPT 轮询: 按大小降序依次分配给"当前总大小最小"的分片 (最小堆)
            import heapq
            parts_sums = [0] * n   # 每个分片当前总字节
            parts_lines = [[] for _ in range(n)]
            heap = [(0, i) for i in range(n)]  # (sum, part_idx)
            heapq.heapify(heap)

            for idx in order:
                cur_size = sizes[idx]
                s, p_idx = heapq.heappop(heap)
                parts_lines[p_idx].append(lines[idx])
                parts_sums[p_idx] = s + cur_size
                heapq.heappush(heap, (parts_sums[p_idx], p_idx))

            chunks = parts_lines
    else:
        # ---- 旧行为: 原始顺序均分 ----
        size = total // n
        remainder = total % n
        chunks = []
        start = 0
        for i in range(n):
            cnt = size + (1 if i < remainder else 0)
            chunks.append(lines[start:start + cnt])
            start += cnt

    out_names = []
    for i in range(n):
        sub_name = f"{base}_{i + 1}.txt"
        sub_path = out_dir_path / sub_name
        with open(sub_path, "w", encoding="utf-8", newline="\n") as f:
            if chunks[i]:
                f.write("\n".join(chunks[i]) + "\n")
        out_names.append(sub_name)
    return out_names


def _gen_one_script(template: str, vars_map: dict, out_dir: str,
                    car_model: str, list_file: str, idx: int) -> str:
    """生成单个列表回灌脚本,写入 out_dir,返回文件全路径"""
    v = dict(vars_map)
    v["LIST_FILE"] = Path(list_file).stem
    content = _render_template(template, v)
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    filename = f"start_with_resume_{car_model}_{idx}.sh"
    safe = filename.replace("/", "_").replace(":", "_").replace(" ", "_")
    out_path = Path(out_dir) / safe
    out_path.write_text(content, encoding="utf-8", newline="\n")
    return str(out_path)


def _gen_sdk_scripts(template: str, vars_map: dict, out_dir: str,
                     car_model: str, input_subpath: str) -> str:
    """生成单个 SDK 回灌脚本(单板,不分片),返回文件全路径"""
    v = dict(vars_map)
    v["INPUT_SUBPATH"] = input_subpath
    content = _render_template(template, v)
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    filename = f"start_sdk_{car_model}_1.sh"
    safe = filename.replace("/", "_").replace(":", "_").replace(" ", "_")
    out_path = Path(out_dir) / safe
    out_path.write_text(content, encoding="utf-8", newline="\n")
    return str(out_path)


# ============================================================
# 多终端启动
# ============================================================

def _has_wt() -> bool:
    """检测系统是否可用 Windows Terminal (wt.exe)"""
    import shutil
    return shutil.which("wt") is not None


def _launch_terminals(project_root: str,
                      assignments: List[Tuple[str, str, str]],
                      log_dir: str = "",
                      app_suffix: str = "",
                      delete_script: bool = False,
                      no_reboot_boards: set = None) -> None:
    """为每个板开终端,运行 per_board_runner.py

    优先用 Windows Terminal 多标签页(一个窗口 N 个标签):
      第1个板: wt new-tab --title board1 python per_board_runner.py ...
      后续板:  wt -w 0 new-tab --title board2 python per_board_runner.py ...
    无 wt 时回退到 CREATE_NEW_CONSOLE(多窗口)

    Args:
        project_root: D:\\Desktop\\devboard-toolkit
        assignments: [(board_name, 回灌文件夹名, 脚本文件名), ...]
        log_dir: 日志保存目录(UNC路径),为空则不保存日志
        app_suffix: 感知包后缀,用于日志文件名
        delete_script: 回灌结束后自动删除脚本
        no_reboot_boards: 集合,板名在其中的会传 --no-reboot (接力跳过reboot)
    """
    # --- exe 打包适配: frozen 环境下用 per_board_runner.exe, 否则用 .py ---
    if getattr(sys, 'frozen', False):
        # PyInstaller exe 环境: runner 已编译为独立 exe
        runner_path = os.path.join(os.path.dirname(sys.executable), "per_board_runner.exe")
        python_exe = ""  # exe 直接运行, 不需要 python 前缀
    else:
        runner_path = os.path.join(project_root, "per_board_runner.py")
        python_exe = sys.executable
    boards = load_boards()
    no_reboot_boards = no_reboot_boards or set()

    print("\n[启动多终端]:")
    use_wt = _has_wt()
    if use_wt:
        print("  (Windows Terminal 多标签页模式)")
    else:
        print("  (未检测到 wt.exe,回退多窗口模式)")
    if log_dir:
        print(f"  (日志保存到: {log_dir})")
    if delete_script:
        print("  (回灌结束后自动删除脚本)")
    if no_reboot_boards:
        print(f"  (接力跳过 reboot: {', '.join(sorted(no_reboot_boards))})")

    # 公共参数
    extra_args = []
    if log_dir:
        extra_args += ["--log-dir", log_dir, "--app-suffix", app_suffix]
    if delete_script:
        extra_args += ["--delete-script"]

    def _board_args(board: str) -> list:
        """返回该板的特有参数 (--no-reboot 等)"""
        return ["--no-reboot"] if board in no_reboot_boards else []

    if use_wt:
        # 用单条 wt 命令 + ; 分隔符一次性创建所有标签页
        # 避免 -w 0 因第一个窗口未初始化而找不到目标窗口的问题
        wt_cmd = ["wt"]
        for i, (board, folder, script_name) in enumerate(assignments):
            host = boards.get(board, {}).get("host", board)
            tab_title = f"{host} {script_name}"
            skip_tag = " [skip-reboot]" if board in no_reboot_boards else ""
            print(f"  {i+1}. {board:<8s} {host:<16s} → {script_name}{skip_tag}")
            if i > 0:
                wt_cmd.append(";")
            wt_cmd.extend(["new-tab", "--title", tab_title])
            if python_exe:
                wt_cmd.append(python_exe)
            wt_cmd.extend([runner_path, board, folder, script_name])
            wt_cmd.extend(extra_args)
            wt_cmd.extend(_board_args(board))
        subprocess.Popen(wt_cmd, cwd=project_root)
    else:
        for i, (board, folder, script_name) in enumerate(assignments, 1):
            host = boards.get(board, {}).get("host", board)
            skip_tag = " [skip-reboot]" if board in no_reboot_boards else ""
            print(f"  {i}. {board:<8s} {host:<16s} → {script_name}{skip_tag}")
            cmd = []
            if python_exe:
                cmd.append(python_exe)
            cmd += [runner_path, board, folder, script_name]
            cmd += extra_args + _board_args(board)
            subprocess.Popen(
                cmd,
                cwd=project_root,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )


# ============================================================
# 主流程
# ============================================================

def batch_replay_main(pre_filled_app: str = None) -> int:
    """批量回灌主流程入口(6 步)

    Args:
        pre_filled_app: 自动编译后传来的感知包名,跳过步骤 [3/6] 的感知包输入
    """
    boards = load_boards()
    if not boards:
        print("[!] 没有已配置开发板")
        return 1

    car_models = load_car_models()
    template = load_replay_list_template()
    env = load_replay_env()
    if not template:
        raise RuntimeError("config.yaml 中未找到 replay_list_template")
    if not car_models:
        raise RuntimeError("config.yaml 中未找到 car_models")

    # 推导路径
    unc_testbed, _linux_testbed = _derive_paths(env)

    print("=" * 60)
    print("  批量回灌: 检测空板 + 选回灌文件夹 + 均分txt + 多终端启动")
    print("=" * 60)
    print(f"  testbed(UNC): {unc_testbed}")

    # === [1/6] 检测空板 ===
    print("\n[1/6] 检测开发板使用状态...")
    idle_boards = _detect_idle_boards(boards)
    if not idle_boards:
        print("\n[!] 没有空闲开发板,无法启动批量回灌")
        return 1
    n = len(idle_boards)
    print(f"\n[+] 检测到 {n} 块空闲板: {', '.join(idle_boards)}")

    # === [2/6] 选回灌文件夹 ===
    print("\n[2/6] 检索回灌文件夹...")
    try:
        folders = _list_folders(unc_testbed)
    except FileNotFoundError as e:
        print(f"[!] {e}")
        return 1
    replay_folder = _select_folder(folders)
    if not replay_folder:
        print("[!] 未选择回灌文件夹,已取消")
        return 1
    # 完整回灌目录
    unc_replay_folder = os.path.normpath(os.path.join(unc_testbed, replay_folder))
    _ensure_folder(unc_replay_folder)
    print(f"  → 回灌目录: {unc_replay_folder}")
    # 板侧目录: /mnt/SYC/testbed/<replay_folder>
    linux_replay_folder = f"{_linux_testbed}/{replay_folder}"

    # === [3/6] 用户输入 ===
    print(f"\n[3/6] 输入回灌参数(将均分为 {n} 份,共用感知包/用户/日期/车型):")
    txt_path = _prompt(
        "  素材清单 txt 文件路径(支持 \\\\server\\share 或 D:\\path 格式)"
    )
    txt_path = _normalize_path(txt_path)
    if pre_filled_app:
        app_path = pre_filled_app
        print(f"  感知包名称: {app_path}  (来自自动编译,已自动填入)")
    else:
        app_path = _prompt(
            "  感知包名称(如 NH_ADAS_PERCEPTION_V3.1.3_8M_3.1.27223.2251)"
        )
    suffix = _extract_suffix(app_path)
    print(f"      → 自动提取后缀: {suffix}")
    user = _prompt("  用户名(如 syc53636)")
    date = _prompt("  日期(如 20260811)")
    print("\n  选择车型:")
    car_model, calibration = _select_car_model(car_models)
    print(f"  → 已选: 车型={car_model} / 标定={calibration}")

    # === [4/6] 均分 txt(写到回灌目录, 按文件大小 LPT 均衡) ===
    print(f"\n[4/6] 均分 txt 为 {n} 份 + 按文件大小 LPT 均衡, 保存到回灌目录...")
    sub_files = _split_txt(txt_path, n, out_dir=unc_replay_folder, sort_by_size=True)
    print(f"[+] 已生成 {len(sub_files)} 个子 txt:")
    for i, sf in enumerate(sub_files, 1):
        full = os.path.join(unc_replay_folder, sf)
        line_cnt = sum(1 for _ in open(full, encoding="utf-8") if _.strip())
        print(f"    {i}. {sf}  ({line_cnt} 条)")

    # === [5/6] 生成 N 个脚本(写到回灌目录) ===
    print(f"\n[5/6] 生成 {n} 个启动脚本,保存到回灌目录...")
    vars_map = {
        "APP_PATH": app_path,
        "APP_SUFFIX": suffix,
        "USER": user,
        "DATE": date,
        "CAR_MODEL": car_model,
        "CALIBRATION": calibration,
    }
    scripts = []
    for i, sf in enumerate(sub_files, 1):
        path = _gen_one_script(
            template, vars_map, unc_replay_folder, car_model, sf, i
        )
        scripts.append(path)
        print(f"    {i}. {Path(path).name}  ←  {sf}")

    # === [6/6] 多终端启动 ===
    print(f"\n[6/6] 为 {n} 块空闲板分别打开独立终端,启动回灌脚本...")
    assignments: List[Tuple[str, str, str]] = []
    for i, board in enumerate(idle_boards):
        script_name = Path(scripts[i]).name
        assignments.append((board, replay_folder, script_name))
    project_root = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(unc_replay_folder, "logs")
    _launch_terminals(project_root, assignments, log_dir=log_dir, app_suffix=suffix)

    # === 汇总 ===
    print("\n" + "=" * 60)
    print("  批量回灌已启动")
    print("=" * 60)
    print(f"  空闲板  : {n} 块 → {', '.join(idle_boards)}")
    print(f"  回灌目录: {linux_replay_folder}  (开发板端)")
    print(f"  UNC路径 : {unc_replay_folder}  (Windows 端)")
    print(f"  日志目录: {log_dir}")
    print(f"  感知包  : {app_path}")
    print(f"  后缀    : {suffix}")
    print(f"  用户    : {user}")
    print(f"  日期    : {date}")
    print(f"  车型    : {car_model} / {calibration}")
    print(f"  子 txt  : {len(sub_files)} 个")
    print(f"  脚本    : {len(scripts)} 个")
    print()
    print("  每块板对应一个终端窗口,窗口内执行:")
    print(f"    1) mount -t cifs ...  (挂载共享)")
    print(f"    2) cd {linux_replay_folder}  (进入回灌目录)")
    print(f"    3) chmod +x 脚本; ./脚本.sh  (运行回灌)")
    print()
    print("  查看进度: 在各终端窗口观察输出")
    print("  板子是否跑完: python run.py --usage-all  (板子变空闲=跑完)")
    print("=" * 60)
    return 0


# ============================================================
# 全流程: 自动编译 + 自动回灌
# ============================================================

def full_auto_main() -> int:
    """全流程自动化: 选回灌目录 → 环境检测 → (可选)自动编译 → 自动回灌

    流程 (7 步 + 环境检测):
      1. 并发检测开发板 → 选择使用几块(默认全部)
      2. 选回灌文件夹 → 得到完整 UNC 路径(回灌环境网络地址)
      3. 询问是否需要自动编译
      3.5 回灌环境检测与补全 (lib + fcf + 可选 runtime/感知包)
      4. 一次性输入所有参数 (zip路径或包名 + txt + 用户 + 日期 + 车型)
      5. (若编译) 自动编译: 产物+runtime 放到回灌目录, 得到感知包名
      6. 均分 txt + 生成脚本
      7. 多终端启动

    Returns:
        0 成功 / 1 失败
    """
    boards = load_boards()
    if not boards:
        print("[!] 没有已配置开发板")
        return 1

    car_models = load_car_models()
    list_template = load_replay_list_template()
    sdk_template = load_replay_sdk_template()
    env = load_replay_env()
    if not list_template:
        raise RuntimeError("config.yaml 中未找到 replay_list_template")
    if not sdk_template:
        raise RuntimeError("config.yaml 中未找到 replay_sdk_template")
    if not car_models:
        raise RuntimeError("config.yaml 中未找到 car_models")

    # 推导路径
    unc_testbed, _linux_testbed = _derive_paths(env)

    print("=" * 60)
    print("  全流程自动化: (可选)自动编译 + 自动回灌")
    print("=" * 60)
    print(f"  testbed(UNC): {unc_testbed}")

    # === [0/7] 选择回灌模式 ===
    print("\n[0/7] 选择回灌模式:")
    print("  1) 列表回灌 (list)   → 多板均分 txt,每块板跑不同片段")
    print("  2) SDK回灌  (sdk)    → 单板跑,素材路径手动输入")
    mode_input = _prompt("  模式编号", "1")
    if mode_input == "2":
        replay_mode = "sdk"
    else:
        replay_mode = "list"
    print(f"  → 模式: {'列表回灌' if replay_mode == 'list' else 'SDK回灌'}")

    # 根据模式选模板
    template = list_template if replay_mode == "list" else sdk_template

    # === [1/7] 检测空板 + 选择使用几块(list模式) / SDK自动取第1块 ===
    print("\n[1/7] 检测开发板使用状态...")
    idle_boards = _detect_idle_boards(boards)
    if not idle_boards:
        print("\n[!] 没有空闲开发板,无法启动")
        return 1
    total_idle = len(idle_boards)
    print(f"\n[+] 检测到 {total_idle} 块空闲板: {', '.join(idle_boards)}")

    if replay_mode == "sdk":
        # SDK模式:自动取第1块空闲板,不询问
        idle_boards = idle_boards[:1]
        n = 1
        host = boards.get(idle_boards[0], {}).get("host", idle_boards[0])
        print(f"  SDK模式: 自动选用 {idle_boards[0]} ({host})")
    else:
        # list模式:选择使用几块板(默认全部)
        use_count_input = _prompt(
            f"  使用几块板(1-{total_idle}, 默认全部 {total_idle})",
            str(total_idle),
        )
        try:
            use_count = int(use_count_input)
            use_count = max(1, min(use_count, total_idle))
        except ValueError:
            use_count = total_idle
        idle_boards = idle_boards[:use_count]
        n = len(idle_boards)
        print(f"  → 将使用 {n} 块板: {', '.join(idle_boards)}")

    # === [2/7] 选回灌文件夹 ===
    print("\n[2/7] 检索回灌文件夹...")
    try:
        folders = _list_folders(unc_testbed)
    except FileNotFoundError as e:
        print(f"[!] {e}")
        return 1
    replay_folder = _select_folder(folders)
    if not replay_folder:
        print("[!] 未选择回灌文件夹,已取消")
        return 1
    unc_replay_folder = os.path.normpath(os.path.join(unc_testbed, replay_folder))
    _ensure_folder(unc_replay_folder)
    print(f"  → 回灌目录(完整网络地址): {unc_replay_folder}")

    # === [3/7] 是否需要自动编译 ===
    print("\n[3/7] 是否需要自动编译? [Y/n]")
    print("      Y = 编译感知包(产物+runtime 放到回灌目录)")
    print("      n = 回灌目录已有完整环境,跳过编译")
    try:
        need_build_input = input("> ").strip().lower()
    except EOFError:
        need_build_input = ""
    need_build = need_build_input in ("", "y", "yes")

    # === [3.5/7] 回灌环境检测与补全 ===
    print(f"\n[3.5/7] 回灌环境检测与补全...")
    env_ok, detected_app = _check_replay_env(unc_replay_folder, need_build)
    if not env_ok:
        print("[!] 回灌环境不完整,已中止")
        return 1

    # === [4/7] 一次性输入所有参数 ===
    print(f"\n[4/7] 一次性输入所有参数:")
    if need_build:
        mode_tag = "编译"
        print(f"  ({'SDK回灌-' if replay_mode=='sdk' else '列表回灌-'}编译模式: 输入感知包 zip 路径,编译后自动得到感知包名)")
        zip_path = _prompt("  感知包 zip 路径(支持类型A:含ARCSOFT+SDK / 类型B:外层包)")
        zip_path = _normalize_path(zip_path)
        app_path = ""  # 编译后才有
    else:
        print(f"  ({'SDK回灌-' if replay_mode=='sdk' else '列表回灌-'}跳过编译: 使用环境检测中找到的感知包)")
        app_path = detected_app
        print(f"  感知包名称: {app_path}  (来自环境检测)")

    # 素材路径: list模式=外部txt路径, sdk模式=input/{{USER}}/后面的相对路径
    input_subpath = ""
    if replay_mode == "list":
        txt_path = _prompt("  素材清单 txt 文件路径(支持 \\\\server\\share 或 D:\\path 格式)")
        txt_path = _normalize_path(txt_path)
    else:
        txt_path = ""  # SDK模式不需要外部txt
        print("  SDK回灌: 请输入 input/{{USER}}/ 之后的素材相对路径")
        print("          例: 20260810/0452")
        input_subpath = _prompt("  素材相对路径").replace("\\", "/").strip("/")

    user = _prompt("  用户名(如 syc53636)")
    # 日期自动填今天(格式 YYYYMMDD),可手动改
    today_date = time.strftime("%Y%m%d", time.localtime())
    date = _prompt("  日期", today_date)

    # 车型: 复用 _select_car_model(支持序号+车型名)
    car_model, calibration = _select_car_model(car_models)

    print(f"\n  → 参数确认:")
    print(f"    需要编译: {'是' if need_build else '否'}")
    if need_build:
        print(f"    zip 路径: {zip_path}")
    else:
        print(f"    感知包名: {app_path}")
    print(f"    txt 路径: {txt_path}")
    print(f"    用户名  : {user}")
    print(f"    日期    : {date}")
    print(f"    车型    : {car_model} / {calibration}")
    print(f"    板数    : {n} 块")

    # === [5/7] 自动编译(若需要) ===
    if need_build:
        print(f"\n[5/7] 启动 Jenkins 自动编译 (产物和 runtime 将放到回灌目录)...")
        project_root = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        try:
            from jenkins_build import auto_build_main
        except Exception as e:
            print(f"[!] 加载 jenkins_build 模块失败: {e}")
            return 1

        app_name, rc = auto_build_main(
            replay_dir=unc_replay_folder, sdk_zip_path=zip_path
        )
        if rc != 0 or not app_name:
            print(f"[!] 自动编译失败 (rc={rc}, app_name={app_name}),已中止")
            return 1
        app_path = app_name
        print(f"\n[✓] 自动编译完成,感知包名: {app_path}")
    else:
        print(f"\n[5/7] 跳过自动编译(使用回灌目录中已有的感知包)")

    # === [6/7] 均分 txt(list) / 生成脚本(sdk) ===
    linux_replay_folder = f"{_linux_testbed}/{replay_folder}"
    suffix = _extract_suffix(app_path)
    vars_map = {
        "APP_PATH": app_path,
        "APP_SUFFIX": suffix,
        "USER": user,
        "DATE": date,
        "CAR_MODEL": car_model,
        "CALIBRATION": calibration,
    }

    if replay_mode == "list":
        print(f"\n[6/7] 均分 txt 为 {n} 份(按文件大小 LPT 均衡) + 生成 {n} 个启动脚本...")
        print(f"      感知包名: {app_path}")
        print(f"      后缀    : {suffix}")

        sub_files = _split_txt(txt_path, n, out_dir=unc_replay_folder, sort_by_size=True)
        print(f"[+] 已生成 {len(sub_files)} 个子 txt:")
        for i, sf in enumerate(sub_files, 1):
            full = os.path.join(unc_replay_folder, sf)
            line_cnt = sum(1 for _ in open(full, encoding="utf-8") if _.strip())
            print(f"    {i}. {sf}  ({line_cnt} 条)")

        scripts = []
        for i, sf in enumerate(sub_files, 1):
            path = _gen_one_script(
                template, vars_map, unc_replay_folder, car_model, sf, i
            )
            scripts.append(path)
            print(f"    {i}. {Path(path).name}  ←  {sf}")
    else:
        # SDK模式: 不分片,直接生成1个脚本
        print(f"\n[6/7] 生成 SDK 回灌脚本(单板, 不分片)...")
        print(f"      感知包名 : {app_path}")
        print(f"      后缀     : {suffix}")
        print(f"      素材路径 : $PWD/input/{user}/{input_subpath}")
        scripts = []
        path = _gen_sdk_scripts(
            template, vars_map, unc_replay_folder, car_model, input_subpath
        )
        scripts.append(path)
        print(f"    1. {Path(path).name}")

    # === [7/7] 多终端启动 ===
    print(f"\n[7/7] 为 {n} 块空闲板分别打开独立终端,启动回灌脚本...")
    assignments: List[Tuple[str, str, str]] = []
    for i, board in enumerate(idle_boards):
        script_name = Path(scripts[i]).name
        assignments.append((board, replay_folder, script_name))
    project_root = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(unc_replay_folder, "logs")
    _launch_terminals(project_root, assignments, log_dir=log_dir, app_suffix=suffix)

    # === 汇总 ===
    print("\n" + "=" * 60)
    print("  全流程已完成")
    print("=" * 60)
    print(f"  回灌模式: {'列表回灌' if replay_mode == 'list' else 'SDK回灌'}")
    print(f"  编译    : {'是' if need_build else '否'}")
    print(f"  空闲板  : {n} 块 → {', '.join(idle_boards)}")
    print(f"  回灌目录: {linux_replay_folder}  (开发板端)")
    print(f"  UNC路径 : {unc_replay_folder}  (Windows 端)")
    print(f"  日志目录: {log_dir}")
    print(f"  感知包  : {app_path}")
    if need_build:
        print(f"  runtime : {os.path.join(unc_replay_folder, 'runtime')}")
    print(f"  后缀    : {suffix}")
    print(f"  用户    : {user}")
    print(f"  日期    : {date}")
    print(f"  车型    : {car_model} / {calibration}")
    if replay_mode == "list":
        print(f"  子 txt  : {len(sub_files)} 个")
    else:
        print(f"  素材路径: $PWD/input/{user}/{input_subpath}")
    print(f"  脚本    : {len(scripts)} 个")
    print()
    print("  查看进度: 在各终端窗口观察输出")
    print("  板子是否跑完: python run.py --usage-all  (板子变空闲=跑完)")
    print("=" * 60)
    return 0
