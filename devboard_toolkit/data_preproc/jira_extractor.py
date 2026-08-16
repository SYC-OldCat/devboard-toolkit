"""Jira 网页内容提取

从 Jira issue 页面提取:
- 原始视频路径 (UNC 路径,如 \\\\server\\share\\xxx.h265)
- 标题 summary (优先用 REST API,回退 HTML 解析)
- issue_id (从 URL 提取,如 ADAAFTI-123)
"""

import re


def _check_login_redirect(response) -> str:
    """检查响应是否被重定向到登录页,返回诊断字符串 (空字符串表示未重定向)"""
    url = getattr(response, "url", "")
    if "login" in url.lower() or "cas" in url.lower():
        return f"(已重定向到登录页, status={response.status_code}, url={url[:120]})"
    return ""


def extract_video_path(session, url):
    """从 Jira 页面提取原始视频路径 (UNC \\\\路径)

    匹配规则:
      - 字段名 "原始视频路径" 或 "数据路径" 后跟 <div> 里的 UNC 路径
      - 排除 "问题素材路径" / "其他补充说明" 段落(避免误匹配)
      - 路径长度 > 15 才算有效

    Returns:
        (视频路径字符串或 None, 诊断信息) — 无诊断时返回 (path, "")
    """
    diag = []
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()

        redirect_diag = _check_login_redirect(response)
        if redirect_diag:
            diag.append(redirect_diag)

        text = response.text

        # 兜底诊断: 如果页面明显是登录页(含"用户名"/"登录"/Sign in),记下来
        if any(k in text.lower() for k in ["用户名", "password", "sign in", "用户名或密码", "username"]):
            if redirect_diag:
                pass  # 已经有登录重定向诊断
            else:
                diag.append("(页面含登录表单元素,疑似 session 未登录)")

        patterns = [
            r'原始视频路径[：:]\s*</strong>\s*<div[^>]*>\s*(\\\\[^"<]+?)\s*<',
            r'数据路径[：:]\s*</strong>\s*<div[^>]*>\s*(\\\\[^"<]+?)\s*<',
            r'原始视频路径[：:]\s*</strong>.*?<div[^>]*>\s*(\\\\[^"<]+?)\s*<',
            r'数据路径[：:]\s*</strong>.*?<div[^>]*>\s*(\\\\[^"<]+?)\s*<',
            # 兜底: 宽松匹配 <strong>原始视频路径</strong> 之后的任意 \\... 路径 (不限制 div)
            r'原始视频路径[：:]\s*</strong>\s*([^"\'<]*\\\\[^"\'<]{10,})',
            r'数据路径[：:]\s*</strong>\s*([^"\'<]*\\\\[^"\'<]{10,})',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text, re.DOTALL):
                path = match.group(1).strip()
                if len(path) <= 15:
                    continue

                # 排除问题素材路径/其他补充说明段落
                match_start = match.start()
                prev_start = max(0, match_start - 500)
                prev_text = text[prev_start:match_start]
                if "问题素材路径" in prev_text or "其他补充说明" in prev_text:
                    continue

                return path, " ; ".join(diag)

        # 匹配失败: 额外看一下页面里出现过哪些自定义字段标签,辅助定位
        if not diag:
            # 扫一下 <strong>...</strong> 标签中包含"路径"或"数据"的标签名
            field_matches = re.findall(r'<strong>\s*([^<]*?(?:路径|数据)[^<]*?)\s*</strong>', text)
            if field_matches:
                diag.append(f"(页面字段标签: {', '.join(field_matches[:5])})")
            else:
                diag.append(f"(未匹配到任何字段标签,页面长度={len(text)})")

        return None, " ; ".join(diag)
    except Exception as e:
        diag.append(f"(请求异常: {e})")
        return None, " ; ".join(diag)


def extract_summary(session, url):
    """提取 Jira issue 标题 (summary)

    优先用 REST API (/rest/api/2/issue/<KEY>),失败回退 HTML 解析。

    Returns:
        (标题字符串或 None, 诊断信息) — 无诊断时返回 (summary, "")
    """
    diag = []
    try:
        issue_key = extract_issue_id(url)
        if issue_key:
            # REST API: /browse/XXX-1 → /rest/api/2/issue/XXX-1
            base = url.rsplit("/", 1)[0].rsplit("/", 1)[0]
            api_url = f"{base}/rest/api/2/issue/{issue_key}"
            try:
                response = session.get(api_url, timeout=30)
                redirect_diag = _check_login_redirect(response)
                if redirect_diag:
                    diag.append(f"REST{redirect_diag}")
                response.raise_for_status()
                try:
                    data = response.json()
                    summary = data.get("fields", {}).get("summary", "")
                    if summary:
                        return summary, " ; ".join(diag)
                except ValueError as ve:
                    diag.append(f"(REST 返回非 JSON: {str(ve)[:80]})")
            except Exception as api_exc:
                diag.append(f"(REST 请求失败: {api_exc})")

        # HTML 回退
        try:
            response = session.get(url, timeout=30)
            redirect_diag = _check_login_redirect(response)
            if redirect_diag:
                diag.append(f"HTML{redirect_diag}")
            response.raise_for_status()
            text = response.text

            summary_patterns = [
                r'<input[^>]*id=["\']summary["\'][^>]*value=["\']([^"\']+)["\']',
                r'<h1[^>]*id=["\']summary-val["\'][^>]*>([^<]+)</h1>',
                r'<span[^>]*id=["\']summary-val["\'][^>]*>([^<]+)</span>',
                r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
                # 兜底: <title>ADAAFTI-123: xxx</title>
                r'<title>\s*[A-Z]+-\d+\s*:\s*([^<]{3,})</title>',
                r'<title>\s*([^<]{5,})\s*-\s*[A-Z]+-\d+',
            ]
            for pattern in summary_patterns:
                match = re.search(pattern, text)
                if match:
                    return match.group(1).strip(), " ; ".join(diag)

            if not diag:
                diag.append(f"(HTML 无匹配,title={re.search(r'<title>([^<]*)</title>', text).group(1)[:80] if re.search(r'<title>([^<]*)</title>', text) else '(空)'})")
        except Exception as html_exc:
            diag.append(f"(HTML 请求失败: {html_exc})")

        return None, " ; ".join(diag)
    except Exception as e:
        diag.append(f"(异常: {e})")
        return None, " ; ".join(diag)


def extract_issue_id(url):
    """从 URL 提取 issue ID (如 ADAAFTI-123)"""
    pattern = r"/browse/([A-Z]+-\d+)"
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    return url.rsplit("/", 1)[-1] if "/" in url else url
