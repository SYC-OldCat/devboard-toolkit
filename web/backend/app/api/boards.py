"""板池管理 API — 查询/检测/锁定/释放"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from .auth import get_current_user
from ..core.board_pool import board_pool

router = APIRouter()


@router.get("")
async def list_boards():
    """获取所有板状态"""
    return {"boards": board_pool.get_all()}


@router.post("/check")
async def check_boards(username: str = Depends(get_current_user)):
    """触发检测所有板 (不检测 busy 板)"""
    import threading
    t = threading.Thread(target=board_pool.check_all, daemon=True)
    t.start()
    return {"msg": "检测已启动"}


class LockReq(BaseModel):
    count: int = 1
    task: str = ""


@router.post("/lock")
async def lock_boards(req: LockReq, username: str = Depends(get_current_user)):
    """锁定 N 块空闲板"""
    locked = board_pool.lock_n(req.count, username, req.task)
    if not locked:
        return {"msg": "没有空闲板可锁定", "locked": []}
    return {"msg": f"锁定 {len(locked)} 块", "locked": locked}


@router.post("/{name}/unlock")
async def unlock_board(name: str, username: str = Depends(get_current_user)):
    """释放一块板"""
    board_pool.unlock(name)
    return {"msg": f"{name} 已释放"}
