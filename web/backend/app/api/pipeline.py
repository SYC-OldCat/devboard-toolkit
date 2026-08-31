"""组合流水线 API — 数据处理 → 编译 → 回灌 串行编排

POST /api/pipeline/start
  body: { dataproc: {...}, jenkins: {...}, replay: {...} }
"""

import threading
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Any

from .auth import get_current_user
from ..core.logbus import log_bus

router = APIRouter()


class PipelineReq(BaseModel):
    steps: list[str] = []                    # ["dataproc", "jenkins", "replay"]
    dataproc_input: str = ""
    jenkins_job: str = ""
    jenkins_params: dict[str, Any] = {}
    replay_folder: str = ""
    replay_board_count: int = 0


@router.post("/start")
async def start_pipeline(req: PipelineReq, username: str = Depends(get_current_user)):
    def _run():
        try:
            log_bus.emit("pipeline", f"[{username}] 组合流水线启动: {req.steps}")
            # TODO: P5 阶段按 steps 顺序串联调用 (P4 阶段接入)
            for step in req.steps:
                log_bus.emit("pipeline", f"[{username}] → 执行步骤: {step}")
            log_bus.emit("pipeline", f"[{username}] 流水线完成")
        except Exception as e:
            log_bus.emit("pipeline", f"[{username}] 流水线异常: {e}", level="error")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"msg": "流水线已启动"}
