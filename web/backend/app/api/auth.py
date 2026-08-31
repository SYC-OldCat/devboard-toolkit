"""用户认证 — 注册/登录/JWT

- 密码 bcrypt 加密存储
- JWT token 认证 (有效期 7 天)
- 内网环境, 简单用户名+密码即可
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import jwt, JWTError

from ..core.user_data import init_user_data


router = APIRouter()

# 配置
_SECRET_KEY = "devboard-toolkit-web-secret-2026"  # 内网, 硬编码即可
_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 7

# 密码加密
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 用户存储: data/users/users.json
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
_USERS_FILE = os.path.join(_DATA_DIR, "users.json")

_security = HTTPBearer()


def _load_users() -> dict:
    if not os.path.exists(_USERS_FILE):
        return {}
    with open(_USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_users(users: dict):
    os.makedirs(os.path.dirname(_USERS_FILE), exist_ok=True)
    with open(_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


# ---- 请求模型 ----

class RegisterReq(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginReq(BaseModel):
    username: str
    password: str


# ---- 认证依赖 ----

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> str:
    """从 JWT token 解析当前用户名"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="无效 token")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="token 已过期或无效")


# ---- 路由 ----

@router.post("/register")
async def register(req: RegisterReq):
    users = _load_users()
    if req.username in users:
        raise HTTPException(status_code=400, detail="用户名已存在")
    users[req.username] = {
        "password_hash": _pwd_ctx.hash(req.password),
        "display_name": req.display_name or req.username,
        "created_at": datetime.now().isoformat(),
    }
    _save_users(users)
    # 初始化用户数据目录
    init_user_data(req.username)
    return {"msg": "注册成功", "username": req.username}


@router.post("/login")
async def login(req: LoginReq):
    users = _load_users()
    user = users.get(req.username)
    if not user or not _pwd_ctx.verify(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 生成 JWT
    expire = datetime.utcnow() + timedelta(days=_TOKEN_EXPIRE_DAYS)
    token = jwt.encode(
        {"sub": req.username, "exp": expire},
        _SECRET_KEY,
        algorithm=_ALGORITHM,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": req.username,
        "display_name": user.get("display_name", req.username),
    }


@router.get("/me")
async def me(username: str = Depends(get_current_user)):
    users = _load_users()
    user = users.get(username, {})
    return {
        "username": username,
        "display_name": user.get("display_name", username),
    }
