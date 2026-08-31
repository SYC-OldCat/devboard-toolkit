"""配置加载器 — 读取项目根目录的 config_system.yaml"""

import os
import yaml
from typing import Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
_SYSTEM_YAML = os.path.join(_PROJECT_ROOT, "config_system.yaml")

_cache: dict = {}


def get_system_config() -> dict[str, Any]:
    """读取系统配置 (带缓存)"""
    global _cache
    if _cache:
        return _cache
    if not os.path.exists(_SYSTEM_YAML):
        return {}
    with open(_SYSTEM_YAML, "r", encoding="utf-8") as f:
        _cache = yaml.safe_load(f) or {}
    return _cache


def reload_system_config():
    """重新加载系统配置 (配置修改后调用)"""
    global _cache
    _cache = {}
    return get_system_config()
