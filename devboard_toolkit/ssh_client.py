"""SSH 客户端封装(paramiko)

修复 Windows 上 paramiko 的两类问题:
1. banner 读取抖动 → 加重试(最多 2 次,间隔 1s)
2. 关闭时 Transport 线程报 WinError 10038 → 安全关闭 + 抑制 paramiko 日志噪音
"""

import sys
import time
import logging
from typing import Tuple

import paramiko


# 抑制 paramiko Transport 线程在 Windows 上的噪音日志
# (Error reading SSH protocol banner[WinError 10038] 等)
logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)


def build_client(cfg: dict, retries: int = 1):
    """建立 SSH 连接,失败时自动重试

    Args:
        cfg: 板配置(host/user/password/timeout 等)
        retries: 失败重试次数(默认 1,共尝试 2 次)
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    conn_timeout = int(cfg.get("timeout", 10))
    banner_timeout = int(cfg.get("banner_timeout", conn_timeout + 2))
    auth_timeout = int(cfg.get("auth_timeout", conn_timeout + 2))

    last_err = None
    for attempt in range(retries + 1):
        try:
            client.connect(
                cfg["host"],
                port=int(cfg.get("port", 22)),
                username=cfg["user"],
                password=cfg["password"],
                timeout=conn_timeout,
                banner_timeout=banner_timeout,
                auth_timeout=auth_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            return client
        except Exception as e:
            last_err = e
            # 关闭可能残留的连接,避免下次复用坏 client
            try:
                client.close()
            except Exception:
                pass
            if attempt < retries:
                time.sleep(1)  # 重试前等 1 秒

    raise last_err


def safe_close(client):
    """安全关闭 SSH 连接,避免 paramiko Transport 线程报 WinError 10038"""
    if client is None:
        return
    try:
        if client.get_transport() is not None:
            client.get_transport().close()
    except Exception:
        pass
    try:
        client.close()
    except Exception:
        pass


def run_cmd(client, cmd: str, timeout: int = 30) -> Tuple[str, str, int]:
    """执行命令,返回 (stdout, stderr, exit_code)"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").rstrip()
    err = stderr.read().decode(errors="replace").rstrip()
    try:
        code = stdout.channel.recv_exit_status()
    except Exception:
        code = -1
    return out, err, code
