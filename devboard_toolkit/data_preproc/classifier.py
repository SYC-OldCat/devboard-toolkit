"""车型分类

根据 Jira 标题 (summary) 和视频路径 (video_path) 推断车型编号。

匹配优先级:
  1. summary 含 "XXX车" 关键词 → 取对应车型编号
  2. video_path 含路径关键词 (如 0463) → 取对应车型编号
  3. 都不命中 → 默认 0452
"""

from typing import Optional


# 标题关键词 → 车型编号 (summary 中出现 "463车" 等即匹配)
CAR_KEYWORDS = ["463车", "508车", "3545车", "3554车", "3637车", "5436车", "5463车"]

# 路径关键词 → 车型编号 (video_path 中出现 0463 等即匹配)
PATH_KEYWORDS = {
    "0463": "463",
    "0508": "508",
    "3545": "3545",
    "3554": "3554",
    "3637": "3637",
    "5436": "5463",
    "5463": "5463",
}

# 默认车型 (匹配不到时)
DEFAULT_CATEGORY = "0452"


def classify(summary: Optional[str], video_path: Optional[str]) -> str:
    """根据标题和视频路径推断车型编号

    Args:
        summary: Jira issue 标题
        video_path: 视频数据路径 (UNC)

    Returns:
        车型编号字符串 (如 "463", "0452")
    """
    if summary:
        for keyword in CAR_KEYWORDS:
            if keyword in summary:
                return keyword.replace("车", "")

    if video_path:
        for path_key, category in PATH_KEYWORDS.items():
            if path_key in video_path:
                return category

    return DEFAULT_CATEGORY
