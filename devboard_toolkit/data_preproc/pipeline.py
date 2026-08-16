"""数据处理主流程编排

两种模式:
  模式 1 - Jira 链接: txt 内是 Jira URL → 认证 → 提取视频路径 → 分类 → 复制 → Excel → 可选预处理
  模式 2 - 视频路径: txt 内是 .h265/.h264 或文件夹路径 → 直接复制 → 可选预处理

并发:
  模式 1 用 ThreadPoolExecutor (默认 5),每个线程用 cookies 重建 session
  模式 2 顺序处理 (复制是 IO 密集,线程池意义不大)
"""

import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional

from ..config import load_jira_data
from .jira_auth import create_session, USER_AGENT
from .jira_extractor import extract_video_path, extract_summary, extract_issue_id
from .classifier import classify
from .file_ops import read_links, copy_single_file, copy_folder_h265_files
from .excel_report import generate_excel_results
from .preprocessor import run_preprocessing
import requests as _requests


def _build_worker_session(master_cookiejar):
    """为子线程构造独立 session,完整复制 CookieJar (保留 domain/path/secure)

    不用 get_dict()/cookies.set() 的原因: set() 默认 domain 为空会丢失域绑定,
    子线程重建后实际请求 Jira 时不会带上 cookie,表现为"认证成功但子线程全失败"。
    """
    s = _requests.Session()
    s.verify = False
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    s.cookies = copy.deepcopy(master_cookiejar)
    return s


def _process_single_jira(
    url: str,
    output_dir: str,
    create_jira_folder: bool,
    classify_category: bool,
    master_cookiejar,
    create_file_folder: bool = True,
    keep_largest_suffix: bool = True,
) -> Dict[str, Any]:
    """处理单个 Jira 链接 (每线程独立 session + deepcopy CookieJar)"""
    session = _build_worker_session(master_cookiejar)
    try:
        issue_id = extract_issue_id(url)

        summary, summary_diag = extract_summary(session, url)
        video_path, video_diag = extract_video_path(session, url)

        # 分类 → 子目录
        if classify_category:
            category = classify(summary, video_path)
            category_dir = os.path.join(output_dir, category)
            os.makedirs(category_dir, exist_ok=True)
        else:
            category_dir = output_dir

        # Jira 同名目录
        if create_jira_folder:
            current_output_dir = os.path.join(category_dir, issue_id)
            os.makedirs(current_output_dir, exist_ok=True)
        else:
            current_output_dir = category_dir

        fail_reason = ""
        diags = []
        if not summary:
            fail_reason += "[标题未找到]"
            if summary_diag:
                diags.append(f"标题诊断: {summary_diag}")
        if not video_path:
            fail_reason += "[视频路径未找到]"
            if video_diag:
                diags.append(f"视频诊断: {video_diag}")

        result_item = {
            "issue_id": issue_id,
            "url": url,
            "summary": summary if summary else "未找到",
            "video_path": video_path if video_path else "未找到",
            "fail_reason": "",
            "success": False,
        }
        if fail_reason or diags:
            parts = []
            if fail_reason:
                parts.append(fail_reason)
            parts.extend(diags)
            result_item["fail_reason"] = " | ".join(parts)

        if not video_path:
            return result_item

        # 复制
        if video_path.lower().endswith((".h265", ".h264")):
            ok, msg = copy_single_file(video_path, current_output_dir,
                                       create_file_folder=create_file_folder)
        else:
            ok, msg = copy_folder_h265_files(video_path, current_output_dir,
                                             create_file_folder=create_file_folder,
                                             keep_largest_suffix=keep_largest_suffix)

        if ok:
            result_item["success"] = True
        else:
            result_item["fail_reason"] = f"复制失败: {msg}"
        return result_item

    except Exception as e:
        issue_id = extract_issue_id(url)
        return {
            "issue_id": issue_id,
            "url": url,
            "summary": "处理异常",
            "video_path": str(e),
            "fail_reason": f"异常: {e}",
            "success": False,
        }


