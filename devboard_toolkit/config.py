"""开发板配置加载

配置结构: config_user.yaml (用户) + config_system.yaml (系统)
  - 用户配置: boards / mount / replay_env / car_models / car_keywords / jenkins / jira_data / adas / paths
  - 系统配置: usage_check / data_proc_car_keywords / replay_list_template / replay_sdk_template

优先级(从低到高, 后者覆盖前者同 name 条目):
  1. 内置 DEFAULT_* (兜底, yaml 不存在时用)
  2. config_system.yaml (系统配置, 随版本更新)
  3. config_user.yaml (用户配置, 用户自定义, 默认不随更新覆盖)
     - 若 config_user.yaml / config_system.yaml 不存在, 自动从 config.yaml (旧格式) 按归属拆分读取 (向后兼容)
  4. 用户级 ~/.devboard_toolkit/config.json (可选覆盖 boards 段)
  5. 项目目录 config.json (可选覆盖 boards 段)
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Tuple

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ===== 段归属定义 =====
# 这些段属于 config_user.yaml, 其余属于 config_system.yaml
USER_SECTIONS = {
    "boards", "mount", "replay_env", "car_models", "car_keywords",
    "jenkins", "jira_data", "adas", "paths",
}
SYSTEM_SECTIONS = {
    "usage_check", "data_proc_car_keywords",
    "replay_list_template", "replay_sdk_template",
}


# ===== 内置默认板(兜底, 配置文件都不存在时用) =====
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
    "max_workers": 5,
}

# 内置默认 ADAS 预处理工具配置(兜底)
DEFAULT_ADAS: Dict[str, Any] = {
    "exe_path": r"D:\Desktop\Data_preprocessing\sdk_data_preprocessing\ADAS_Visualization\ADAS_Visualization.exe",
    "car_type": 3,
    "generate_mcap": False,
    "timeout": 300,
    "max_workers": 4,
}

# 内置默认数据处理车型映射(兜底, 系统配置缺失时用)
DEFAULT_DATA_PROC_CAR: Dict[str, Any] = {
    "title_keywords": {
        "463车": "463", "508车": "508", "3545车": "3545",
        "3554车": "3554", "3637车": "3637", "5436车": "5463", "5463车": "5463",
    },
    "path_keywords": {
        "0463": "463", "0508": "508", "3545": "3545",
        "3554": "3554", "3637": "3637", "5436": "5463", "5463": "5463",
    },
    "default": "0452",
}

# 内置默认开发板空闲检测阈值(兜底, 系统配置缺失时用)
DEFAULT_USAGE_CHECK: Dict[str, Any] = {
    "loadavg_threshold": 4.0,
    "net_rx_threshold_gb": 1.0,
}


# ===== 基础工具 =====
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
    """项目根目录 (exe 模式=exe 目录, 开发模式=仓库根)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ===== 文件路径 =====
def _old_yaml_path() -> Path:
    """旧版单一 config.yaml 路径 (向后兼容)"""
    return Path(get_project_root()) / "config.yaml"


def _user_yaml_path() -> Path:
    """用户配置 YAML 路径"""
    return Path(get_project_root()) / "config_user.yaml"


def _system_yaml_path() -> Path:
    """系统配置 YAML 路径"""
    return Path(get_project_root()) / "config_system.yaml"


# ===== 迁移辅助: 是否存在旧 config.yaml (供 GUI 弹窗询问用) =====
def has_old_config() -> bool:
    """存在旧版 config.yaml 但缺少 config_user.yaml 或 config_system.yaml → 返回 True"""
    old = _old_yaml_path()
    user = _user_yaml_path()
    sys_ = _system_yaml_path()
    if not old.exists():
        return False
    return (not user.exists()) or (not sys_.exists())


def split_old_to_memory() -> Tuple[Dict, Dict]:
    """把旧 config.yaml 按段归属在内存里拆分成 (user_dict, system_dict)

    返回值可直接 dump 成两个新文件 (GUI 迁移向导调用 write_split_files)。
    若旧文件不存在, 返回 ({}, {})。
    """
    old = _load_yaml(_old_yaml_path())
    if not old:
        return {}, {}
    user_sections: Dict[str, Any] = {}
    system_sections: Dict[str, Any] = {}
    for k, v in old.items():
        if k in USER_SECTIONS:
            user_sections[k] = v
        elif k in SYSTEM_SECTIONS:
            system_sections[k] = v
        # 未知段: 丢弃 (不归类不报错, 避免崩溃)
    return user_sections, system_sections


