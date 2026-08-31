"""数据处理 API — 复用 data_preproc/pipeline.py

POST /api/dataproc/start
  body: { mode: "jira"|"video", input: "...", output_dir: "..." }
  → 后台线程跑 pipeline → WebSocket 推日志
"""

import os
import sys
import threading
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import get_current_user
from ..core.logbus import log_bus
from ..core.user_data import load_user_config, get_user_task_dir

router = APIRouter()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class DataProcReq(BaseModel):
    mode: str = "jira"            # jira | video
    input_text: str               # Jira 链接列表 或 视频路径列表 (换行分隔)
    output_dir: str = ""          # 留空则用用户任务目录


@router.post("/start")
async def start_dataproc(req: DataProcReq, username: str = Depends(get_current_user)):
    if not req.input_text.strip():
        raise HTTPException(status_code=400, detail="输入不能为空")

    out_dir = req.output_dir or get_user_task_dir(username)

    def _run():
        try:
            log_bus.emit("dataproc", f"[{username}] 数据处理启动 (mode={req.mode})")
            user_cfg = load_user_config(username)

            # 复用桌面版 pipeline (后续 P3 阶段细化)
            lines = [l.strip() for l in req.input_text.strip().split("\n") if l.strip()]
            log_bus.emit("dataproc", f"[{username}] 共 {len(lines)} 条输入")

            # TODO: 调用 pipeline.data_preproc_main(...), 传入 user_cfg
            for i, line in enumerate(lines, 1):
                log_bus.emit("dataproc", f"[{i}/{len(lines)}] 处理: {line}")

            log_bus.emit("dataproc", f"[{username}] 数据处理完成, 输出: {out_dir}")
        except Exception as e:
            log_bus.emit("dataproc", f"[{username}] 数据处理异常: {e}", level="error")

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return {"msg": "数据处理已启动"}