def process_jira_links(
    jira_links: List[str],
    output_dir: str,
    master_session,
    create_jira_folder: bool = False,
    classify_category: bool = True,
    max_workers: int = 5,
    stop_event: Optional[threading.Event] = None,
    create_file_folder: bool = True,
    keep_largest_suffix: bool = True,
) -> List[Dict[str, Any]]:
    """多线程处理 Jira 链接列表

    Args:
        master_session: 主线程已认证的 requests.Session (仅用于提取 cookiejar,子线程 deepcopy)
        stop_event: 取消事件信号,设置后立即取消未开始任务并退出
        其他参数见 data_preproc_main

    Returns:
        每条链接的处理结果列表
    """
    os.makedirs(output_dir, exist_ok=True)
    total = len(jira_links)
    results: List[Dict[str, Any]] = []

    master_cookiejar = master_session.cookies

    print(f"\n[*] 开始处理 {total} 个 Jira 链接 (并发数: {max_workers})")

    cancelled = False
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_single_jira, url, output_dir,
                create_jira_folder, classify_category, master_cookiejar,
                create_file_folder, keep_largest_suffix
            ): url
            for url in jira_links
        }
        done = 0
        for future in as_completed(futures):
            if stop_event is not None and stop_event.is_set():
                print(f"\n[!] 用户请求取消,终止剩余 {total - done} 个任务")
                executor.shutdown(wait=False, cancel_futures=True)
                cancelled = True
                break
            result_item = future.result()
            results.append(result_item)
            done += 1
            if result_item.get("success"):
                print(f"  [{done}/{total}] {result_item['issue_id']} (OK)")
            else:
                reason = result_item.get("fail_reason") or "(未知原因)"
                vp = result_item.get("video_path", "")
                if vp and vp != "未找到":
                    reason = f"{reason} video_path={vp[:80]}"
                print(f"  [{done}/{total}] {result_item['issue_id']} (FAIL) {reason}")

    success_count = sum(1 for r in results if r.get("success", False))
    failed_count = len(results) - success_count

    generate_excel_results(results, output_dir)

    print(f"\n========== 处理{'(已取消)' if cancelled else '完成'} ==========")
    print(f"[+] 成功: {success_count}")
    print(f"[-] 失败: {failed_count}")
    if cancelled:
        print(f"[!] 剩余未处理: {total - len(results)}")
    print(f"[>] 目标目录: {output_dir}")

    return results


def process_video_paths(
    folder_path: str,
    output_dir: str,
    stop_event: Optional[threading.Event] = None,
    create_file_folder: bool = True,
    classify_category: bool = True,
) -> List[Dict[str, Any]]:
    """处理视频路径 (模式 2: 遍历单个文件夹, 找到所有 .h265/.h264, 分类复制)

    Args:
        folder_path: 源文件夹路径 (递归遍历)
        output_dir: 输出目录
        create_file_folder: 每个文件是否创建同名子目录
        classify_category: 是否按车型分类
    """
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.isdir(folder_path):
        print(f"[!] 文件夹不存在: {folder_path}")
        return []

    # 遍历文件夹找到所有 .h265/.h264 文件
    h265_files: List[str] = []
    for root, _, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith((".h265", ".h264")):
                h265_files.append(os.path.join(root, f))

    if not h265_files:
        print(f"[!] 文件夹中未找到 .h265 或 .h264 文件: {folder_path}")
        return []

    total = len(h265_files)
    print(f"\n[*] 遍历找到 {total} 个 .h265/.h264 文件")

    success = 0
    failed = 0
    results: List[Dict[str, Any]] = []
    cancelled = False

    for i, src_file in enumerate(h265_files, 1):
        if stop_event is not None and stop_event.is_set():
            print(f"\n[!] 用户请求取消,终止剩余 {total - i + 1} 个任务")
            cancelled = True
            break

        # 分类 → 子目录
        if classify_category:
            category = classify(None, src_file)
            target_dir = os.path.join(output_dir, category)
            os.makedirs(target_dir, exist_ok=True)
        else:
            target_dir = output_dir

        ok, _msg = copy_single_file(src_file, target_dir,
                                     create_file_folder=create_file_folder)

        if ok:
            success += 1
        else:
            failed += 1
        results.append({
            "issue_id": os.path.basename(src_file),
            "url": src_file,
            "summary": "",
            "video_path": src_file,
            "success": ok,
        })
        print(f"  [{i}/{total}] {os.path.basename(src_file)} ({'OK' if ok else 'FAIL'})")

    generate_excel_results(results, output_dir)

    print(f"\n========== 处理{'(已取消)' if cancelled else '完成'} ==========")
    print(f"[+] 成功: {success}")
    print(f"[-] 失败: {failed}")
    if cancelled:
        print(f"[!] 剩余未处理: {total - len(results)}")
    print(f"[>] 目标目录: {output_dir}")

    return results


