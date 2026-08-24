"""车型分类

根据 Jira 标题 (summary) 和视频路径 (video_path) 推断车型编号。

车型映射源 (优先级从高到低):
  1. config.yaml 的 data_proc_car_keywords 段 (title_keywords / path_keywords / default)
  2. 内置 DEFAULT_DATA_PROC_CAR (config.py 兜底)

匹配优先级:
  1. summary 含 title_keywords 的 key → 取对应 value
  2. video_path 含 path_keywords 的 key → 取对应 value
  3. 都不命中 → default
"""

from typing import Optional, Dict, Any

from ..config import load_data_proc_car_keywords


# 模块级缓存 (首次调用 classify 时加载, 避免每条素材都读 yaml)
_CFG: Optional[Dict[str, Any]] = None


def _get_cfg() -> Dict[str, Any]:
    global _CFG
    if _CFG is None:
        _CFG = load_data_proc_car_keywords()
    return _CFG


def classify(summary: Optional[str], video_path: Optional[str]) -> str:
    """根据标题和视频路径推断车型编号

    Args:
        summary: Jira issue 标题
        video_path: 视频数据路径 (UNC)

    Returns:
        车型编号字符串 (如 "463", "0452")
    """
    cfg = _get_cfg()

    if summary:
        for keyword, category in cfg["title_keywords"].items():
            if keyword in summary:
                return str(category)

    if video_path:
        for path_key, category in cfg["path_keywords"].items():
            if path_key in video_path:
                return str(category)

    return str(cfg["default"])
