r"""Jira 网页内容提取

从 Jira issue 页面提取:
- 原始视频路径 (UNC 路径,如 \\server\share\xxx.h265)
  提取策略 (优先级从高到低):
    1. REST API /rest/api/2/issue/<KEY> — 遍历所有 customfield,扫 UNC 路径
    2. HTML 精准匹配 — 字段名 "原始视频路径"/"数据路径" 后面紧跟的 UNC
       (支持强/label/dt/描述列表/单页应用结构)
    3. 页面全文兜底扫描 — 在 description/comment 等富文本区域抓 UNC
- 标题 summary (优先用 REST API,回退 HTML 解析)
- issue_id (从 URL 提取,如 ADAAFTI-123)
"""

import re
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# 工具函数: 路径清洗 + UNC 识别
# ---------------------------------------------------------------------------

_UNC_MIN_LEN = 15  # 小于这个长度的 UNC 基本是误匹配 (如 \\a\b)


def _normalize_unc(raw: str) -> Optional[str]:
    r"""对原始匹配片段做清洗并判定是否为合法 UNC

    清洗规则:
      - 去首尾空白/逗号/分号/引号/HTML实体(&nbsp;)
      - 把 /path/to/share 这种路径中的 / 统一成 \\ (仅限 UNC)
      - 去掉换行和多余空白
    返回合法 UNC 字符串, 非法返回 None
    """
    if not raw:
        return None
    s = raw.replace("&nbsp;", " ").replace("\n", " ").replace("\r", " ").strip()
    # 常见尾部标点(中文句号/分号/引号/顿号/括号)清洗
    s = s.rstrip('，,;:、"）)]。；：')
    # 必须是 \ 开头的 UNC (允许 \\\\server 或 \\server)
    if not (s.startswith("\\") or s.startswith("//")):
        return None
    # 统一 \ 分隔符
    if s.startswith("//"):
        s = "\\" + "\\" + s[2:].replace("/", "\\")
    s = s.replace("/", "\\")
    # 规范化多个连续 \ 为两个开头,后续单个
    prefix = ""
    rest = s.lstrip("\\")
    prefix = "\\\\"
    s = prefix + rest
    if len(s) < _UNC_MIN_LEN:
        return None
    # 必须至少有两个路径分量: \\server\share 算最短,即 \ 数量 >= 3
    if s.count("\\") < 3:
        return None
    return s


_UNC_RE = re.compile(
    r'(?:\\\\|//)[A-Za-z0-9_\-\.~%\$][^\s"\'<>|&{}\x00-\x1f]{8,}'
)


def _scan_unc_in_text(text: str, top_n: int = 10):
    """在任意文本里扫 UNC 候选, 返回前 top_n 个 (清洗后去重,保留顺序)"""
    seen = set()
    out = []
    for m in _UNC_RE.finditer(text or ""):
        cleaned = _normalize_unc(m.group(0))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
            if len(out) >= top_n:
                break
    return out


# ---------------------------------------------------------------------------
# 登录检测 (更严格,避免误判为"未登录"而丢掉真实匹配)
# ---------------------------------------------------------------------------

def _check_login_redirect(response) -> str:
    """检查响应是否被重定向到登录页,返回诊断字符串 (空字符串表示未重定向)

    只有 URL 中明确出现 login/cas 并且与原 issue URL 不一致, 才算登录重定向.
    """
    url = getattr(response, "url", "")
    if not url:
        return ""
    url_l = url.lower()
    if ("login" in url_l or "cas" in url_l or "signin" in url_l
            or "oauth" in url_l or "sso" in url_l):
        return f"(已重定向到登录页, status={response.status_code}, url={url[:120]})"
    return ""


def _check_login_page_content(text: str) -> str:
    """内容级登录页检测 — 仅当页面"非常像登录页"时才标记

    激进策略会误判: Jira 详情页导航栏会包含用户名,而且页面埋点/搜索框含 password 等词。
    严格策略: 必须同时满足 (1) 页面较短 或 title 含登录 (2) 存在真正的登录表单结构。
    """
    if not text:
        return ""
    title_m = re.search(r"<title[^>]*>([^<]*)</title>", text, re.I)
    title = (title_m.group(1) if title_m else "").strip().lower()
    title_login = any(k in title for k in ["login", "sign in", "登录", "sign-in"])
    short_page = len(text) < 20000
    form_login = re.search(
        r'<form[^>]+(login|signin|sign-in|auth|username|password)[^>]*>',
        text, re.I)
    if (title_login or short_page) and form_login:
        return "(页面为登录表单页,疑似 session 未登录)"
    return ""


