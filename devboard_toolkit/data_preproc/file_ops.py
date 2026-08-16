"""文件操作

- 读取 txt 中的链接/路径列表
- 复制单个 .h265/.h264 文件 (可选同名子目录)
- 复制文件夹下所有 .h265/.h264 文件 (只保留同前缀最大后缀, 可选同名子目录)
"""

import os
import re
import shutil
from typing import List, Tuple, Dict


def read_links(txt_path: str, jira_base_url: str = "") -> List[str]:
    """读取 txt 文件中的链接/路径列表 (每行一个,自动去引号)

    支持每行三种形式:
      1. 完整 URL (http:// 或 https:// 开头) → 原样使用
      2. 纯 issue ID,如 ADAAFTI-123 / FTIM-586 → 自动拼 jira_base_url + "/browse/" + id
      3. /browse/XXX-123 形式 → 自动补 jira_base_url

    Args:
        txt_path: txt 文件路径
        jira_base_url: Jira 服务前缀, 如 "https://jira.arcsoft.com.cn:8443"
                      留空时不做补全,纯 id 会原样保留 (由上层处理)

    Returns:
        链接/路径字符串列表
    """
    if not os.path.exists(txt_path):
        raise FileNotFoundError(f"txt 文件不存在: {txt_path}")

    links: List[str] = []
    jira_base = (jira_base_url or "").rstrip("/")

    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            line = line.strip('"').strip("'")

            # 完整 URL → 原样
            if line.lower().startswith(("http://", "https://")):
                links.append(line)
                continue

            # /browse/XXX-123 → 补 jira_base
            if line.startswith("/browse/") and jira_base:
                links.append(f"{jira_base}{line}")
                continue

            # 纯 issue ID: 形如 [A-Z]+-数字,如 FTIM-586 / ADAAFTI-123
            if jira_base:
                if re.match(r"^[A-Z][A-Z0-9]*-\d+$", line, re.IGNORECASE):
                    links.append(f"{jira_base}/browse/{line}")
                    continue

            # 其它情况: 当作文件路径 (模式 2) 或不处理,原样保留
            links.append(line)

    return links


def copy_single_file(src_path: str, output_dir: str,
                     create_file_folder: bool = True) -> Tuple[bool, str]:
    """复制单个文件到目标目录

    create_file_folder=True:  output_dir/<文件名去后缀>/<原文件名>
    create_file_folder=False: output_dir/<原文件名>

    Returns:
        (是否成功, 消息)
    """
    try:
        if not os.path.isfile(src_path):
            return False, "源文件不存在"

        file_name = os.path.basename(src_path)
        name_no_ext, _ = os.path.splitext(file_name)

        if create_file_folder:
            target_dir = os.path.join(output_dir, name_no_ext)
            os.makedirs(target_dir, exist_ok=True)
            dst_path = os.path.join(target_dir, file_name)
        else:
            os.makedirs(output_dir, exist_ok=True)
            dst_path = os.path.join(output_dir, file_name)

        if os.path.exists(dst_path):
            return True, "文件已存在,跳过"

        shutil.copy2(src_path, dst_path)
        return True, f"复制成功: {dst_path}"
    except Exception as e:
        return False, str(e)


def _extract_prefix_and_num(name_no_ext: str):
    """从文件名(去后缀)提取前缀和数字后缀

    支持两种命名:
      模式A: 文件名_数字        如 20260324_150504_3 → ("20260324_150504", 3)
      模式B: 文件名_数字_cameraId_N  如 ...190319_1_cameraId_0
             → 先去掉 _cameraId_N → ...190319_1 → ("...190319", 1)
    不匹配则返回 (name_no_ext, None)
    """
    # 先去掉 _cameraId_数字 后缀
    m_cam = re.match(r"^(.+)_cameraId_\d+$", name_no_ext)
    if m_cam:
        name_no_ext = m_cam.group(1)

    m = re.match(r"^(.+)_(\d+)$", name_no_ext)
    if m:
        return m.group(1), int(m.group(2))
    return name_no_ext, None


def _select_largest_suffix(h265_files: List[str]) -> List[str]:
    """从文件列表中,按前缀分组,每组只保留数字后缀最大的文件

    无数字后缀的文件各自独立成组,全部保留。
    """
    groups: Dict[str, List[Tuple[int, str]]] = {}
    standalone: List[str] = []

    for fpath in h265_files:
        fname = os.path.basename(fpath)
        name_no_ext, _ = os.path.splitext(fname)
        prefix, num = _extract_prefix_and_num(name_no_ext)
        if num is None:
            standalone.append(fpath)
        else:
            groups.setdefault(prefix, []).append((num, fpath))

    result: List[str] = list(standalone)
    for prefix, items in groups.items():
        items.sort(key=lambda x: x[0])
        result.append(items[-1][1])

    return result


def copy_folder_h265_files(src_folder: str, output_dir: str,
                          create_file_folder: bool = True,
                          keep_largest_suffix: bool = True) -> Tuple[bool, str]:
    """复制文件夹下所有 .h265/.h264 文件到目标目录

    keep_largest_suffix=True (默认): 按前缀分组,每组只保留数字后缀最大的文件
      支持 _cameraId_N 后缀: 先去掉 _cameraId_N 再分组
    keep_largest_suffix=False: 复制全部 .h265/.h264 文件
    每个文件可选创建同名子目录。

    create_file_folder=True:  output_dir/<文件名去后缀>/<原文件名>
    create_file_folder=False: output_dir/<原文件名>

    Returns:
        (是否成功, 消息)
    """
    try:
        if not os.path.isdir(src_folder):
            return False, "源文件夹不存在"

        h265_files: List[str] = []
        for root, _, files in os.walk(src_folder):
            for file in files:
                if file.lower().endswith((".h265", ".h264")):
                    h265_files.append(os.path.join(root, file))

        if not h265_files:
            return False, "文件夹中未找到 .h265 或 .h264 文件"

        if keep_largest_suffix:
            # 按前缀分组,每组只保留最大后缀
            selected = _select_largest_suffix(h265_files)
            skipped = len(h265_files) - len(selected)
            if skipped > 0:
                print(f"    [i] 同前缀去重: {len(h265_files)} → {len(selected)} 个 (跳过 {skipped} 个小后缀文件)")
        else:
            selected = h265_files

        copied_count = 0
        for src_file in selected:
            file_name = os.path.basename(src_file)
            name_no_ext, _ = os.path.splitext(file_name)

            if create_file_folder:
                file_target_dir = os.path.join(output_dir, name_no_ext)
                os.makedirs(file_target_dir, exist_ok=True)
                dst_path = os.path.join(file_target_dir, file_name)
            else:
                os.makedirs(output_dir, exist_ok=True)
                dst_path = os.path.join(output_dir, file_name)

            if os.path.exists(dst_path):
                continue

            shutil.copy2(src_file, dst_path)
            copied_count += 1

        return True, f"复制成功,共复制 {copied_count} 个文件到 {output_dir}"
    except Exception as e:
        return False, str(e)
