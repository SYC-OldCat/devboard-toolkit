"""开发板配置加载

配置源(优先级从高到低,后者覆盖前者同 name 条目):
1. 内置 DEFAULT_BOARDS(兜底,源码硬编码)
2. 项目目录 config.yaml(主配置,含 boards + mount + 阈值)
3. 用户级 ~/.devboard_toolkit/config.json(可选覆盖)
4. 项目目录 config.json(可选覆盖)
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ===== 内置默认板(兜底,config.yaml 不存在时用) =====
DEFAULT_BOARDS: Dict[str, Dict[str, Any]] = {
    "board1": {"host": "172.17.188.122", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "board2": {"host": "172.17.188.189", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "board3": {"host": "172.17.188.241", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "board4": {"host": "172.17.188.71", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 15},
    "board5": {"host": "172.17.189.31", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "board6": {"host": "172.17.189.36", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "Online1": {"host": "172.17.188.246", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "Online2": {"host": "172.17.188.248", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "Online3": {"host": "172.17.189.1", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "Online4": {"host": "172.17.188.247", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "Online5": {"host": "172.17.189.12", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "Online6": {"host": "172.17.189.13", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
    "Online7": {"host": "172.17.189.16", "port": 22, "user": "root", "password": "arcsoft123", "timeout": 8},
}

# 内置默认回灌环境(兜底)
DEFAULT_REPLAY_ENV: Dict[str, Any] = {
    "mount_source": "//172.17.12.118/Model_Test/TestSpace/Personal_Space",
    "mount_point": "/mnt",
    "mount_options": "username=syc53636,password=@Ss19855468498,domain=arcsoft-hz",
    "testbed_subpath": "SYC/testbed",
    "windows_host": "hz-iotfs02",
}

# 内置默认 Jenkins 配置(兜底)
DEFAULT_JENKINS: Dict[str, Any] = {
    "server": "http://172.17.189.18:8080",
    "username": "qatest",
    "password": "qatest",
    "download_dir": r"\\hz-iotfs02\Model_Test\TestSpace\Personal_Space\SYC\testbed\pkgs",
    "default_job": "25640-2_PDT_Perception_Testbed_V3.1.4_Linux_Lq560v200",
}

# 内置默认 Jira 数据预处理配置(兜底)
DEFAULT_JIRA_DATA: Dict[str, Any] = {
    "base_url": "https://jira.arcsoft.com.cn:8443",
    "test_url": "https://jira.arcsoft.com.cn:8443/browse/ADAAFTI-1",
    "username": "syc53636",
    "password": "@Ss19855468498",
    # Jira 模式默认并发数
    "max_workers": 5,
}

# 内置默认 ADAS 预处理工具配置(兜底)
# exe_path 默认指向原项目位置(大体积外部工具,不搬进本仓库)
DEFAULT_ADAS: Dict[str, Any] = {
    "exe_path": r"D:\Desktop\Data_preprocessing\sdk_data_preprocessing\ADAS_Visualization\ADAS_Visualization.exe",
    "car_type": 3,
    "generate_mcap": False,
    "timeout": 300,
    "max_workers": 4,
}


def _merge(a: Dict, b: Dict) -> Dict:
    """同 key 的配置用 b 覆盖 a"""
    out = dict(a)
    for k, v in b.items():
        out[k] = v
    return out


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists() or not _HAS_YAML:
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_project_root() -> str:
    """获取项目根目录

    exe 打包后: 返回 exe 所在目录 (config.yaml / tool/ 在 exe 旁边)
    开发模式: 返回 devboard_toolkit 包的上级目录
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _yaml_path() -> Path:
    """config.yaml 搜索路径:项目目录"""
    return Path(get_project_root()) / "config.yaml"


