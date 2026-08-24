"""区分车型 (用于列表回灌 - 视频路径输入)

功能:
  1. 遍历给定源路径, 找到所有 .h264/.h265 视频
  2. 按文件名去重 (同名选最佳路径: 不带 _bu 优先)
  3. 根据路径关键词分类车型
  4. 每个车型输出一个 {{USER}}_{car}.txt (包含板端相对路径)

并行: 用 ThreadPoolExecutor 并行处理每个文件的分类
"""

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple


VIDEO_SUFFIX = (".h264", ".h265")


# ============================================================
# 路径映射 (共享 UNC 路径 → 板端 Linux 挂载路径)
# 与 D:\Desktop\Test_script\data_pre-processing\区分车型.py 保持一致
# 多个共享源都挂载到板端 /tmp/iot_test/mnt_data
# ============================================================
PATH_MAPPING = {
    r"\\hz-iotfs02\Model_Test\TestSpace\Personal_Space": "/tmp/iot_test/mnt_data",
    r"\\Material\xuekangkang\download": "/tmp/iot_test/mnt_data",
    r"\\hz-iotfs02\Function_Test\Front_Camera": "/tmp/iot_test/mnt_data",
    r"\\Material\chz62985\download": "/tmp/iot_test/mnt_data",
}


def normalize_path(p: str) -> str:
    for src, dst in PATH_MAPPING.items():
        if src in p.replace("/", "\\"):
            p = p.replace("/", "\\").replace(src, dst)
            break
    return p.replace("\\", "/")


# ============================================================
# 车型关键词 (路径中包含 key 即归为 car, 先排 long key → short key, 防止短词抢命中)
# 从 config.yaml 的 car_keywords 加载, 硬编码作为 fallback
# ============================================================
_DEFAULT_CAR_KEYWORDS: Dict[str, str] = {
    "lixiang3": "lixiang3", "lixiang2": "lixiang2", "lixiang1": "lixiang1",
    "lx3": "lixiang3", "lx2": "lixiang2", "lx1": "lixiang1",
    "lixinag1": "lixiang1", "lixinag2": "lixiang2",

    "natie3": "natie3", "natie2": "natie2",
    "nt3": "natie3", "nt2": "natie2",
    "none": "5013",

    "Wuling_5577": "wuling_5577", "Wuling_5741": "wuling_5741",
    "wuling_5577": "wuling_5577", "wuling_5741": "wuling_5741",
    "Wuling_0240": "wuling_0240", "wuling_0240": "wuling_0240",
    "wuling_5436": "wuling_5436", "wuling_3545": "wuling_3545",
    "wuling_3554": "wuling_3554", "wuling": "wuling",

    "lynkco": "lynkco", "lyncko": "lynkco",

    "Geely_2239": "geely_2239", "geely_2239": "geely_2239",
    "Geely_2506": "geely_2506", "geely_2506": "geely_2506",
    "Geely_0452": "geely_0452", "ss21_0452": "geely_0452",
    "geely_0452": "geely_0452", "geely_463": "geely_463",
    "geely_0463": "geely_0463", "geely_508": "geely_508",
    "geely_1604": "geely_1604", "geely_0508": "geely_0508",
    "geely_5463": "geely_5463", "geely_3637": "geely_3637",

    "gl8": "GL8", "GL8": "gl8",
    "hq": "HQ", "hongqi": "HQ",
    "bl": "BL", "BL": "BL",

    "2239": "geely_2239",
    "2506": "geely_2506",
    "0452": "geely_0452", "463": "geely_463",
    "0463": "geely_0463", "508": "geely_508",
    "1604": "geely_1604", "0508": "geely_0508",
    "5463": "geely_5463", "3637": "geely_3637",
}


def _load_car_keywords() -> Dict[str, str]:
    """从 config_user.yaml 加载 car_keywords, 加载失败则用默认值"""
    try:
        from .config import load_car_keywords as _cfg_load_car_kw
        kw = _cfg_load_car_kw()
        if kw:
            return kw
    except Exception:
        pass
    # fallback: 走旧逻辑直接读 config.yaml
    import sys, os
    try:
        if getattr(sys, 'frozen', False):
            root = os.path.dirname(sys.executable)
        else:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for cfg_name in ("config_user.yaml", "config.yaml"):
            cfg_path = os.path.join(root, cfg_name)
            if os.path.isfile(cfg_path):
                import yaml
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                kw = data.get("car_keywords")
                if isinstance(kw, dict) and kw:
                    return {str(k): str(v) for k, v in kw.items()}
    except Exception:
        pass
    return _DEFAULT_CAR_KEYWORDS


CAR_KEYWORDS_RAW: Dict[str, str] = _load_car_keywords()

# 按 key 长度从长到短排序 (防止短词抢命中)
CAR_KEYWORDS: List[Tuple[str, str]] = sorted(
    CAR_KEYWORDS_RAW.items(), key=lambda x: -len(x[0])
)


def extract_car_type(path: str) -> str:
    """从路径提取车型关键词

    从后往前扫路径分段, 越靠近文件的优先级越高。
    """
    parts = path.replace("\\", "/").lower().split("/")
    for p in reversed(parts):
        for key, car in CAR_KEYWORDS:
            if key.lower() in p:
                return car
    return "unknown"


