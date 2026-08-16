"""Jira CAS 认证

支持两种使用方式:
1. 用户名密码登录 (create_session) - 通过 CAS 单点登录表单认证
2. 复用已有 cookies (create_session_with_cookies) - 多线程场景共享会话
"""

import re
import urllib.parse
import warnings

import requests
import urllib3

# 全局抑制 verify=False 产生的 InsecureRequestWarning
# (Jira / CAS 服务器在内网, 证书不校验属于正常场景)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings(
    "ignore",
    category=requests.packages.urllib3.exceptions.InsecureRequestWarning,
)


# 浏览器 UA (模拟 Chrome, 避免被服务器识别为爬虫)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _extract_lt_exec(response_text):
    """从 CAS 登录页 HTML 提取 lt / execution 隐藏字段 (用于提交表单)

    优先用 BeautifulSoup 解析,不可用则回退正则。
    """
    lt_value = None
    execution_value = None

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response_text, "html.parser")
        for input_tag in soup.find_all("input"):
            name = input_tag.get("name", "")
            value = input_tag.get("value", "")
            if name == "lt":
                lt_value = value
            elif name == "execution":
                execution_value = value
        if lt_value or execution_value:
            return lt_value, execution_value
    except ImportError:
        pass

    # 正则回退
    lt_pattern = r'<input[^>]*name=["\']lt["\'][^>]*value=["\']([^"\']+)["\']'
    exec_pattern = r'<input[^>]*name=["\']execution["\'][^>]*value=["\']([^"\']+)["\']'
    match_lt = re.search(lt_pattern, response_text)
    match_exec = re.search(exec_pattern, response_text)
    if match_lt:
        lt_value = match_lt.group(1)
    if match_exec:
        execution_value = match_exec.group(1)
    return lt_value, execution_value


def _get_form_action(response_text, base_url):
    """从 HTML 提取 form action,相对路径拼接成绝对路径"""
    action_pattern = r'<form[^>]*action=["\']([^"\']+)["\']'
    match_action = re.search(action_pattern, response_text)
    if match_action:
        form_action = match_action.group(1)
        if not form_action.startswith("http"):
            form_action = urllib.parse.urljoin(base_url, form_action)
        return form_action
    return base_url


def create_session(username, password, test_url):
    """创建并认证 Jira 会话 (CAS 单点登录)

    Args:
        username: Jira 用户名
        password: Jira 密码
        test_url: 用于验证登录的 Jira 测试 URL (如 https://xxx/browse/ADAAFTI-1)

    Returns:
        认证成功的 requests.Session, 失败返回 None
    """
    session = requests.Session()
    session.verify = False
    session.headers.update({"User-Agent": USER_AGENT})

    try:
        response = session.get(test_url, timeout=30, allow_redirects=True)

        # 已经登录 (URL 不含 login)
        if "login" not in response.url.lower():
            return session

        login_page_url = response.url
        lt_value, execution_value = _extract_lt_exec(response.text)
        form_action = _get_form_action(response.text, login_page_url)

        login_data = {
            "username": username,
            "password": password,
            "_eventId": "submit",
        }
        if lt_value:
            login_data["lt"] = lt_value
        if execution_value:
            login_data["execution"] = execution_value

        session.post(form_action, data=login_data, timeout=30, allow_redirects=True)

        test_response = session.get(test_url, timeout=30)
        if "login" not in test_response.url.lower():
            return session

        # CAS 重试 (部分场景需要二次登录)
        if "cas" in test_response.url.lower():
            retry_response = session.get(test_url, timeout=30, allow_redirects=True)
            lt_value, execution_value = _extract_lt_exec(retry_response.text)
            if lt_value and execution_value:
                login_data = {
                    "username": username,
                    "password": password,
                    "lt": lt_value,
                    "execution": execution_value,
                    "_eventId": "submit",
                }
                retry_action = _get_form_action(retry_response.text, retry_response.url)
                session.post(retry_action, data=login_data, timeout=30, allow_redirects=True)
                final_test = session.get(test_url, timeout=30)
                if "login" not in final_test.url.lower():
                    return session

        return None
    except Exception:
        return None


def create_session_with_cookies(cookies_dict):
    """用已有 cookies 创建会话 (多线程场景共享登录态)"""
    session = requests.Session()
    session.verify = False
    session.headers.update({"User-Agent": USER_AGENT})
    for name, value in cookies_dict.items():
        session.cookies.set(name, value)
    return session
