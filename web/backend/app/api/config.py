"""配置管理 API — 读写用户配置 (config_user.yaml) 和系统配置 (config_system.yaml)"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any

from .auth import get_current_user
from ..core.user_data import load_user_config, save_user_config
from ..config_loader import get_system_config, reload_system_config

router = APIRouter()


@router.get("/user")
async def get_user_cfg(username: str = Depends(get_current_user)):
    return load_user_config(username)


@router.put("/user")
async def put_user_cfg(data: dict, username: str = Depends(get_current_user)):
    save_user_config(username, data)
    return {"msg": "用户配置已保存"}


@router.get("/system")
async def get_system_cfg():
    return get_system_config()


@router.put("/system")
async def put_system_cfg(data: dict, username: str = Depends(get_current_user)):
    """系统配置写入 (管理员权限, 内网简化处理)"""
    import os, yaml
    from ..config_loader import _SYSTEM_YAML
    with open(_SYSTEM_YAML, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    reload_system_config()
    return {"msg": "系统配置已保存"}