def _walk_video_files(src_root: str) -> List[str]:
    """遍历源根目录, 返回所有 .h264/.h265 文件的绝对路径 (Windows 格式)"""
    results: List[str] = []
    for root, _, files in os.walk(src_root):
        for name in files:
            if name.lower().endswith(VIDEO_SUFFIX):
                results.append(os.path.join(root, name))
    return results


def _classify_one(full_win_path: str) -> Tuple[str, str]:
    """单个文件: 路径归一化 + 分类

    Args:
        full_win_path: 视频文件的 Windows 绝对路径

    Returns:
        (normalized_linux_path, car_type)
    """
    linux_path = normalize_path(full_win_path)
    car = extract_car_type(linux_path)
    return linux_path, car


def classify_by_car(src_root: str, out_dir: str, user: str,
                    unc_prefix: str = "", linux_prefix: str = "",
                    max_workers: int = 32) -> List[str]:
    """主入口: 遍历视频, 按车型分类, 输出多份 {{USER}}_{car}.txt

    Args:
        src_root:   源视频根目录 (Windows UNC 或本地路径)
        out_dir:    输出目录 (txt 写到这里)
        user:       {{USER}} 用户名占位符的实际值
        unc_prefix: (已弃用) 路径映射改用内置 PATH_MAPPING, 保留参数仅为向后兼容
        linux_prefix: (已弃用) 同上
        max_workers: 并行分类线程数

    Returns:
        排序后的生成 txt 路径列表 (按条数从小到大, 不含 error_unknown.txt)
        如 [unc_replay_folder/GZY_5013.txt, ...]
    """
    os.makedirs(out_dir, exist_ok=True)

    if not os.path.isdir(src_root):
        raise FileNotFoundError(f"源路径不存在: {src_root}")

    print(f"\n[*] 遍历视频目录: {src_root}")
    raw_files = _walk_video_files(src_root)
    print(f"[*] 找到 {len(raw_files)} 个原始视频文件")
    if not raw_files:
        return []

    # Step 1: 按文件名去重 (多路径同名时, 优先不带 _bu 的)
    file_map: Dict[str, List[str]] = defaultdict(list)
    for fp in raw_files:
        file_map[os.path.basename(fp)].append(fp)

    deduped: List[str] = []
    for name, paths in file_map.items():
        non_bu = [p for p in paths if "_bu" not in os.path.basename(p).lower()]
        deduped.append(non_bu[0] if non_bu else paths[0])

    print(f"[*] 按文件名去重后 {len(deduped)} 个, 开始并行分类 ({max_workers} 线程)...")
    print(f"[*] 路径映射: PATH_MAPPING (UNC → /tmp/iot_test/mnt_data)")

    # Step 2: 并行分类
    car_map: Dict[str, List[str]] = defaultdict(list)
    unknown_list: List[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_classify_one, fp): fp for fp in deduped}
        total = len(futs)
        done = 0
        for fut in as_completed(futs):
            linux_path, car = fut.result()
            if car == "unknown":
                unknown_list.append(linux_path)
            else:
                car_map[car].append(linux_path)
            done += 1
            if done % 500 == 0 or done == total:
                print(f"    进度: {done}/{total}")

    # Step 3: 写 txt
    print(f"\n[*] 输出分类 txt 到: {out_dir}")
    output_txts: List[Tuple[str, int]] = []  # [(路径, 条数)]

    for car, paths in sorted(car_map.items()):
        fname = f"{user}_{car}.txt"
        fpath = os.path.join(out_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            for p in paths:
                f.write(p + "\n")
        n = len(paths)
        output_txts.append((fpath, n))
        print(f"    {fname:30s}: {n:5d} 条")

    if unknown_list:
        fname_unk = "error_unknown.txt"
        fpath_unk = os.path.join(out_dir, fname_unk)
        with open(fpath_unk, "w", encoding="utf-8") as f:
            for p in unknown_list:
                f.write(p + "\n")
        print(f"    {fname_unk:30s}: {len(unknown_list):5d} 条 (暂不回灌, 请自行处理)")

    print("\n========== 分类统计 ==========")
    total_cnt = 0
    # 按条数从小到大排序
    output_txts.sort(key=lambda x: x[1])
    for fpath, n in output_txts:
        print(f"  {os.path.basename(fpath):30s}: {n:5d} 条")
        total_cnt += n
    if unknown_list:
        print(f"  {'error_unknown.txt':30s}: {len(unknown_list):5d} 条")
        total_cnt += len(unknown_list)
    print(f"  {'TOTAL':30s}: {total_cnt:5d} 条")

    # 返回排序后的 txt 路径 (不含 unknown)
    return [fp for fp, _ in output_txts]


def match_calibration(txt_filename: str, car_models: dict) -> tuple:
    """从 txt 文件名匹配车型标定

    按 key 长度从长到短匹配, 防止短 key 抢命中
    (如 "0452" 不应先于 "geely_0452" 命中, 虽然当前配置中无此冲突)

    Args:
        txt_filename: txt 文件名 (如 "GZY_5013.txt")
        car_models: 车型-标定映射 dict

    Returns:
        (matched_key, calibration_value) 或 (None, None)
    """
    name_lower = os.path.basename(txt_filename).lower()
    for key in sorted(car_models.keys(), key=len, reverse=True):
        if key.lower() in name_lower:
            return key, car_models[key]
    return None, None