# ---------------------------------------------------------------------------
# REST API 兜底: 从 customfields 中扫描 UNC 路径
# ---------------------------------------------------------------------------

def _extract_from_rest(session, issue_key: str, base_url: str):
    """调用 Jira REST API 获取 issue fields,扫描所有 customfield / description / comment
    返回 (path_or_None, diag_str)
    """
    api_url = f"{base_url}/rest/api/2/issue/{issue_key}"
    try:
        resp = session.get(api_url, params={"expand": "renderedFields"}, timeout=30)
    except Exception as e:
        return None, f"(REST 请求异常: {e})"
    rd = _check_login_redirect(resp)
    if resp.status_code != 200:
        return None, (rd or f"(REST HTTP {resp.status_code})")
    try:
        data = resp.json()
    except ValueError:
        return None, (rd or "(REST 返回非 JSON)")
    fields = data.get("fields", {})
    # 递归收集所有字符串值
    collected_texts = []

    def _walk(v):
        if isinstance(v, str):
            collected_texts.append(v)
        elif isinstance(v, list):
            for x in v:
                _walk(x)
        elif isinstance(v, dict):
            # comment.body 等
            for k, val in v.items():
                if k.lower() in {"body", "value", "content", "text", "description", "name"}:
                    _walk(val)
                else:
                    _walk(val)
    _walk(fields)
    # renderedFields 也扫一遍
    rendered = data.get("renderedFields") or {}
    _walk(rendered)
    all_text = "\n".join(collected_texts)
    uncs = _scan_unc_in_text(all_text, top_n=10)
    if uncs:
        # 优先选看起来像原始视频/数据路径的 UNC: 含 h264/h265/文件夹 关键词
        def _score(p):
            pl = p.lower()
            score = 0
            for kw in [".h265", ".h264", ".mp4", ".avi", ".mov"]:
                if kw in pl:
                    score += 5
            for kw in ["original", "video", "数据", "原始", "素材", "record"]:
                if kw in pl:
                    score += 2
            if pl.count("\\") > 4:
                score += 1
            return score
        uncs.sort(key=lambda p: -_score(p))
        diag = rd if rd else ""
        return uncs[0], diag
    return None, (rd or "(REST 未扫描到 UNC 路径)")


# ---------------------------------------------------------------------------
# HTML 字段匹配: 更完整的结构覆盖
# ---------------------------------------------------------------------------

# 所有可能的路径字段标签 (含变体,中英文混用)
_FIELD_LABELS = [
    "原始视频路径", "数据路径", "视频路径", "原始数据路径",
    "素材路径", "原始素材路径", "路采数据路径", "源文件路径",
    "原始视频", "原始数据",
    # 英文项目可能出现的标签
    "Original Video Path", "Video Path", "Data Path", "Source Path",
]


def _build_label_pattern(labels):
    """生成字段标签的 OR 正则片段 (按长度从长到短排,避免短词先吃长词)"""
    sorted_labels = sorted(set(labels), key=len, reverse=True)
    escaped = [re.escape(l) for l in sorted_labels]
    return "(?:" + "|".join(escaped) + ")"


_LABELS_RE = _build_label_pattern(_FIELD_LABELS)


# 排除标签: 匹配到这些字段区域时跳过
_EXCLUDE_LABELS_RE = _build_label_pattern([
    "问题素材路径", "其他补充说明", "附件路径", "上传路径",
    "结果路径", "输出路径", "下载路径",
])


