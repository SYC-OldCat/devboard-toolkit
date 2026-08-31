"""日志总线: 各模块的日志输出 → WebSocket 推送到前端

用法:
    from .logbus import log_bus
    log_bus.emit("replay", "[11:21:23] board3 开始回灌...")
    log_bus.emit("dataproc", "[11:21:25] FTIM-1708 提取成功")
"""

import asyncio
import queue
import threading
from datetime import datetime
from typing import Optional


class LogBus:
    """全局日志总线 (线程安全)

    - publish 端: 任意线程/async 调用 emit()
    - subscribe 端: WebSocket 处理协程从 asyncio.Queue 读取
    """

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def emit(self, channel: str, msg: str, level: str = "info"):
        """发送日志消息到所有订阅者 (线程安全, 非阻塞)"""
        payload = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "channel": channel,
            "msg": msg,
            "level": level,
        }
        with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # 慢消费者丢消息,不阻塞

    # 同步线程也安全的 publish 接口 (供 paramiko SSH 线程调用)
    def emit_sync(self, channel: str, msg: str, level: str = "info"):
        """同步版本 emit, 供后台线程池中的代码调用"""
        self.emit(channel, msg, level)


# 全局单例
log_bus = LogBus()
