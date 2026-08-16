"""批量 SSH 连通性探测(并发,提升效率)"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List

from .ssh_client import build_client, run_cmd, safe_close


@dataclass
class ProbeResult:
    name: str
    host: str
    port: int
    ok: bool
    detail: str
    latency_ms: float = 0.0
    uname: str = ""


def _probe_one(name: str, cfg: dict) -> ProbeResult:
    host = cfg.get("host", "?")
    port = int(cfg.get("port", 22))
    user = cfg.get("user", "root")
    timeout = float(cfg.get("timeout", 8))

    t0 = time.monotonic()
    try:
        client = build_client(cfg)
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        return ProbeResult(
            name=name, host=host, port=port, ok=False,
            detail=f"连接失败: {e}", latency_ms=dt,
        )

    try:
        dt_conn = (time.monotonic() - t0) * 1000
        out, err, code = run_cmd(client, "uname -a", timeout=timeout)
        uname = out.splitlines()[0] if out else "(无uname输出)"
        return ProbeResult(
            name=name, host=host, port=port, ok=True,
            detail=f"认证成功,用户={user}",
            latency_ms=dt_conn, uname=uname,
        )
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        return ProbeResult(
            name=name, host=host, port=port, ok=False,
            detail=f"已连接但执行命令失败: {e}",
            latency_ms=dt,
        )
    finally:
        safe_close(client)


def probe_boards(
    boards: Dict[str, dict],
    names: List[str] | None = None,
    max_workers: int = 6,
) -> List[ProbeResult]:
    """并发探测指定(或全部)板子的 SSH 连通性。

    Args:
        boards: load_boards() 返回的配置字典
        names: 指定要测的板名列表,None 则测所有
        max_workers: 并发数(默认6,和当前板数一致)
    """
    targets = names if names else list(boards.keys())
    missing = [n for n in targets if n not in boards]
    if missing:
        raise ValueError(f"未知开发板: {missing}")

    results: List[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_probe_one, n, boards[n]): n for n in targets}
        for fut in as_completed(futs):
            results.append(fut.result())
    # 按板名排序,方便对照
    order = {n: i for i, n in enumerate(targets)}
    results.sort(key=lambda r: order.get(r.name, 999))
    return results


def print_results(results: List[ProbeResult]):
    print(f"{'编号':<6s}{'板名':<10s}{'地址':<22s}{'状态':<8s}{'耗时(ms)':<10s}{'备注'}")
    print("-" * 90)
    ok_cnt = 0
    for idx, r in enumerate(results, 1):
        tag = "OK" if r.ok else "FAIL"
        if r.ok:
            ok_cnt += 1
        print(
            f"{idx:<6d}{r.name:<10s}{r.host+':'+str(r.port):<22s}"
            f"{tag:<8s}{r.latency_ms:<10.1f}{r.detail}"
        )
        if r.ok and r.uname:
            print(f"      {'':<10s}{'':<22s}{'':<8s}{'':<10s}系统: {r.uname}")
    print("-" * 90)
    print(f"总计: {len(results)} 块,连通 {ok_cnt} 块,失败 {len(results) - ok_cnt} 块")