def write_split_files(user_data: Dict, system_data: Dict,
                      backup_old: bool = True) -> bool:
    """把拆分好的内容写入 config_user.yaml / config_system.yaml, 可选备份旧文件为 config.yaml.bak

    返回: 是否成功 (文件都写入了)
    """
    if not _HAS_YAML:
        return False
    try:
        up = _user_yaml_path()
        sp = _system_yaml_path()
        with up.open("w", encoding="utf-8") as f:
            yaml.safe_dump(user_data, f, allow_unicode=True, sort_keys=False, width=1024)
        with sp.open("w", encoding="utf-8") as f:
            yaml.safe_dump(system_data, f, allow_unicode=True, sort_keys=False, width=1024)
        if backup_old and _old_yaml_path().exists():
            bak = _old_yaml_path().with_suffix(".yaml.bak")
            try:
                _old_yaml_path().replace(bak)
            except Exception:
                pass
        return True
    except Exception:
        return False


# ===== 配置读取: 合并三个来源 =====
def _get_user_yaml_data() -> Dict[str, Any]:
    """读取用户配置: 优先 config_user.yaml, 不存在则从旧 config.yaml 按归属提取"""
    data = _load_yaml(_user_yaml_path())
    if not data:
        user_mem, _sys_mem = split_old_to_memory()
        data = user_mem
    return data


def _get_system_yaml_data() -> Dict[str, Any]:
    """读取系统配置: 优先 config_system.yaml, 不存在则从旧 config.yaml 按归属提取"""
    data = _load_yaml(_system_yaml_path())
    if not data:
        _user_mem, sys_mem = split_old_to_memory()
        data = sys_mem
    return data


# ===== 对外 load_*() 函数 (API 不变, 调用方无需改) =====
def load_boards() -> Dict[str, Dict[str, Any]]:
    """加载开发板配置

    优先级(从低到高, 后者整体替换前者):
      1. 内置 DEFAULT_BOARDS (兜底)
      2. config_user.yaml 的 boards 段 (整体替换 DEFAULT)
      3. ~/.devboard_toolkit/config.json 的 boards 段 (继续整体替换)
      4. 项目目录 config.json 的 boards 段 (继续整体替换)
    """
    user_yaml = _get_user_yaml_data()
    # 2. 用户 YAML 的 boards 段存在 → 整体替换 DEFAULT_BOARDS
    if "boards" in user_yaml and user_yaml["boards"]:
        boards = {k: dict(v) for k, v in user_yaml["boards"].items() if v}
    else:
        boards = dict(DEFAULT_BOARDS)
    # 3. 用户级 JSON (整体替换 boards 段)
    user_cfg = Path.home() / ".devboard_toolkit" / "config.json"
    user_json = _load_json(user_cfg)
    if "boards" in user_json and user_json["boards"]:
        boards = {k: dict(v) for k, v in user_json["boards"].items() if v}
    elif user_json and not isinstance(next(iter(user_json.values())), dict):
        # 兼容旧格式 (顶层直接是 boards, 非 dict 才判定为旧, 实际 boards 子项都是 dict, 这里 skip)
        pass
    # 4. 项目级 JSON (整体替换 boards 段)
    proj_cfg = Path.cwd() / "config.json"
    proj_json = _load_json(proj_cfg)
    if "boards" in proj_json and proj_json["boards"]:
        boards = {k: dict(v) for k, v in proj_json["boards"].items() if v}
    return boards


def load_replay_env() -> Dict[str, Any]:
    """加载回灌环境(挂载 + testbed 路径)"""
    cfg = dict(DEFAULT_REPLAY_ENV)
    user_yaml = _get_user_yaml_data()
    if "replay_env" in user_yaml:
        cfg = _merge(cfg, user_yaml["replay_env"])
    return cfg


