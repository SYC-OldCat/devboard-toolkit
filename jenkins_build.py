"""Jenkins 自动编译感知包 (http://172.17.189.18:8080)

功能:
  1. 列所有 Job (5 个感知包编译任务)
  2. 查看 Job 参数 (BRANCH / SDK_ZIP)
  3. 触发构建 (带参数 + 可选 SDK_ZIP 文件上传)
  4. 等队列分配 build# → 轮询构建完成
  5. 列产物 → 下载到共享目录 pkgs → 可选自动解压
  6. 衔接 batch_replay(感知包名自动填入)

命令行入口: python run.py --auto-build
"""

import os
import sys
import time
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
from requests.auth import HTTPBasicAuth

# 允许从脚本直接运行(也被 run.py 调用)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devboard_toolkit.config import load_jenkins


# ============================================================
# 底层 REST API 封装
# ============================================================

class JenkinsClient:
    """轻量 Jenkins REST API 客户端"""

    def __init__(self, cfg: Dict[str, Any]):
        self.server = cfg["server"].rstrip("/")
        self.auth = HTTPBasicAuth(cfg["username"], cfg["password"])
        self.timeout = 15
        self.s = requests.Session()
        self.s.auth = self.auth

    # ---------- 基础请求 ----------
    def _get(self, path: str, **kw) -> requests.Response:
        url = f"{self.server}{path}"
        r = self.s.get(url, timeout=kw.pop("timeout", self.timeout), **kw)
        r.raise_for_status()
        return r

    def _get_json(self, path: str, **kw) -> Any:
        return self._get(path, **kw).json()

    def _post(self, path: str, **kw) -> requests.Response:
        url = f"{self.server}{path}"
        # Jenkins CSRF: 新 Jenkins 默认需要 crumb; 尝试取一下,失败就跳过(很多实例不强制)
        # 注意: multipart/form-data 上传文件时,绝对不能手动设 Content-Type,
        #       requests 会自动生成带 boundary 的正确值
        headers = kw.pop("headers", {}) or {}
        crumb = self._get_crumb()
        if crumb:
            headers.update(crumb)
        # 只在不是上传文件(无 files)时才允许覆盖 Content-Type
        if "files" not in kw or not kw["files"]:
            # headers 为空就不传,让 requests 自动处理
            if headers:
                kw["headers"] = headers
        else:
            # 上传文件: 仅保留 crumb,绝不传 Content-Type
            filtered = {k: v for k, v in headers.items()
                        if k.lower() not in ("content-type",)}
            if filtered:
                kw["headers"] = filtered
        r = self.s.post(url, timeout=kw.pop("timeout", self.timeout), **kw)
        if r.status_code >= 400:
            # 打印响应体,方便定位 4xx 具体原因
            body = r.text[:2000]
            sys.stderr.write(
                f"\n[Jenkins HTTP {r.status_code} 响应体]: {body}\n"
                f"[URL] {url}\n"
            )
            sys.stderr.flush()
        r.raise_for_status()
        return r

    def _get_crumb(self) -> Dict[str, str]:
        """拿 Jenkins CSRF crumb; 拿不到就返回空 dict"""
        try:
            data = self._get_json("/crumbIssuer/api/json")
            return {data["crumbRequestField"]: data["crumb"]}
        except Exception:
            return {}

    # ---------- Job ----------
    def list_jobs(self) -> List[Dict[str, Any]]:
        """列出所有 Job (name/color/lastBuild 号+结果)"""
        data = self._get_json(
            "/api/json?tree=jobs[name,color,url,"
            "lastBuild[number,result,timestamp,duration],"
            "lastSuccessfulBuild[number,result]]"
        )
        jobs = data.get("jobs", [])
        # 按名称排序,感知包 Job 放前面
        jobs.sort(key=lambda j: j.get("name", ""))
        return jobs

    def get_job_params(self, job_name: str) -> List[Dict[str, Any]]:
        """取 Job 参数定义 (parameterDefinitions)"""
        encoded = requests.utils.quote(job_name, safe="")
        data = self._get_json(
            f"/job/{encoded}/api/json?tree=property["
            "parameterDefinitions[name,type,defaultParameterValue[value],"
            "description,choices]]"
        )
        params = []
        for prop in data.get("property", []):
            if "parameterDefinitions" in prop:
                for p in prop["parameterDefinitions"]:
                    params.append({
                        "name": p.get("name", ""),
                        "type": p.get("type", ""),
                        "default": (p.get("defaultParameterValue") or {}).get("value"),
                        "description": p.get("description", ""),
                        "choices": p.get("choices", []),
                    })
        return params

    # ---------- 触发构建 ----------
    def trigger_build(self, job_name: str,
                      params: Dict[str, str],
                      sdk_zip_path: Optional[str] = None) -> Optional[str]:
        """触发构建,返回 Jenkins 队列 item URL(包含 ID)

        Jenkins 先把任务放进队列,再分配真正的 build#,
        所以需要拿 queue item id 去等 build# 出来。
        """
        encoded = requests.utils.quote(job_name, safe="")

        if sdk_zip_path and os.path.isfile(sdk_zip_path):
            # 有文件上传 → 用 buildWithParameters + multipart/form-data
            # Jenkins 文件参数: field 名必须和 Job 里定义的参数名一致(SDK_ZIP)
            # 文本参数和文件都通过 files dict 传(requests 会合并成一个 multipart form)
            files = {}
            file_handles = []
            # 文本参数(用 (None, "值") 表示表单普通字段,和文件混合)
            for k, v in params.items():
                if v is not None and v != "":
                    files[k] = (None, str(v))
            # 文件参数
            f = open(sdk_zip_path, "rb")
            file_handles.append(f)
            files["SDK_ZIP"] = (os.path.basename(sdk_zip_path), f, "application/zip")
            try:
                r = self._post(f"/job/{encoded}/buildWithParameters", files=files)
            finally:
                for fh in file_handles:
                    try:
                        fh.close()
                    except Exception:
                        pass
        else:
            # 纯参数 → buildWithParameters (application/x-www-form-urlencoded)
            data = {k: v for k, v in params.items() if v is not None and v != ""}
            r = self._post(f"/job/{encoded}/buildWithParameters", data=data)

        # 拿 Location: /queue/item/XXXX/
        loc = r.headers.get("Location", "")
        if not loc:
            return None
        # Location 可能是绝对或相对路径,取最后的数字 ID
        parts = [p for p in loc.strip("/").split("/") if p]
        if parts and parts[-1].isdigit():
            return parts[-1]
        return None

    # ---------- 队列 → build# ----------
    def wait_build_start(self, queue_id: str,
                         interval: int = 3,
                         timeout: int = 300) -> Optional[int]:
        """等队列被执行拿到真正的 build#"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                data = self._get_json(f"/queue/item/{queue_id}/api/json")
                exe = data.get("executable")
                if exe and exe.get("number"):
                    return int(exe["number"])
                # 是否被取消
                if data.get("cancelled"):
                    return None
            except Exception:
                pass
            time.sleep(interval)
        return None

    # ---------- 轮询构建完成 ----------
    def get_build_status(self, job_name: str, build_num: int) -> Dict[str, Any]:
        encoded = requests.utils.quote(job_name, safe="")
        return self._get_json(
            f"/job/{encoded}/{build_num}/api/json?tree="
            "number,result,building,duration,estimatedDuration,timestamp,url"
        )

    def wait_build_done(self, job_name: str, build_num: int,
                        interval: int = 10,
                        timeout: int = 7200) -> str:
        """轮询直到构建完成,返回 SUCCESS / FAILURE / ABORTED / TIMEOUT"""
        start = time.time()
        while time.time() - start < timeout:
            st = self.get_build_status(job_name, build_num)
            building = st.get("building", False)
            result = st.get("result")
            dur = st.get("duration", 0) / 1000.0
            est = st.get("estimatedDuration", 0) / 1000.0
            now = time.strftime("%H:%M:%S", time.localtime())
            if building:
                if est > 0:
                    pct = min(100, int(dur / est * 100))
                    sys.stdout.write(
                        f"\r  {now}  构建中  已用时 {dur:.0f}s / 预计 {est:.0f}s ({pct}%)   "
                    )
                else:
                    sys.stdout.write(
                        f"\r  {now}  构建中  已用时 {dur:.0f}s   "
                    )
                sys.stdout.flush()
            else:
                print()
                if result:
                    return result
                return "UNKNOWN"
            time.sleep(interval)
        print()
        return "TIMEOUT"

    # ---------- 产物 ----------
    def list_artifacts(self, job_name: str, build_num: int) -> List[Dict[str, Any]]:
        """列出编译产物(按 size 降序,通常最大的就是感知包包体)"""
        encoded = requests.utils.quote(job_name, safe="")
        data = self._get_json(
            f"/job/{encoded}/{build_num}/api/json?tree=artifacts[fileName,relativePath,size]"
        )
        arts = data.get("artifacts", [])
        arts.sort(key=lambda x: x.get("size", 0), reverse=True)
        return arts

    def download_artifact(self, job_name: str, build_num: int,
                          rel_path: str, out_path: str,
                          show_progress: bool = True) -> str:
        """分块下载产物到 out_path, 可显示进度

        Returns:
            输出文件的绝对路径
        """
        encoded_job = requests.utils.quote(job_name, safe="")
        encoded_path = requests.utils.quote(rel_path, safe="")
        url = f"{self.server}/job/{encoded_job}/{build_num}/artifact/{encoded_path}"

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        with self.s.get(url, stream=True, timeout=30, auth=self.auth) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            got = 0
            t0 = time.time()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)
                    got += len(chunk)
                    if show_progress and total > 0:
                        pct = got / total * 100
                        elapsed = max(0.01, time.time() - t0)
                        speed = got / elapsed / (1024 * 1024)  # MB/s
                        got_mb = got / (1024 * 1024)
                        tot_mb = total / (1024 * 1024)
                        bar_len = 30
                        filled = int(bar_len * got // total)
                        bar = "█" * filled + "░" * (bar_len - filled)
                        sys.stdout.write(
                            f"\r  下载 {bar} {pct:5.1f}%  {got_mb:.2f}/{tot_mb:.2f} MB  {speed:.2f} MB/s   "
                        )
                        sys.stdout.flush()
        if show_progress:
            print()
        return os.path.abspath(out_path)


# ============================================================
# 工具函数
# ============================================================

def _fmt_size(sz: int) -> str:
    if sz is None:
        return "?"
    for unit in ["B", "KB", "MB", "GB"]:
        if sz < 1024:
            return f"{sz:.2f} {unit}"
        sz /= 1024
    return f"{sz:.2f} TB"


def _color_to_status(color: str) -> str:
    """Jenkins color(blue/green/red/yellow/aborted/anime) → 中文状态"""
    mapping = {
        "blue": "✓成功",
        "blue_anime": "⟳构建中",
        "green": "✓成功",
        "green_anime": "⟳构建中",
        "red": "✗失败",
        "red_anime": "⟳失败重试",
        "yellow": "⚠不稳定",
        "yellow_anime": "⟳不稳定中",
        "aborted": "✕中止",
        "aborted_anime": "⟳中止中",
        "notbuilt": "-未构建",
        "disabled": "⊘禁用",
        "grey": "？未知",
    }
    return mapping.get(color or "", color or "")


def _extract_artifact(file_path: str, out_dir: Optional[str] = None) -> str:
    """自动识别并解压 tar.gz/tar/zip

    Returns:
        解压后的目录路径
    """
    fp = Path(file_path)
    if out_dir is None:
        # 同名目录(去掉 .tar.gz/.tgz/.zip 等)
        name = fp.name
        for suf in [".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"]:
            if name.lower().endswith(suf):
                name = name[:-len(suf)]
                break
        else:
            for suf in [".zip", ".tar"]:
                if name.lower().endswith(suf):
                    name = name[:-len(suf)]
                    break
        out_dir = str(fp.with_name(name))

    # 先清空旧产物 (防止不同版本 .so / config 残留互相污染, 和 runtime 策略一致)
    import shutil as _shutil_art
    if os.path.exists(out_dir):
        try:
            _shutil_art.rmtree(out_dir)
        except Exception:
            # 目录被占用等情况退化为文件级覆盖, 不阻塞主流程
            pass
    os.makedirs(out_dir, exist_ok=True)
    name_lower = fp.name.lower()

    if name_lower.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar")):
        with tarfile.open(file_path, "r:*") as tf:
            tf.extractall(out_dir)
    elif name_lower.endswith(".zip"):
        with zipfile.ZipFile(file_path, "r") as zf:
            zf.extractall(out_dir)
    else:
        # 不认识的格式直接跳过解压
        return file_path
    return out_dir


def _prompt(msg: str, default: str = "") -> str:
    try:
        if default:
            r = input(f"{msg} [{default}]: ").strip()
            return r if r else default
        return input(f"{msg}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default


# ============================================================
# 感知包识别工具函数
# ============================================================

def _extract_prefix(zip_name: str) -> str:
    """从感知包文件名提取前缀(如 25640-2)

    规则:
      #25640-2_D3-4_ARCSOFT_...  → 25640-2
      23455-40_D3-4_...          → 23455-40

    步骤: 去掉路径/#, 取第一个 _ 之前的部分
    """
    base = os.path.basename(zip_name)
    # 去掉 # 前缀
    if base.startswith("#"):
        base = base[1:]
    # 取第一个 _ 之前
    if "_" in base:
        return base.split("_", 1)[0]
    return base


def _extract_build_version(zip_name: str) -> str:
    """从感知包文件名提取编译版本号(如 2244)

    格式: ..._3.1.27223.2244_...  → 提取 2244
    规则: 找 3.1.27223.XXXX 模式,取最后的 XXXX

    类型A: #25640-2_D3-4_ARCSOFT_ADAS_3.1.27223.2244_LINUX_...  → 2244
    类型B: #25640-2_D3-4_ADAS_3.1.27223.2244_08042026_...       → 2244
    """
    import re
    # 匹配 3.1.NNNNN.XXXX 格式,取 XXXX
    m = re.search(r'3\.1\.\d+\.(\d+)', zip_name)
    if m:
        return m.group(1)
    return ""


def _match_job(jobs: List[Dict[str, Any]], prefix: str) -> Optional[Dict[str, Any]]:
    """用前缀匹配 Jenkins Job

    匹配规则: Job name 以 "前缀_" 开头
    例: prefix="25640-2" → 匹配 "25640-2_PDT_Perception_Testbed_V3.1.4_Linux_Lq560v200"

    Returns:
        匹配到的 Job dict, 或 None(未匹配/多个匹配时也返回None让用户手选)
    """
    if not prefix:
        return None
    matches = [j for j in jobs if j.get("name", "").startswith(f"{prefix}_")]
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_sdk_zip(zip_path: str) -> Optional[str]:
    """识别感知包类型,返回最终要上传的 SDK zip 路径

    类型A — 直接可编译:
      文件名含 ARCSOFT 且 SDK → 直接返回 zip_path

    类型B — 外层包需解压取内层:
      文件名不含 ARCSOFT/SDK → 解压外层 zip 到临时目录,
      在里面找 .zip 文件:
        - 含 LOG → 跳过
        - 含 SDK → 这是要上传的内层包
      返回内层 SDK zip 的完整路径

    Returns:
        最终要上传的 zip 路径, 或 None(识别失败)
    """
    if not os.path.isfile(zip_path):
        return None

    fname = os.path.basename(zip_path)
    fname_upper = fname.upper()

    # 类型A: 文件名含 ARCSOFT 且 SDK → 直接用
    if "ARCSOFT" in fname_upper and "SDK" in fname_upper:
        return zip_path

    # 类型B: 需要解压外层包,找内层 SDK zip
    import tempfile

    print(f"  → 类型B(外层包),解压查找内层 SDK zip ...")
    tmp_dir = tempfile.mkdtemp(prefix="jenkins_sdk_")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)
        # 在解压目录里找 .zip 文件(递归)
        import glob
        inner_zips = glob.glob(os.path.join(tmp_dir, "**", "*.zip"), recursive=True)
        sdk_zip = None
        for iz in sorted(inner_zips):
            iz_name = os.path.basename(iz)
            iz_upper = iz_name.upper()
            if "LOG" in iz_upper:
                print(f"  → 跳过(日志包): {iz_name}")
                continue
            if "SDK" in iz_upper:
                if sdk_zip is None:
                    sdk_zip = iz
                    print(f"  → 找到 SDK zip: {iz_name} ({_fmt_size(os.path.getsize(iz))})")
                else:
                    print(f"  ! 多个 SDK zip,跳过: {iz_name}")
        if sdk_zip:
            return sdk_zip
        print(f"  ! 未在内层找到 SDK zip")
        return None
    except Exception as e:
        print(f"  ! 解压外层包失败: {e}")
        return None


def _extract_runtime(sdk_zip_path: str, dest_dir: str) -> str:
    """从 SDK zip 中提取 runtime 文件夹到 dest_dir/runtime/

    SDK zip 内部结构:
      SDK/lib/runtime/  → 复制到 dest_dir/runtime/

    Returns:
        runtime 目标路径, 或 "" 表示失败
    """
    import shutil

    if not os.path.isfile(sdk_zip_path):
        print(f"  ! SDK zip 不存在: {sdk_zip_path}")
        return ""

    # 可能的前缀
    prefixes = ["SDK/lib/runtime/", "lib/runtime/"]
    found_prefix = None

    try:
        with zipfile.ZipFile(sdk_zip_path, "r") as zf:
            names = zf.namelist()
            # 找到 runtime 文件夹前缀
            for pfx in prefixes:
                if any(n.startswith(pfx) for n in names):
                    found_prefix = pfx
                    break

            if not found_prefix:
                print(f"  ! SDK zip 内未找到 runtime 文件夹 (查找过: {prefixes})")
                return ""

            # 复制 runtime 下所有文件到 dest_dir/runtime/
            runtime_dest = os.path.join(dest_dir, "runtime")
            # 如果已存在旧 runtime,先删除再复制,确保干净覆盖
            if os.path.exists(runtime_dest):
                shutil.rmtree(runtime_dest, ignore_errors=True)
                print(f"  [i] 已删除旧 runtime 目录: {runtime_dest}")
            os.makedirs(runtime_dest, exist_ok=True)
            count = 0
            for name in names:
                if not name.startswith(found_prefix):
                    continue
                rel = name[len(found_prefix):]
                if not rel:
                    continue
                target = os.path.join(runtime_dest, rel)
                # 规范化路径分隔符
                target = target.replace("\\", "/")
                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    count += 1

            print(f"  [✓] runtime 已复制到: {runtime_dest} ({count} 个文件)")
            return runtime_dest
    except Exception as e:
        print(f"  ! 提取 runtime 失败: {e}")
        return ""


# ============================================================
# 主交互流程 (6 步)
# ============================================================

def auto_build_main(replay_dir: str = None, sdk_zip_path: str = None) -> tuple:
    """Jenkins 自动编译主流程

    Args:
        replay_dir: 回灌目录 UNC 路径。如果提供:
            - 编译产物下载到此目录(而非默认 download_dir)
            - 自动从 SDK zip 提取 runtime 到此目录
            - 跳过 [6/6] 衔接回灌的询问,直接返回 app_name
        sdk_zip_path: 感知包 zip 路径。如果提供(全流程模式),跳过 [1/6] 的交互输入

    Returns:
        (app_name, return_code)
        - 成功: (感知包名, 0)
        - 失败: (None, 1)
    """
    cfg = load_jenkins()
    client = JenkinsClient(cfg)
    default_job = cfg.get("default_job", "")
    download_dir = cfg.get("download_dir", r"\\hz-iotfs02\Model_Test\TestSpace\Personal_Space\SYC\testbed\pkgs")

    # 如果传了 replay_dir,产物和 runtime 都放到回灌目录
    if replay_dir:
        download_dir = replay_dir

    print("=" * 64)
    print(f"  Jenkins 自动编译感知包  ({cfg['server']})")
    print("=" * 64)

    # ---- [1/6] 输入感知包 → 自动匹配 Job + 解析 SDK zip ----
    print("\n[1/6] 输入感知包路径:")
    # 如果调用方已传入 sdk_zip_path(全流程模式),跳过交互输入
    if sdk_zip_path:
        raw_input_path = sdk_zip_path.strip('"').strip("'")
        print(f"  感知包 zip 路径: {raw_input_path}  (已传入)")
    else:
        raw_input_path = _prompt("  感知包 zip 路径(支持类型A:含ARCSOFT+SDK / 类型B:外层包)")
        if not raw_input_path:
            print("[!] 未输入路径")
            return (None, 1)
        # 去掉首尾引号
        raw_input_path = raw_input_path.strip('"').strip("'")
    if not os.path.isfile(raw_input_path):
        print(f"[!] 文件不存在: {raw_input_path}")
        return (None, 1)

    # 1a. 提取前缀,自动匹配 Job
    prefix = _extract_prefix(raw_input_path)
    print(f"  → 提取前缀: {prefix}")

    try:
        jobs = client.list_jobs()
    except Exception as e:
        print(f"[!] 连不上 Jenkins: {e}")
        print("    请检查网络 / server 配置 / 账号密码 (config.yaml jenkins)")
        return (None, 1)

    job = _match_job(jobs, prefix)
    if job:
        print(f"  → 自动匹配 Job: {job['name']}")
    else:
        # 匹配失败 → 手动选
        print(f"  ! 前缀 '{prefix}' 未匹配到唯一 Job,请手动选择:")
        default_idx = 0
        for i, j in enumerate(jobs, 1):
            name = j.get("name", "")
            mark = " [默认]" if name == default_job else ""
            if name == default_job:
                default_idx = i
            last_build = j.get("lastBuild") or {}
            lb_num = last_build.get("number", "-")
            lb_res = (last_build.get("result") or "-") if not j.get("color", "").endswith("_anime") else "-"
            lb_res_cn = "✓" if lb_res == "SUCCESS" else (
                "✗" if lb_res == "FAILURE" else "?" if lb_res == "-" else lb_res
            )
            status_cn = _color_to_status(j.get("color", ""))
            print(f"  {i:>2}) {name:<62s}  {status_cn:<8s}  上次#{lb_num}{lb_res_cn}{mark}")
        choice = _prompt(f"\n选择编号", str(default_idx))
        try:
            job = jobs[int(choice) - 1]
        except Exception:
            job = next((x for x in jobs if x["name"] == default_job), jobs[0] if jobs else None)
        if not job:
            print("[!] 没有可用 Job")
            return (None, 1)
    job_name = job["name"]
    print(f"  → 已选 Job: {job_name}")

    # 1b. 识别感知包类型,解析出最终要上传的 SDK zip
    sdk_zip_path = _resolve_sdk_zip(raw_input_path)
    if not sdk_zip_path:
        print("[!] 无法从输入包中识别 SDK zip,请检查文件")
        return (None, 1)
    print(f"  → 上传 SDK zip: {os.path.basename(sdk_zip_path)} ({_fmt_size(os.path.getsize(sdk_zip_path))})")

    # ---- [2/6] 填参数 (SDK_ZIP 已自动填入,文本参数有默认值跳过) ----
    print(f"\n[2/6] 构建参数:")
    try:
        params_def = client.get_job_params(job_name)
    except Exception as e:
        print(f"[!] 读取参数失败: {e}")
        params_def = []

    build_params: Dict[str, str] = {}

    for p in params_def:
        pname = p.get("name", "")
        ptype = p.get("type", "")
        default_val = p.get("default")
        desc = p.get("description", "")
        if desc:
            print(f"  # {desc}")

        if "File" in ptype or "file" in ptype.lower():
            # SDK_ZIP: 已经在 [1/6] 自动解析填入,跳过交互
            print(f"  {pname:<12s} = {os.path.basename(sdk_zip_path)}  (已自动填入)")
            continue

        # 普通文本参数: 有默认值时直接用默认值,不再提示敲回车
        def_val_str = ""
        if default_val is not None:
            def_val_str = str(default_val)
        choices = p.get("choices") or []
        if choices:
            print(f"    可选值: {', '.join(choices)}")
        if def_val_str:
            # 有默认 → 直接用
            build_params[pname] = def_val_str
            print(f"  {pname:<12s} = {def_val_str}  (默认值,跳过输入)")
        else:
            # 无默认 → 提示输入
            build_params[pname] = _prompt(f"  {pname:<12s}")

    # ---- [3/6] 触发构建 ----
    print(f"\n[3/6] 触发构建 ...")
    try:
        queue_id = client.trigger_build(job_name, build_params, sdk_zip_path)
    except Exception as e:
        print(f"[!] 触发失败: {e}")
        return (None, 1)
    if not queue_id:
        print("[!] 触发成功但拿不到队列 ID,请手动去 Jenkins 页面查看")
        return (None, 1)

    print(f"  → 已入队列 queue#{queue_id}")

    # ---- [4/6] 等启动 & 轮询构建完成 ----
    print(f"\n[4/6] 等待分配 build# (最多 5 分钟)...")
    build_num = client.wait_build_start(queue_id, interval=3, timeout=300)
    if not build_num:
        print("[!] 超时,队列中的任务一直没开始(可能 Jenkins 节点忙)")
        try:
            input("按回车退出...")
        except EOFError:
            pass
        return (None, 1)
    print(f"  [✓] 已开始 Build #{build_num}")

    result = client.wait_build_done(job_name, build_num, interval=10, timeout=7200)
    if result == "SUCCESS":
        print(f"  [✓] Build #{build_num} SUCCESS")
    else:
        print(f"  [✗] Build #{build_num} {result}")
        print("      请去 Jenkins 页面查看日志定位原因")
        try:
            input("按回车退出...")
        except EOFError:
            pass
        return (None, 1)

    # ---- [5/6] 自动匹配并下载产物 ----
    print(f"\n[5/6] 编译产物:")
    try:
        arts = client.list_artifacts(job_name, build_num)
    except Exception as e:
        print(f"[!] 读产物列表失败: {e}")
        return (None, 1)
    if not arts:
        print("  ! 本次构建没有产物(可能编译脚本没归档)")
        try:
            input("按回车退出...")
        except EOFError:
            pass
        return (None, 1)

    # 列出所有产物
    for i, a in enumerate(arts, 1):
        size = _fmt_size(a.get("size", 0))
        rpath = a.get("relativePath", a.get("fileName", ""))
        print(f"  {i:>2}) {size:<12s}  {rpath}")

    # 从用户上传的 SDK zip 文件名提取版本号(如 2244)
    # 格式: ..._3.1.27223.2244_...  → 提取 2244
    sdk_basename = os.path.basename(sdk_zip_path) if sdk_zip_path else ""
    build_version = _extract_build_version(sdk_basename)

    # 自动匹配: 产物文件名里含相同版本号的
    chosen_art = None
    if build_version:
        for a in arts:
            fname = a.get("fileName", "")
            if build_version in fname:
                chosen_art = a
                print(f"\n  → 自动匹配版本号 {build_version}: {fname}")
                break

    if not chosen_art:
        # 匹配失败 → 手动选
        if build_version:
            print(f"\n  ! 未匹配到版本号 {build_version} 的产物,请手动选择")
        else:
            print(f"\n  ! 未能从 SDK 文件名提取版本号,请手动选择")
        choice = _prompt(f"  下载编号", "1")
        try:
            chosen_art = arts[int(choice) - 1]
        except Exception:
            chosen_art = arts[0]

    rel_path = chosen_art.get("relativePath", chosen_art.get("fileName", ""))
    out_file = os.path.join(download_dir, os.path.basename(rel_path) or f"artifact_build_{build_num}")
    try:
        local_path = client.download_artifact(job_name, build_num, rel_path, out_file)
    except Exception as e:
        print(f"[!] 下载失败: {e}")
        return (None, 1)
    print(f"  [✓] 已保存: {local_path}")

    # 自动解压
    app_folder = ""
    try:
        extracted = _extract_artifact(local_path)
        if extracted != local_path:
            app_folder = extracted
            print(f"  [✓] 自动解压到: {extracted}")
    except Exception as e:
        print(f"  ! 自动解压失败,请手动解压: {e}")

    # 尝试找到感知包可执行文件路径(用于后续 batch_replay 自动填)
    app_name = _guess_app_name(app_folder or local_path)

    # ---- 提取 runtime 到回灌目录(仅当提供 replay_dir) ----
    if replay_dir:
        print(f"\n  → 从 SDK zip 提取 runtime 到回灌目录...")
        runtime_path = _extract_runtime(sdk_zip_path, replay_dir)
        if not runtime_path:
            print("  ! runtime 提取失败,回灌可能无法启动,请手动复制")
            return (None, 1)

    # ---- [6/6] 衔接批量回灌 ----
    # 当提供 replay_dir(全自动模式)时,直接返回 app_name,由调用方衔接回灌
    if replay_dir:
        print(f"\n[✓] 自动编译完成,感知包名: {app_name or '(未识别)'}")
        return (app_name, 0)

    print(f"\n[6/6] 是否衔接批量回灌? [Y/n]")
    try:
        go_on = input().strip().lower()
    except EOFError:
        go_on = "n"
    if go_on in ("y", ""):
        print(f"  感知包名: {app_name or '(未识别,请在回灌流程手动输入)'}")
        print()
        # 进入 batch_replay 流程,把 app_name 传进去
        from devboard_toolkit.batch_replay import batch_replay_main
        rc = batch_replay_main(pre_filled_app=app_name or None)
        return (app_name, rc if isinstance(rc, int) else 0)
    else:
        print(f"  OK, 产物已在: {local_path}")
        if app_name:
            print(f"  下次回灌时,感知包名填: {app_name}")
    return (app_name, 0)


def _guess_app_name(artifact_path: str) -> str:
    """从解压后的目录里猜感知包名 (NH_ADAS_PERCEPTION_ 开头的可执行文件).

    优先级:
      1. 解压目录内直接有 NH_ADAS_PERCEPTION_* 可执行文件
      2. 文件名去掉后缀后是 NH_ADAS_PERCEPTION_* (比如 tar.gz 的根名)
    """
    import glob
    name = ""
    if artifact_path and os.path.isdir(artifact_path):
        # 目录里找 NH_ADAS_PERCEPTION_* 文件(非文件夹)
        for p in sorted(glob.glob(os.path.join(artifact_path, "NH_ADAS_PERCEPTION_*"))):
            if os.path.isfile(p):
                return os.path.basename(p)
        # 没找到就找子目录
        for p in sorted(glob.glob(os.path.join(artifact_path, "**", "NH_ADAS_PERCEPTION_*"), recursive=True)):
            if os.path.isfile(p):
                return os.path.basename(p)
    # 用文件名推
    base = os.path.basename(artifact_path)
    if "NH_ADAS_PERCEPTION_" in base:
        # 去后缀
        for suf in [".tar.gz", ".tgz", ".zip", ".tar"]:
            if base.lower().endswith(suf):
                base = base[:-len(suf)]
                break
        name = base
    return name


if __name__ == "__main__":
    _app, _rc = auto_build_main()
    sys.exit(_rc if isinstance(_rc, int) else 1)
