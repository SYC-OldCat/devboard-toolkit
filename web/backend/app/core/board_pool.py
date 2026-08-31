"""全局板池管理 — 服务器共享,多人协作

状态:
  idle  — 空闲,可被锁定
  busy  — 被某用户锁定,正在执行任务
  error — 检测失败 (SSH 不通等)

规则:
  - 启动回灌时从 idle 板中锁定 N 块
  - 回灌结束自动解锁
  - 所有用户通过 WebSocket 实时看到板状态变化
"""

import threading
import time
from datetime import datetime
from typing import Optional

import paramiko

from ..config_loader import get_system_config
from .logbus import log_bus


class BoardInfo:
    """单块板状态"""
    def __init__(self, name: str, ip: str, username: str = "root", port: int = 22):
        self.name = name
        self.ip = ip
        self.username = username
        self.port = port
        self.status: str = "idle"          # idle / busy / error / unknown
        self.locked_by: Optional[str] = None  # username
        self.locked_at: Optional[str] = None
        self.current_task: Optional[str] = None
        self.last_check: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ip": self.ip,
            "status": self.status,
            "locked_by": self.locked_by,
            "locked_at": self.locked_at,
            "current_task": self.current_task,
            "last_check": self.last_check,
        }


class BoardPool:
    """全局板池 (线程安全单例)"""

    def __init__(self):
        self._boards: dict[str, BoardInfo] = {}
        self._lock = threading.Lock()
        self._initialized = False

    def init_from_config(self):
        """从 config_system.yaml 的 boards 配置初始化板池"""
        with self._lock:
            self._boards.clear()
            sys_cfg = get_system_config()
            # boards 定义在 config_user.yaml, 但 web 版统一用 system 配置的 boards 段
            # 这里从 system config 读 boards (也可以从 user config 读)
            boards_cfg = sys_cfg.get("boards", {})
            for name, info in boards_cfg.items():
                self._boards[name] = BoardInfo(
                    name=name,
                    ip=info.get("ip", ""),
                    username=info.get("username", "root"),
                    port=info.get("port", 22),
                )
            self._initialized = True
            log_bus.emit("boards", f"板池初始化: {len(self._boards)} 块板")

    def get_all(self) -> list[dict]:
        with self._lock:
            return [b.to_dict() for b in self._boards.values()]

    def get_idle_names(self) -> list[str]:
        with self._lock:
            return [name for name, b in self._boards.items() if b.status == "idle"]

    def lock(self, name: str, username: str, task: str = "") -> bool:
        """锁定一块板 (原子操作)"""
        with self._lock:
            b = self._boards.get(name)
            if not b or b.status != "idle":
                return False
            b.status = "busy"
            b.locked_by = username
            b.locked_at = datetime.now().strftime("%H:%M:%S")
            b.current_task = task
            log_bus.emit("boards", f"[{name}] 被 {username} 锁定 (task={task})")
            return True

    def lock_n(self, n: int, username: str, task: str = "") -> list[str]:
        """锁定 N 块空闲板,返回成功锁定的板名列表"""
        locked = []
        with self._lock:
            idle = [name for name, b in self._boards.items() if b.status == "idle"]
            for name in idle[:n]:
                b = self._boards[name]
                b.status = "busy"
                b.locked_by = username
                b.locked_at = datetime.now().strftime("%H:%M:%S")
                b.current_task = task
                locked.append(name)
        if locked:
            log_bus.emit("boards", f"{username} 锁定 {len(locked)} 块板: {locked}")
        return locked

    def unlock(self, name: str):
        with self._lock:
            b = self._boards.get(name)
            if b:
                b.status = "idle"
                b.locked_by = None
                b.locked_at = None
                b.current_task = None
                log_bus.emit("boards", f"[{name}] 已释放")

    def check_all(self):
        """并行 SSH 检测所有板,更新状态 (不改动 locked 板)"""
        import concurrent.futures

        with self._lock:
            boards_to_check = [
                (name, b) for name, b in self._boards.items()
                if b.status != "busy"  # 不检测被锁定的板
            ]

        def _check_one(name: str, b: BoardInfo):
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    b.ip, port=b.port, username=b.username,
                    timeout=5, banner_timeout=5,
                )
                # 简单检测: 执行 uptime
                stdin, stdout, stderr = client.exec_command("uptime", timeout=5)
                stdout.read()
                client.close()
                return name, "idle"
            except Exception:
                return name, "error"

        results = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(boards_to_check) or 1
        ) as pool:
            futures = [pool.submit(_check_one, n, b) for n, b in boards_to_check]
            for f in concurrent.futures.as_completed(futures):
                name, status = f.result()
                results[name] = status

        with self._lock:
            for name, status in results.items():
                b = self._boards.get(name)
                if b and b.status != "busy":  # 不覆盖 busy
                    b.status = status
                    b.last_check = datetime.now().strftime("%H:%M:%S")

        log_bus.emit("boards", f"检测完成: {results}")


# 全局单例
board_pool = BoardPool()
