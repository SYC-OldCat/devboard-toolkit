"""开发板状态检测(回灌前体检)"""

from .ssh_client import run_cmd


def _section(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def check_board(client):
    _section("[1] 在线用户 / 会话")
    out, _, _ = run_cmd(client, "who; echo ' ----- w ----- '; w")
    print(out or "(无人登录)")

    _section("[2] SSH 连接数")
    out, _, _ = run_cmd(
        client,
        "ss -tn state established '( sport = :22 )' 2>/dev/null | tail -n +2 | wc -l",
    )
    print(f"当前 SSH 已建立连接数: {out.strip() or '0'}")

    _section("[3] 系统负载 / 内存")
    out, _, _ = run_cmd(client, "uptime; echo ' ----- free ----- '; free -h")
    print(out)

    _section("[4] GPU / NPU 占用")
    out, _, _ = run_cmd(client, "nvidia-smi 2>/dev/null || echo '(无 nvidia-smi)'")
    print(out)
    out, _, _ = run_cmd(client, "npu-smi info 2>/dev/null || echo '(无 npu-smi)'")
    print(out)

    _section("[5] 高 CPU 进程 Top10")
    out, _, _ = run_cmd(
        client,
        "ps -eo pid,user,%cpu,%mem,comm --sort=-%cpu | head -n 11",
    )
    print(out)

    _section("[6] 自动化启动脚本 - /etc/rc.local")
    out, _, _ = run_cmd(client, "cat /etc/rc.local 2>/dev/null || echo '(无 rc.local)'")
    print(out)

    _section("[7] 自动化启动脚本 - systemd running 服务")
    out, _, _ = run_cmd(
        client,
        "systemctl list-units --type=service --state=running --no-pager 2>/dev/null | head -n 30",
    )
    print(out)

    _section("[8] 自动化启动脚本 - crontab(root)")
    out, _, _ = run_cmd(client, "crontab -l 2>/dev/null || echo '(无 crontab)'")
    print(out)

    _section("[9] 自动化启动脚本 - /etc/init.d 自启项")
    out, _, _ = run_cmd(
        client,
        "ls /etc/init.d/ 2>/dev/null | head -n 30 || echo '(无 /etc/init.d)'",
    )
    print(out)

    _section("[10] ~/.bashrc 末尾 20 行(可能的自启)")
    out, _, _ = run_cmd(client, "tail -n 20 ~/.bashrc 2>/dev/null || echo '(无 ~/.bashrc)'")
    print(out)

    print("\n[检测完成]")
