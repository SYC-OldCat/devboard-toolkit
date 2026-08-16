"""数据预处理子包

从 D:\\Desktop\\Data_preprocessing 项目搬运, 去除 GUI 部分。

功能:
  - Jira CAS 认证 (jira_auth)
  - Jira 网页内容提取 (jira_extractor)
  - 车型分类 (classifier)
  - 文件复制 (file_ops)
  - Excel 报告 (excel_report)
  - ADAS 并行预处理 (preprocessor)
  - 主流程编排 (pipeline)

入口:
  from devboard_toolkit.data_preproc import data_preproc_main
  data_preproc_main()
"""

from .pipeline import data_preproc_main
from .jira_auth import create_session, create_session_with_cookies
from .jira_extractor import extract_video_path, extract_summary, extract_issue_id
from .classifier import classify
from .file_ops import read_links, copy_single_file, copy_folder_h265_files
from .excel_report import generate_excel_results
from .preprocessor import run_preprocessing

__all__ = [
    "data_preproc_main",
    "create_session",
    "create_session_with_cookies",
    "extract_video_path",
    "extract_summary",
    "extract_issue_id",
    "classify",
    "read_links",
    "copy_single_file",
    "copy_folder_h265_files",
    "generate_excel_results",
    "run_preprocessing",
]
