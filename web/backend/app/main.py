"""DevBoard Toolkit — Web 版后端

FastAPI 应用入口, 提供:
- 用户认证 (注册/登录/JWT)
- 多用户隔离配置 (每人独立 config_user.yaml)
- 全局共享板池 (多人协作)
- WebSocket 实时日志推送
- 复用桌面版核心模块 (ssh_client / batch_replay / jenkins_build / data_preproc)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

# 把项目根目录加入 sys.path, 以便 import devboard_toolkit 模块
import sys
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from .api import auth, boards, config, dataproc, jenkins, replay, pipeline
from .core.logbus import log_bus
from .core.board_pool import board_pool

app = FastAPI(
    title="DevBoard Toolkit Web",
    description="开发板工具箱 — 网页版",
    version="0.1.0",
)

# CORS: 内网环境直接放行
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(config.router, prefix="/api/config", tags=["配置"])
app.include_router(boards.router, prefix="/api/boards", tags=["板池"])
app.include_router(replay.router, prefix="/api/replay", tags=["回灌"])
app.include_router(dataproc.router, prefix="/api/dataproc", tags=["数据处理"])
app.include_router(jenkins.router, prefix="/api/jenkins", tags=["Jenkins编译"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["组合流水线"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# WebSocket: 实时日志 (前端订阅后持续推送)
from fastapi import WebSocket, WebSocketDisconnect
from .core.logbus import log_bus

@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    await ws.accept()
    queue = log_bus.subscribe()
    try:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        log_bus.unsubscribe(queue)


# 前端静态文件 (生产环境由 nginx 直接处理, 开发时由 Vite dev server)
_frontend_dist = os.path.join(os.path.dirname(_PROJECT_ROOT), "frontend", "dist")
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
