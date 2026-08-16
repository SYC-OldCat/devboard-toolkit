"""开发板使用状态检测(基于 ps -eo 进程级判定)

判定逻辑(核心看进程,最准确):
- 主判据: 存在非系统进程 CPU > 10%  或  内存 > 5%  →  BUSY(在跑东西)
- 系统进程白名单: 内核线程[xxx]、sshd、init/systemd、kthread/kworker、ksoftirqd、rcu_*、
  migration、watchdog、irq/、cpuhp、mm_percpu_wq、cifs*、jbd2、ext4、rpciod、nfsiod、
  stmmac_wq、udhcpc、getty、mdev、klogd、ota_server、ot_rotate.sh、ps/top 观测工具本身

命令实现: paramiko exec_command 是 non-login shell(PATH 不完整),会找不到 procps-ng 的 ps。
用 `sh -lc '...'` 套一层,强制走 login shell(读取 /etc/profile 补全 PATH),这样 procps-ng 的
完整 ps -eo(含 %CPU/%MEM/ELAPSED 列)才能执行。
"""

from dataclasses import dataclass, field
from typing import List, Tuple

from .ssh_client import build_client, run_cmd, safe_close


@dataclass
class ProcEntry:
    user: str
    pid: int
    pcpu: float
    pmem: float
    etime: str
    cmd: str


@dataclass
class UsageResult:
    name: str
    host: str
    busy: bool
    reasons: List[str] = field(default_factory=list)
    top_procs: List[ProcEntry] = field(default_factory=list)


# ============================================================
# 系统进程白名单(不算"在用板子")
# ============================================================
SYS_KEYWORDS = [
    # 内核线程([xxx] 格式)
    "[",
    # init / systemd
    "init", "linuxrc", "systemd",
    # kthread 家族
    "kthreadd", "kworker", "ksoftirqd", "rcu_", "rcub/", "rcuc/", "rcu_par",
    "rcu_gp", "rcu_preempt", "rcu_tasks", "migration", "watchdog",
    "watchdogd", "irq_work", "cpuhp",
    # 内存/文件系统
    "mm_percpu_wq", "kdevtmpfs", "netns", "oom_reaper", "writeback",
    "kcompactd", "ksmd", "kblockd", "devfreq_wq", "kswapd0",
    "jbd2/", "ext4-", "ext4_",
    # 网络/CIFS/NFS
    "cifsd", "cifsiod", "smb3decryptd", "cifsfileinfoput", "cifsoplockd",
    "rpciod", "xprtiod", "nfsiod", "stmmac_wq", "ipv6_addrconf",
    # 中断/硬件
    "irq/", "sdhci", "mmc_complete", "cve_k_sched", "npu_core",
    "dsp_mail", "vpwm", "HSM_AIC", "ts_irq", "ot_mipir", "ot_",
    "usb_ovc", "dw_axi_d",
    # 系统服务
    "sshd", "sftp-server", "internal-sftp", "@pts/", "@notty", "@internal",
    "udhcpc", "getty", "mdev", "klogd", "syslogd",
    "/opt/bin/ota_server", "ot_rotate.sh",
    # 观测工具本身
    "ps -eo", "ps aux", "top ", "top$", "htop",
]

# 阈值
CPU_PCT_THRESHOLD = 10.0
MEM_PCT_THRESHOLD = 5.0


def _is_system_proc(cmd: str) -> bool:
    if not cmd:
        return True
    c = cmd.strip()
    for kw in SYS_KEYWORDS:
        if c.startswith(kw) or kw in c:
            return True
    return False


def _parse_ps_output(out: str) -> List[ProcEntry]:
    entries: List[ProcEntry] = []
    data_started = False
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not data_started:
            if "PID" in stripped and ("%CPU" in stripped or "PCPU" in stripped):
                data_started = True
            continue
        # [user, pid, pcpu, pmem, etime, 剩余整串 cmd]
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            pid = int(parts[1])
            pcpu = float(parts[2])
            pmem = float(parts[3])
        except (ValueError, IndexError):
            continue
        entries.append(ProcEntry(
            user=parts[0],
            pid=pid,
            pcpu=pcpu,
            pmem=pmem,
            etime=parts[4],
            cmd=parts[5].strip(),
        ))
    return entries


# 关键: sh -lc 套一层,读 /etc/profile 补全 PATH,这样 procps-ng ps 才找得到
_PS_CMD = (
    "sh -lc 'ps -eo user,pid,pcpu,pmem,etime,cmd --sort=-pcpu'"
)


def check_usage_one(name: str, cfg: dict) -> UsageResult:
    host = cfg.get("host", "?")
    result = UsageResult(name=name, host=host, busy=False)

    try:
        client = build_client(cfg)
    except Exception as e:
        result.busy = True
        result.reasons.append(f"[连接失败,按 BUSY 处理] {e}")
        return result

    try:
        out, err, code = run_cmd(client, _PS_CMD, timeout=10)
    except Exception as e:
        result.busy = True
        result.reasons.append(f"[命令执行失败,按 BUSY 处理] {e}")
        return result
    finally:
        safe_close(client)

    if code != 0 and not out.strip():
        result.busy = True
        result.reasons.append(
            f"[ps 命令异常 exit={code},按 BUSY 处理] {err[:200]}"
        )
        return result

    procs = _parse_ps_output(out)
    result.top_procs = procs[:5]

    busy_procs: List[Tuple[str, ProcEntry]] = []
    for p in procs:
        if _is_system_proc(p.cmd):
            continue
        if p.pcpu >= CPU_PCT_THRESHOLD or p.pmem >= MEM_PCT_THRESHOLD:
            tag = f"CPU {p.pcpu:.1f}%" if p.pcpu >= CPU_PCT_THRESHOLD else f"MEM {p.pmem:.1f}%"
            busy_procs.append((tag, p))

    if busy_procs:
        result.busy = True
        for tag, p in busy_procs:
            result.reasons.append(
                f"进程活跃: {p.cmd} (PID {p.pid}, {tag}, 运行 {p.etime})"
            )

    if not result.busy:
        sys_summaries = []
        for p in procs[:3]:
            sys_summaries.append(f"{p.cmd} CPU {p.pcpu:.1f}%")
        result.reasons.append(
            "无用户进程 (Top3 系统: " + ", ".join(sys_summaries) + ")"
        )

    return result


def format_result(r: UsageResult) -> str:
    status = "BUSY" if r.busy else "IDLE"
    lines = [f"{r.name:<10s} {r.host:<20s} {status:<6s} {r.reasons[0] if r.reasons else ''}"]
    for reason in r.reasons[1:]:
        lines.append(f"{'':<10s} {'':<20s} {'':<6s} {reason}")
    return "\n".join(lines)


def print_results(results: List[UsageResult]):
    print(f"{'板名':<10s}{'地址':<20s}{'状态':<6s}{'原因'}")
    print("-" * 110)
    idle_names = []
    busy_names = []
    for r in results:
        print(format_result(r))
        if r.busy:
            busy_names.append(r.name)
        else:
            idle_names.append(r.name)
    print("-" * 110)
    print(f"汇总: 空闲 {len(idle_names)} 块" +
          (f"({', '.join(idle_names)}) → 可安全使用" if idle_names else ""))
    print(f"     使用中 {len(busy_names)} 块" +
          (f"({', '.join(busy_names)}) → 请勿中断" if busy_names else ""))
