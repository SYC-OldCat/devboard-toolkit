"""自动回灌 API — 复用 batch_replay.py 核心逻辑

POST /api/replay/start
  body: { folder: "...", board_count: 2 }
  → 检测空闲板 → 锁定 N 块 → 生成脚本 → SSH 启动 → WebSocket 推日志
"""

import os
import sys
import threading
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import get_current_user
from ..core.board_pool import board_pool
from ..core.logbus import log_bus
from ..core.user_data import load_user_config, get_user_task_dir

router = APIRouter()

# 把桌面版项目根加入 path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class ReplayReq(BaseModel):
    folder: str               # 回灌素材文件夹路径 (UNC 或本地路径)
    board_count: int = 0      # 使用板数, 0=自动选最大空闲数


@router.post("/start")
async def start_replay(req: ReplayReq, username: str = Depends(get_current_user)):
    if not os.path.isdir(req.folder):
        raise HTTPException(status_code=400, detail=f"文件夹不存在: {req.folder}")

    # 锁定板子
    n = req.board_count if req.board_count > 0 else len(board_pool.get_idle_names())
    if n == 0:
        raise HTTPException(status_code=400, detail="没有空闲板可用")

    locked = board_pool.lock_n(n, username, task=f"replay:{req.folder}")
    if not locked:
        raise HTTPException(status_code=400, detail="锁定板子失败")

    # 后台线程跑回灌
    def _run():
        try:
            log_bus.emit("replay", f"[{username}] 回灌启动: {req.folder}")
            log_bus.emit("replay", f"[{username}] 使用板: {locked}")

            # 复用桌面版 batch_replay (后续 P2 阶段细化)
            user_cfg = load_user_config(username)
            # TODO: 调用 batch_replay 核心逻辑,传入 locked 板和 user_cfg
            # 目前先输出日志占位
            for board_name in locked:
                log_bus.emit("replay", f"[{board_name}] SSH 连接中...")
                # 示意: 实际 P2 阶段接入 batch_replay.run()

            log_bus.emit("replay", f"[{username}] 回灌完成")
        except Exception as e:
            log_bus.emit("replay", f"[{username}] 回灌异常: {e}", level="error")
        finally:
            for board_name in locked:
                board_pool.unlock(board_name)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return {"msg": "回灌已启动", "boards": locked}
