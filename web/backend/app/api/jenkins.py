"""Jenkins 编译 API — 复用 jenkins_build.py

POST /api/jenkins/build
  body: { job: "...", params: {...}, sdk_zip_path: "..." }
"""

import threading
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any

from .auth import get_current_user
from ..core.logbus import log_bus
from ..core.user_data import load_user_config

router = APIRouter()


class BuildReq(BaseModel):
    job: str
    params: dict[str, Any] = {}
    sdk_zip_path: str = ""


@router.post("/build")
async def start_build(req: BuildReq, username: str = Depends(get_current_user)):
    def _run():
        try:
            log_bus.emit("jenkins", f"[{username}] Jenkins 编译: {req.job}")
            user_cfg = load_user_config(username)
            # TODO: 调用 jenkins_build 核心逻辑 (P4 阶段)
            log_bus.emit("jenkins", f"[{username}] 编译已触发")
        except Exception as e:
            log_bus.emit("jenkins", f"[{username}] 编译异常: {e}", level="error")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"msg": "编译已触发"}