def _extract_from_html(text: str) -> Tuple[Optional[str], str]:
    """HTML 中按字段标签 + UNC 组合匹配,返回 (path_or_None, diag_str)"""
    diag = []
    labels_re = _LABELS_RE
    exclude_re = _EXCLUDE_LABELS_RE

    # ========== 阶段 1: 强结构化匹配 ==========
    # 模式 A: <strong>标签</strong><div>内容</div> (原逻辑,扩展标签范围)
    patterns_a = [
        # strong + div
        labels_re + r'\s*[：:]?\s*</strong>\s*<div[^>]*>\s*(\\\\[^"<]+?)\s*<',
        # strong + span / p / td / li (不同布局)
        labels_re + r'\s*[：:]?\s*</strong>\s*<(?:span|p|td|th|li|pre|code)[^>]*>\s*(\\\\[^"<]+?)\s*<',
        # strong 之间可能有其他 span (如 <span>标签</span> 值)
        r'<strong>\s*' + labels_re + r'\s*[：:]?\s*</strong>\s*<[^>]*>\s*([^<]*\\\\[^<]{10,})',
    ]
    for pat in patterns_a:
        for m in re.finditer(pat, text, re.DOTALL | re.I):
            cleaned = _normalize_unc(m.group(1))
            if not cleaned:
                continue
            # 看一下匹配前 800 字符,排除不该匹配的标签
            prev = text[max(0, m.start() - 800):m.start()]
            if re.search(exclude_re, prev, re.I):
                continue
            return cleaned, " ; ".join(diag)

    # ========== 阶段 2: 描述列表 (dt/dd) / label/value ==========
    patterns_b = [
        # <dt>标签</dt><dd>UNC</dd>
        r'<dt[^>]*>\s*' + labels_re + r'\s*[：:]?\s*</dt>\s*<dd[^>]*>\s*(\\\\[^"<]+)\s*<',
        # <label>标签</label> <div>UNC</div>  (配对)
        r'<label[^>]*>\s*' + labels_re + r'\s*[：:]?\s*</label>\s*(?:<[^>]*>\s*)*([^<]*\\\\[^<]{10,})',
        # 单页应用常见:  <div class="name">标签</div><div class="value">UNC</div>
        r'(?:class|data-test-id|aria-label)[^>]*>[\s\S]{0,60}?' + labels_re + r'\s*[：:]?\s*</(?:div|span|p|label)>\s*'
        r'(?:<[^>]*>[\s\S]{0,200}?)(\\\\[^"<\n]{12,})',
    ]
    for pat in patterns_b:
        for m in re.finditer(pat, text, re.DOTALL | re.I):
            # UNC 可能在最后一个 group 或第一个 group
            g = None
            for gi in range(m.lastindex, 0, -1):
                c = _normalize_unc(m.group(gi))
                if c:
                    g = c
                    break
            if not g:
                continue
            prev = text[max(0, m.start() - 800):m.start()]
            if re.search(exclude_re, prev, re.I):
                continue
            return g, " ; ".join(diag)

    # ========== 阶段 3: 标签名冒号后跟 UNC (非结构化,如纯文本/Markdown渲染) ==========
    # "原始视频路径: \\server\share..." 这类直接文本
    patterns_c = [
        # 直接文本冒号 (HTML 转义后可能是 &#xff1a; 或 : )
        labels_re + r'\s*(?:[:：]|&#xff1a;|&#58;)\s*([^\s<"\']{15,})',
        # 标签后是 data-content 属性值
        r'data-content=["\']([^"\'>]*' + labels_re + r'[\s\S]{0,80}?\\\\[^"\'>]{10,})[^"\']*["\']',
    ]
    for pat in patterns_c:
        for m in re.finditer(pat, text, re.I):
            for gi in range(1, (m.lastindex or 0) + 1):
                g = m.group(gi) or ""
                cleaned = _normalize_unc(g)
                if cleaned:
                    prev = text[max(0, m.start() - 800):m.start()]
                    if re.search(exclude_re, prev, re.I):
                        continue
                    return cleaned, " ; ".join(diag)

    # ========== 阶段 4: 全文兜底, 抓所有 UNC 去重后给优先选择 ==========
    # 先把所有 HTML 标签去掉(粗去标签),防止标签中字符干扰
    plain = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.I)
    plain = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", plain, flags=re.I)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = re.sub(r"&nbsp;|&amp;|&lt;|&gt;", " ", plain)
    uncs = _scan_unc_in_text(plain, top_n=20)
    if uncs:
        # 过滤: 在 HTML 中排除字段附近若出现"问题素材/其他补充说明", 则该 UNC 降级
        def _score(p):
            sc = 0
            pl = p.lower()
            # 文件格式分
            for kw in [".h265", ".h264", ".mp4", ".mov", ".avi"]:
                if kw in pl:
                    sc += 5
            # 标签命中分: 在原 HTML 里看 UNC 之前出现过哪个 label
            try:
                idx = text.index(p[:30]) if len(p) >= 30 else -1
            except ValueError:
                idx = -1
            if idx >= 0:
                prev_ctx = text[max(0, idx - 1500):idx]
                if re.search(labels_re, prev_ctx, re.I):
                    sc += 10
                if re.search(exclude_re, prev_ctx, re.I):
                    sc -= 5
            # 深度分: 路径越深越像真实数据
            sc += min(p.count("\\"), 5)
            return sc
        uncs.sort(key=lambda p: -_score(p))
        best = uncs[0]
        best_score = _score(best)
        # 只有分数 >= 一定阈值才接受, 防止纯全文扫到的随机 UNC 误匹配
        if best_score >= 5:
            diag.append(f"(全文兜底匹配,score={best_score})")
            return best, " ; ".join(diag)
        diag.append(f"(候选 UNC 分数不足,top={best_score}:{best[:80]})")

    # 匹配失败诊断: 输出出现过哪些字段标签
    field_matches = re.findall(
        r'<(?:strong|dt|label|span)[^>]*>\s*([^<]{2,30}?(?:路径|数据|视频)[^<]{0,30})\s*</(?:strong|dt|label|span)>',
        text, re.I)
    if field_matches:
        uniq = []
        for f in field_matches:
            if f not in uniq:
                uniq.append(f)
        diag.append(f"(页面字段标签: {', '.join(uniq[:8])})")
    else:
        diag.append(f"(页面长度={len(text)})")
    return None, " ; ".join(diag)


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

