"""用户数据存储 — 每用户独立配置目录

data/users/
  <username>/
    config_user.yaml   # 用户个人配置 (boards/mount/jira/jenkins/adas/paths)
    tasks/             # 任务输出目录
"""

import os
import yaml
import shutil
from typing import Any

# 数据目录: web/backend/data/
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
_USERS_DIR = os.path.join(_DATA_DIR, "users")

# 模板: 从项目根目录的 config_user.yaml 复制新用户初始配置
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
_TEMPLATE_USER_YAML = os.path.join(_PROJECT_ROOT, "config_user.yaml")


def _user_dir(username: str) -> str:
    return os.path.join(_USERS_DIR, username)


def _user_yaml_path(username: str) -> str:
    return os.path.join(_user_dir(username), "config_user.yaml")


def init_user_data(username: str):
    """新用户注册时创建数据目录, 从模板复制初始配置"""
    d = _user_dir(username)
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, "tasks"), exist_ok=True)
    if not os.path.exists(_user_yaml_path(username)):
        if os.path.exists(_TEMPLATE_USER_YAML):
            shutil.copy2(_TEMPLATE_USER_YAML, _user_yaml_path(username))
        else:
            # 模板不存在,创建空配置
            with open(_user_yaml_path(username), "w", encoding="utf-8") as f:
                yaml.dump({}, f, allow_unicode=True)


def load_user_config(username: str) -> dict:
    """读取用户配置"""
    p = _user_yaml_path(username)
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_user_config(username: str, data: dict):
    """保存用户配置"""
    p = _user_yaml_path(username)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def get_user_task_dir(username: str) -> str:
    """获取用户任务输出目录"""
    d = os.path.join(_user_dir(username), "tasks")
    os.makedirs(d, exist_ok=True)
    return d