def process_file_list(
    file_paths: List[str],
    output_dir: str,
    stop_event: Optional[threading.Event] = None,
    create_file_folder: bool = True,
    classify_category: bool = True,
) -> List[Dict[str, Any]]:
    """处理文件清单 (模式 3: txt 中每行是特定 .h265/.h264 路径, 分类复制)

    Args:
        file_paths: 文件路径列表 (从 txt 读取)
        output_dir: 输出目录
        create_file_folder: 每个文件是否创建同名子目录
        classify_category: 是否按车型分类
    """
    os.makedirs(output_dir, exist_ok=True)

    total = len(file_paths)
    success = 0
    failed = 0
    results: List[Dict[str, Any]] = []
    cancelled = False

    for i, file_path in enumerate(file_paths, 1):
        if stop_event is not None and stop_event.is_set():
            print(f"\n[!] 用户请求取消,终止剩余 {total - i + 1} 个任务")
            cancelled = True
            break

        # 分类 → 子目录
        if classify_category:
            category = classify(None, file_path)
            target_dir = os.path.join(output_dir, category)
            os.makedirs(target_dir, exist_ok=True)
        else:
            target_dir = output_dir

        ok, _msg = copy_single_file(file_path, target_dir,
                                    create_file_folder=create_file_folder)

        if ok:
            success += 1
        else:
            failed += 1
        results.append({
            "issue_id": os.path.basename(file_path),
            "url": file_path,
            "summary": "",
            "video_path": file_path,
            "success": ok,
        })
        print(f"  [{i}/{total}] {os.path.basename(file_path)} ({'OK' if ok else 'FAIL'})")

    generate_excel_results(results, output_dir)

    print(f"\n========== 处理{'(已取消)' if cancelled else '完成'} ==========")
    print(f"[+] 成功: {success}")
    print(f"[-] 失败: {failed}")
    if cancelled:
        print(f"[!] 剩余未处理: {total - len(results)}")
    print(f"[>] 目标目录: {output_dir}")

    return results