def extract_video_path(session, url: str) -> Tuple[Optional[str], str]:
    """从 Jira issue 提取原始视频路径

    Returns:
        (path_str or None, 诊断字符串)
    """
    diag = []
    issue_key = extract_issue_id(url)

    # 计算 base_url (https://jira.xxx.com 部分)
    # url 形如 https://jira.xxx.com/browse/ADAAFTI-123
    base_url = url
    if "/browse/" in base_url:
        base_url = base_url.split("/browse/", 1)[0]
    else:
        base_url = "/".join(base_url.split("/")[:3])  # scheme://host[:port]

    html_text = None
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        redirect_diag = _check_login_redirect(resp)
        if redirect_diag:
            diag.append(redirect_diag)
        html_text = resp.text
        login_diag = _check_login_page_content(html_text)
        if login_diag:
            diag.append(login_diag)
    except Exception as e:
        diag.append(f"(HTML 请求异常: {e})")

    # 1) 先跑 HTML 匹配 (最常用)
    if html_text:
        path, html_diag = _extract_from_html(html_text)
        if path:
            if html_diag:
                diag.append(html_diag)
            return path, " ; ".join(diag)
        if html_diag:
            diag.append(html_diag)

    # 2) HTML 没取到, 兜底 REST API (customfields 全扫描)
    if issue_key:
        rest_path, rest_diag = _extract_from_rest(session, issue_key, base_url)
        if rest_path:
            if rest_diag:
                diag.append(rest_diag)
            diag.append("(REST API 兜底命中)")
            return rest_path, " ; ".join(diag)
        if rest_diag:
            diag.append(rest_diag)

    return None, " ; ".join(diag)


def extract_summary(session, url):
    """提取 Jira issue 标题 (summary)

    优先用 REST API (/rest/api/2/issue/<KEY>),失败回退 HTML 解析。

    Returns:
        (标题字符串或 None, 诊断信息)
    """
    diag = []
    try:
        issue_key = extract_issue_id(url)
        if issue_key:
            base_url = url
            if "/browse/" in base_url:
                base_url = base_url.split("/browse/", 1)[0]
            else:
                base_url = "/".join(base_url.split("/")[:3])
            api_url = f"{base_url}/rest/api/2/issue/{issue_key}"
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
                diag.append(
                    f"(HTML 无匹配,title="
                    f"{re.search(r'<title>([^<]*)</title>', text).group(1)[:80] if re.search(r'<title>([^<]*)</title>', text) else '(空)'})")
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