def load_mount() -> Dict[str, Any]:
    """加载 mount 段 (GUI 读写使用)"""
    user_yaml = _get_user_yaml_data()
    return dict(user_yaml.get("mount", {}))


def load_usage_check() -> Dict[str, Any]:
    """加载开发板空闲检测阈值"""
    cfg = dict(DEFAULT_USAGE_CHECK)
    system_yaml = _get_system_yaml_data()
    if "usage_check" in system_yaml and isinstance(system_yaml["usage_check"], dict):
        cfg = _merge(cfg, system_yaml["usage_check"])
    return cfg


def load_jenkins() -> Dict[str, Any]:
    """加载 Jenkins 编译平台配置"""
    cfg = dict(DEFAULT_JENKINS)
    user_yaml = _get_user_yaml_data()
    if "jenkins" in user_yaml:
        cfg = _merge(cfg, user_yaml["jenkins"])
    return cfg


def load_jira_data() -> Dict[str, Any]:
    """加载 Jira 数据预处理配置 (账号 / test_url / 并发数)"""
    cfg = dict(DEFAULT_JIRA_DATA)
    user_yaml = _get_user_yaml_data()
    if "jira_data" in user_yaml:
        cfg = _merge(cfg, user_yaml["jira_data"])
    return cfg


def load_adas() -> Dict[str, Any]:
    """加载 ADAS 预处理工具配置 (exe 路径 / 车型 / mcap / 超时 / 并发)"""
    cfg = dict(DEFAULT_ADAS)
    user_yaml = _get_user_yaml_data()
    if "adas" in user_yaml:
        cfg = _merge(cfg, user_yaml["adas"])
    return cfg


def load_paths() -> Dict[str, Any]:
    """加载 paths 段 (下载/输出根目录)"""
    user_yaml = _get_user_yaml_data()
    return dict(user_yaml.get("paths", {}))


def load_car_models() -> Dict[str, str]:
    """加载车型-标定名称映射"""
    user_yaml = _get_user_yaml_data()
    return dict(user_yaml.get("car_models", {}))


def load_car_keywords() -> Dict[str, str]:
    """加载 classify_by_car.py 使用的车型关键词映射 (config_user.yaml)"""
    user_yaml = _get_user_yaml_data()
    raw = user_yaml.get("car_keywords", {})
    if isinstance(raw, dict) and raw:
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def load_data_proc_car_keywords() -> Dict[str, Any]:
    """加载数据处理车型映射 (title_keywords / path_keywords / default)

    优先级: 内置 DEFAULT_DATA_PROC_CAR (兜底) → config_system.yaml 的 data_proc_car_keywords 段
    """
    cfg = {
        "title_keywords": dict(DEFAULT_DATA_PROC_CAR["title_keywords"]),
        "path_keywords": dict(DEFAULT_DATA_PROC_CAR["path_keywords"]),
        "default": DEFAULT_DATA_PROC_CAR["default"],
    }
    system_yaml = _get_system_yaml_data()
    user = system_yaml.get("data_proc_car_keywords", {})
    if isinstance(user, dict):
        if "title_keywords" in user and isinstance(user["title_keywords"], dict):
            cfg["title_keywords"] = dict(user["title_keywords"])
        if "path_keywords" in user and isinstance(user["path_keywords"], dict):
            cfg["path_keywords"] = dict(user["path_keywords"])
        if "default" in user and user["default"]:
            cfg["default"] = str(user["default"])
    return cfg


def load_replay_list_template() -> str:
    """加载列表回灌脚本模板 (config_system.yaml)"""
    system_yaml = _get_system_yaml_data()
    return str(system_yaml.get("replay_list_template", ""))


def load_replay_sdk_template() -> str:
    """加载 SDK 回灌脚本模板 (config_system.yaml)"""
    system_yaml = _get_system_yaml_data()
    return str(system_yaml.get("replay_sdk_template", ""))


def config_sources() -> Dict[str, Path]:
    return {
        "builtin": Path(__file__).parent / "config.py",
        "config_user": _user_yaml_path(),
        "config_system": _system_yaml_path(),
        "legacy_config": _old_yaml_path(),
        "user": Path.home() / ".devboard_toolkit" / "config.json",
        "project": Path.cwd() / "config.json",
    }