def data_preproc_main(
    txt_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    mode: Optional[str] = None,
    create_jira_folder: bool = False,
    classify_category: bool = True,
    run_preprocessing_flag: bool = True,
    max_workers: int = 5,
    car_type: int = 3,
    generate_mcap: bool = False,
    stop_event: Optional[threading.Event] = None,
    create_file_folder: bool = True,
    keep_largest_suffix: bool = True,
) -> int:
    """数据处理主入口 (交互式 / 参数式 两种调用方式)

    Args:
        txt_path: 模式1/3 为 txt 文件路径, 模式2 为文件夹路径。None 则交互输入
        output_dir: 输出目录。None 则交互输入
        mode: "1" Jira 链接 / "2" 视频路径(文件夹) / "3" 文件清单(txt)。None 则交互选择
        create_jira_folder: 是否为每个 Jira 创建同名子目录 (仅模式1)
        classify_category: 是否按车型分类
        run_preprocessing_flag: 是否运行 ADAS 预处理
        max_workers: Jira 模式并发数
        car_type: ADAS 预处理车型 (0-9)
        generate_mcap: 是否生成 mcap 文件
        stop_event: 取消事件信号,GUI 取消时触发
        create_file_folder: 每个文件是否创建同名子目录
        keep_largest_suffix: 只保留同前缀最大后缀文件 (仅模式1)

    Returns:
        0 成功 / 1 失败
    """
    print("=" * 60)
    print("  数据预处理工具 (Jira 提取 / 视频复制 / ADAS 预处理)")
    print("=" * 60)

    # 交互补全参数
    if mode is None:
        print("\n模式 1: Jira 链接 (txt 内为 Jira URL, 自动提取视频路径)")
        print("模式 2: 视频路径 (输入文件夹路径, 遍历找 .h265)")
        print("模式 3: 文件清单 (txt 内为 .h265 文件路径, 逐个复制)")
        try:
            mode = input("请选择模式 (1/2/3): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1

    if txt_path is None:
        try:
            if mode == "2":
                txt_path = input("请输入文件夹路径: ").strip().strip('"').strip("'")
            else:
                txt_path = input("请输入 txt 文件路径: ").strip().strip('"').strip("'")
        except (EOFError, KeyboardInterrupt):
            return 1

    if not os.path.exists(txt_path):
        print(f"[!] 路径不存在: {txt_path}")
        return 1

    if output_dir is None:
        try:
            output_dir = input("请输入目标目录路径: ").strip().strip('"').strip("'")
        except (EOFError, KeyboardInterrupt):
            return 1

    if not output_dir:
        print("[!] 请输入目标目录路径")
        return 1

    if mode == "1":
        # === 模式 1: Jira 链接 ===
        jira_cfg = load_jira_data()
        username = jira_cfg["username"]
        password = jira_cfg["password"]
        test_url = jira_cfg["test_url"]

        # 从 test_url 提取 Jira base (如 https://jira.arcsoft.com.cn:8443)
        # 用于把 txt 中纯 issue_id (FTIM-586) 自动补全为完整 browse URL
        import re as _re_re
        _m = _re_re.match(r"^(https?://[^/]+)", test_url)
        jira_base_url = _m.group(1) if _m else ""

        # 重读 links: 纯 issue_id 自动补全 browse URL
        try:
            links = read_links(txt_path, jira_base_url=jira_base_url)
        except Exception as e:
            print(f"[!] 读取 txt (含 URL 补全) 失败: {e}")
            return 1
        if not links:
            print("[!] 未找到任何链接/路径")
            return 1
        print(f"[*] 配置: 创建Jira目录={create_jira_folder}, 车型分类={classify_category}, "
              f"预处理={run_preprocessing_flag}, 并发={max_workers}")

        print("[*] Jira 认证中...")
        session = create_session(username, password, test_url)
        if not session:
            print("[!] Jira 认证失败,请检查账号密码 / 网络")
            return 1
        print("[+] 认证成功")

        process_jira_links(
            links, output_dir, session,
            create_jira_folder=create_jira_folder,
            classify_category=classify_category,
            max_workers=max_workers,
            stop_event=stop_event,
            create_file_folder=create_file_folder,
            keep_largest_suffix=keep_largest_suffix,
        )
        if stop_event is not None and stop_event.is_set():
            return 2
    elif mode == "2":
        # === 模式 2: 视频路径 (遍历文件夹) ===
        print(f"[*] 配置: 车型分类={classify_category}, 同名文件夹={create_file_folder}")
        process_video_paths(txt_path, output_dir, stop_event=stop_event,
                            create_file_folder=create_file_folder,
                            classify_category=classify_category)
        if stop_event is not None and stop_event.is_set():
            return 2
    elif mode == "3":
        # === 模式 3: 文件清单 (txt 内为 .h265 路径) ===
        try:
            file_paths = read_links(txt_path)
        except Exception as e:
            print(f"[!] 读取 txt 失败: {e}")
            return 1
        if not file_paths:
            print("[!] 未找到任何文件路径")
            return 1
        print(f"[*] 读取到 {len(file_paths)} 条记录")
        print(f"[*] 配置: 车型分类={classify_category}, 同名文件夹={create_file_folder}")
        process_file_list(file_paths, output_dir, stop_event=stop_event,
                          create_file_folder=create_file_folder,
                          classify_category=classify_category)
        if stop_event is not None and stop_event.is_set():
            return 2

    # 取消则跳过预处理
    if stop_event is not None and stop_event.is_set():
        print("[*] 已取消,跳过 ADAS 预处理")
        return 2

    # 预处理
    if run_preprocessing_flag:
        ok, msg = run_preprocessing(output_dir, car_type=car_type,
                                    generate_mcap=generate_mcap,
                                    stop_event=stop_event)
        if ok:
            print(f"[+] {msg}")
        else:
            print(f"[!] {msg}")
    else:
        print("[*] 跳过 ADAS 预处理")

    return 0
