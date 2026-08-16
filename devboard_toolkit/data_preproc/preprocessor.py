"""ADAS 数据预处理 (并行)

调用外部 ADAS_Visualization.exe 对目录下的 .h265/.h264 视频做预处理。

命令行参数:
  -p  预处理视频路径
  -v  车型 (0-9)
  -m  是否生成mcap文件 (True/False)

特点:
- 按目录级别并行 (每个目录一个 ADAS 实例)
- 按文件级别统计进度
- ADAS 工具不存在时静默跳过 (不报错,允许只做复制不预处理)
"""

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Dict, Optional

from ..config import load_adas


def _collect_video_files(data_dir: str) -> Dict[str, List[str]]:
    """递归收集目录下所有 .h265/.h264 文件,按父目录分组

    Returns:
        {父目录: [视频文件绝对路径, ...]}
    """
    file_groups: Dict[str, List[str]] = {}
    for root, _dirs, files in os.walk(data_dir):
        for f in files:
            if f.lower().endswith((".h265", ".h264")):
                file_path = os.path.join(root, f)
                parent_dir = os.path.dirname(root)
                file_groups.setdefault(parent_dir, []).append(file_path)
    return file_groups


def _run_single_adas(exe_path: str, data_dir: str, car_type: int,
                     generate_mcap: bool, timeout: int) -> Tuple[str, bool, str]:
    """运行单个 ADAS 实例预处理一个目录

    Returns:
        (目录, 是否成功, 消息)
    """
    if not os.path.exists(exe_path):
        return data_dir, False, "ADAS 工具不存在"
    if not os.path.exists(data_dir):
        return data_dir, False, "目录不存在"

    command = f'"{exe_path}" -p "{data_dir}" -v {car_type} -m {generate_mcap}'
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            return data_dir, True, "完成"
        return data_dir, False, (result.stderr or "未知错误")[:200]
    except subprocess.TimeoutExpired:
        return data_dir, False, "超时"
    except Exception as e:
        return data_dir, False, str(e)[:200]


def run_preprocessing(data_dir: str, car_type: int = 3,
                      generate_mcap: bool = False,
                      stop_event: Optional[threading.Event] = None) -> Tuple[bool, str]:
    """并行运行 ADAS 预处理

    Args:
        data_dir: 待预处理的数据根目录
        car_type: 车型 (0-9)
        generate_mcap: 是否生成 mcap 文件
        stop_event: 取消事件信号

    Returns:
        (是否全部成功, 消息)
    """
    adas_cfg = load_adas()
    exe_path = adas_cfg["exe_path"]
    timeout = adas_cfg["timeout"]
    max_workers = adas_cfg["max_workers"]

    if not os.path.exists(exe_path):
        # ADAS 工具不存在,静默跳过
        return True, "ADAS 工具不存在,跳过预处理"

    file_groups = _collect_video_files(data_dir)
    if not file_groups:
        return True, "无视频文件需要预处理"

    dirs_to_process = list(file_groups.keys())
    total_dirs = len(dirs_to_process)
    total_files = sum(len(files) for files in file_groups.values())
    completed_files = 0
    all_success = True
    errors: List[str] = []
    cancelled = False

    print(f"\n[*] 并行 ADAS 预处理: {total_dirs} 个目录 / {total_files} 个文件 (workers={max_workers})")
    print(f"    车型={car_type}, 生成mcap={generate_mcap}")

    with ThreadPoolExecutor(max_workers=min(total_dirs, max_workers)) as executor:
        futures = {
            executor.submit(_run_single_adas, exe_path, d, car_type,
                            generate_mcap, timeout): d
            for d in dirs_to_process
        }
        for future in as_completed(futures):
            if stop_event is not None and stop_event.is_set():
                print(f"\n[!] 用户请求取消,终止剩余 ADAS 预处理任务")
                executor.shutdown(wait=False, cancel_futures=True)
                cancelled = True
                break
            dir_path, ok, msg = future.result()
            file_count = len(file_groups.get(dir_path, []))
            completed_files += file_count

            if not ok:
                all_success = False
                errors.append(f"{os.path.basename(dir_path)}: {msg}")

            print(f"  [{completed_files}/{total_files}] {os.path.basename(dir_path)} "
                  f"({'OK' if ok else 'FAIL: ' + msg})")

    if cancelled:
        return False, f"已取消 (已完成 {completed_files}/{total_files} 文件)"
    if all_success:
        return True, f"并行预处理完成,共处理 {total_files} 个文件"
    return False, f"部分失败 ({len(errors)}/{total_dirs}): {'; '.join(errors[:3])}"