def load_boards() -> Dict[str, Dict[str, Any]]:
    """加载开发板配置

    优先级(从低到高, 后者整体替换前者):
    1. 内置 DEFAULT_BOARDS (兜底, config.yaml 不存在或没有 boards 段时用)
    2. config.yaml 的 boards 段 (存在则**整体替换** DEFAULT_BOARDS, 不再合并,
       因为用户在 GUI 设置里删板时, 删掉的板不应再从 DEFAULT_BOARDS 补回来)
    3. ~/.devboard_toolkit/config.json 的 boards 段 (继续整体替换)
    4. 项目目录 config.json 的 boards 段 (继续整体替换)
    """
    yaml_data = _load_yaml(_yaml_path())

    # 2. config.yaml 的 boards 段存在 → 整体替换 DEFAULT_BOARDS
    if "boards" in yaml_data and yaml_data["boards"]:
        boards = {k: dict(v) for k, v in yaml_data["boards"].items() if v}
    else:
        boards = dict(DEFAULT_BOARDS)

    # 3. 用户级 JSON (整体替换 boards 段, 不存在则保持当前)
    user_cfg = Path.home() / ".devboard_toolkit" / "config.json"
    user_data = _load_json(user_cfg)
    if "boards" in user_data and user_data["boards"]:
        boards = {k: dict(v) for k, v in user_data["boards"].items() if v}
    elif user_data and not isinstance(next(iter(user_data.values())), dict):
        # 兼容旧格式 (顶层直接是 boards, 且至少一个值不是 dict 才判定为旧)
        # 实际上旧格式 key=board_name, value=cfg dict, 所以这里 skip
        pass

    # 4. 项目级 JSON (整体替换 boards 段)
    project_cfg = Path.cwd() / "config.json"
    proj_data = _load_json(project_cfg)
    if "boards" in proj_data and proj_data["boards"]:
        boards = {k: dict(v) for k, v in proj_data["boards"].items() if v}

    return boards


def load_replay_env() -> Dict[str, Any]:
    """加载回灌环境(挂载 + testbed 路径)"""
    cfg = dict(DEFAULT_REPLAY_ENV)
    yaml_data = _load_yaml(_yaml_path())
    if "replay_env" in yaml_data:
        cfg = _merge(cfg, yaml_data["replay_env"])
    return cfg


def load_jenkins() -> Dict[str, Any]:
    """加载 Jenkins 编译平台配置"""
    cfg = dict(DEFAULT_JENKINS)
    yaml_data = _load_yaml(_yaml_path())
    if "jenkins" in yaml_data:
        cfg = _merge(cfg, yaml_data["jenkins"])
    return cfg


def load_jira_data() -> Dict[str, Any]:
    """加载 Jira 数据预处理配置 (账号 / test_url / 并发数)"""
    cfg = dict(DEFAULT_JIRA_DATA)
    yaml_data = _load_yaml(_yaml_path())
    if "jira_data" in yaml_data:
        cfg = _merge(cfg, yaml_data["jira_data"])
    return cfg


def load_adas() -> Dict[str, Any]:
    """加载 ADAS 预处理工具配置 (exe 路径 / 车型 / mcap / 超时 / 并发)"""
    cfg = dict(DEFAULT_ADAS)
    yaml_data = _load_yaml(_yaml_path())
    if "adas" in yaml_data:
        cfg = _merge(cfg, yaml_data["adas"])
    return cfg


def load_car_models() -> Dict[str, str]:
    """加载车型-标定名称映射"""
    yaml_data = _load_yaml(_yaml_path())
    return dict(yaml_data.get("car_models", {}))


def load_replay_list_template() -> str:
    """加载列表回灌脚本模板"""
    yaml_data = _load_yaml(_yaml_path())
    return str(yaml_data.get("replay_list_template", ""))


def load_replay_sdk_template() -> str:
    """加载 SDK 回灌脚本模板(单板,素材路径手动输入)"""
    yaml_data = _load_yaml(_yaml_path())
    return str(yaml_data.get("replay_sdk_template", ""))


def config_sources() -> Dict[str, Path]:
    return {
        "builtin": Path(__file__).parent / "config.py",
        "yaml": _yaml_path(),
        "user": Path.home() / ".devboard_toolkit" / "config.json",
        "project": Path.cwd() / "config.json",
    }
