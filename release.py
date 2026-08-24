"""一键发布脚本: git 提交推送 + build exe + 注入版本号 + 创建 GitHub Release

用法:
    python release.py                  # 全流程: git提交推送 + build + 发布
    python release.py -m "修复..."     # 指定 commit 消息
    python release.py --build-only     # 只 build, 不 git 提交也不发布
    python release.py --skip-git       # 跳过 git 提交推送 (已手动提交时)
    python release.py --notes "修复..." # 指定 Release notes
    python release.py --notes-file notes.txt  # 从 UTF-8 文件读取 Release notes (支持多行)

前置条件:
    1. gh CLI 已安装并登录 (gh auth login)
    2. PyInstaller 已安装 (pip install pyinstaller)

流程:
    1. git add + commit (自动消息或 -m 指定) + push origin master
    2. git rev-parse --short=12 HEAD → 获取 commit hash
    3. pyinstaller build.spec → 打包 exe 到 dist/
    4. 写 dist/_version.txt (commit hash)
    5. gh release create v{date}_{hash} → 上传 exe + _version.txt
"""

import argparse
import os
import subprocess
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_DIST_DIR = os.path.join(_PROJECT_ROOT, "dist")
_EXE_NAME = "devboard_toolkit.exe"
_RUNNER_NAME = "per_board_runner.exe"
_VERSION_FILE = "_version.txt"
_USER_YAML = "config_user.yaml"
_SYSTEM_YAML = "config_system.yaml"


def _run(cmd, cwd=None, check=True):
    """执行命令, 实时输出"""
    print(f"  $ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    result = subprocess.run(
        cmd, cwd=cwd or _PROJECT_ROOT,
        shell=isinstance(cmd, str),
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败: {cmd}")
    return result


def _git_commit_push(commit_msg=""):
    """git add + commit + push, 返回 True/False"""
    # 检查有无变更 (暂存区 + 工作区)
    status = _run(["git", "status", "--porcelain"], check=False)
    changes = status.stdout.strip()

    if not changes:
        print("  [i] 无未提交变更, 跳过 git commit")
    else:
        # 有变更 → add + commit
        _run(["git", "add", "-A"], check=True)

        if not commit_msg:
            # 自动生成 commit 消息: 列出变更文件摘要
            n_changed = len([l for l in changes.splitlines() if l.strip()])
            commit_msg = f"update: {n_changed} files ({time.strftime('%Y%m%d %H:%M')})"

        _run(["git", "commit", "-m", commit_msg], check=True)
        print(f"  [+] git commit: {commit_msg}")

    # push (无论有无新 commit, 确保远端同步)
    push_result = _run(["git", "push", "origin", "master"], check=False)
    if push_result.returncode != 0:
        # push 可能因为远端有新 commit 而失败 → 尝试 pull --rebase 再 push
        print("  [!] push 失败, 尝试 pull --rebase 后重试...")
        _run(["git", "pull", "--rebase", "origin", "master"], check=False)
        push_result = _run(["git", "push", "origin", "master"], check=False)
        if push_result.returncode != 0:
            print("  [!] push 仍失败, 请手动检查后重试")
            return False

    print("  [+] git push 完成")
    return True


def _get_git_hash():
    """获取当前 commit 的 short hash (12位)"""
    result = _run(["git", "rev-parse", "--short=12", "HEAD"])
    return result.stdout.strip()


def _get_git_branch():
    """获取当前分支名"""
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


def _build_exe():
    """运行 PyInstaller 打包"""
    print("\n[2] PyInstaller 打包...")
    spec_path = os.path.join(_PROJECT_ROOT, "build.spec")
    _run([sys.executable, "-m", "PyInstaller", spec_path, "--noconfirm"])
    print(f"  [+] 打包完成: {_DIST_DIR}")


def _write_version_file(git_hash):
    """写 _version.txt 到 dist/ (随 exe 分发)"""
    print(f"  [+] 注入版本号: {git_hash}")
    ver_path = os.path.join(_DIST_DIR, _VERSION_FILE)
    with open(ver_path, "w", encoding="utf-8") as f:
        f.write(git_hash)
    print(f"  [+] 已写入: {ver_path}")


def _copy_yamls_to_dist(include_user: bool = False, include_system: bool = False):
    """根据发布参数, 把指定 yaml 从项目根目录复制到 dist/

    Args:
        include_user:   是否复制 config_user.yaml (用户配置)
        include_system: 是否复制 config_system.yaml (系统配置)
    """
    import shutil
    os.makedirs(_DIST_DIR, exist_ok=True)
    flags = {_USER_YAML: include_user, _SYSTEM_YAML: include_system}
    for yname, enabled in flags.items():
        if not enabled:
            print(f"  [-] 未勾选包含 {yname}, 跳过")
            continue
        src = os.path.join(_PROJECT_ROOT, yname)
        dst = os.path.join(_DIST_DIR, yname)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            print(f"  [+] 复制到 dist/: {yname}")
        else:
            print(f"  [!] 项目根目录无 {yname}, 跳过复制")


def _create_release(git_hash, notes="", draft=False):
    """用 gh CLI 创建 GitHub Release 并上传 assets"""
    tag = f"v{time.strftime('%Y%m%d_%H%M')}_{git_hash[:8]}"
    title = f"v{time.strftime('%Y%m%d')} ({git_hash[:8]})"

    if not notes:
        # 自动从最近 commits 生成 notes
        result = _run([
            "git", "log", "--oneline", "-10",
            "--pretty=format:- %s (%h)"
        ], check=False)
        notes = result.stdout.strip() if result.stdout else ""

    print(f"\n[4] 创建 GitHub Release: {tag}")
    print(f"    标题: {title}")

    # 检查 gh 是否可用
    try:
        _run(["gh", "--version"], check=True)
    except Exception:
        print("  [!] gh CLI 不可用, 请先安装并登录:")
        print("      winget install GitHub.cli")
        print("      gh auth login")
        return False

    # 构建 gh release create 命令
    exe_path = os.path.join(_DIST_DIR, _EXE_NAME)
    runner_path = os.path.join(_DIST_DIR, _RUNNER_NAME)
    ver_path = os.path.join(_DIST_DIR, _VERSION_FILE)
    user_yaml_path = os.path.join(_DIST_DIR, _USER_YAML)
    system_yaml_path = os.path.join(_DIST_DIR, _SYSTEM_YAML)

    # 检查文件存在: 两个 exe + _version.txt 必更 (缺失则终止)
    assets = []
    missing_required = []
    for label, fpath in [(_EXE_NAME, exe_path), (_RUNNER_NAME, runner_path), (_VERSION_FILE, ver_path)]:
        if os.path.isfile(fpath):
            size_mb = os.path.getsize(fpath) // 1024 // 1024 if label.endswith(".exe") else 0
            size_str = f" ({size_mb} MB)" if size_mb else ""
            assets.append(fpath)
            print(f"  [+] 上传: {label}{size_str}")
        else:
            missing_required.append(label)
    if missing_required:
        print(f"  [!] 缺失必更新文件: {', '.join(missing_required)}, 终止发布")
        return False

    # YAML 可选: 有就上传 (用户端 GUI 复选框控制是否替换)
    for label, fpath in [(_USER_YAML, user_yaml_path), (_SYSTEM_YAML, system_yaml_path)]:
        if os.path.isfile(fpath):
            assets.append(fpath)
            print(f"  [+] 上传: {label}")
        else:
            print(f"  [-] dist/ 无 {label}, 跳过 (不影响发布)")

    cmd = [
        "gh", "release", "create", tag,
        "--title", title,
        "--notes", notes,
    ]
    if draft:
        cmd.append("--draft")
    else:
        cmd.append("--latest")
    cmd.extend(assets)

    try:
        _run(cmd)
        print(f"\n  [+] Release 创建成功: {tag}")
        return True
    except Exception as e:
        print(f"\n  [!] Release 创建失败: {e}")
        print("  手动创建:")
        print(f"    gh release create {tag} {' '.join(assets)} "
              f'--title "{title}" --notes "..."')
        return False


def main():
    parser = argparse.ArgumentParser(description="git提交推送 + Build + 发布 exe 到 GitHub Releases")
    parser.add_argument("-m", "--message", default="",
                        help="git commit 消息 (默认自动生成)")
    parser.add_argument("--build-only", action="store_true",
                        help="只打包, 不 git 提交也不发布")
    parser.add_argument("--skip-git", action="store_true",
                        help="跳过 git 提交推送 (已手动提交时)")
    parser.add_argument("--notes", default="",
                        help="Release notes (默认从 git log 自动生成)")
    parser.add_argument("--notes-file", default="",
                        help="从文件读取 Release notes (UTF-8, 支持\\n换行; 与 --notes 二选一, --notes-file 优先)")
    parser.add_argument("--draft", action="store_true",
                        help="创建为草稿 (不公开)")
    parser.add_argument("--skip-build", action="store_true",
                        help="跳过 PyInstaller (已有 dist/ 产物时)")
    parser.add_argument("--include-user-yaml", action="store_true",
                        help=f"发布时包含 {_USER_YAML} (用户端会自动覆盖该文件, 慎用!)")
    parser.add_argument("--include-system-yaml", action="store_true",
                        help=f"发布时包含 {_SYSTEM_YAML} (用户端会自动覆盖该文件)")
    args = parser.parse_args()

    # notes 解析: --notes-file 优先于 --notes
    notes = args.notes
    if args.notes_file:
        try:
            with open(args.notes_file, "r", encoding="utf-8") as f:
                notes = f.read().strip()
            print(f"[i] 已从 {args.notes_file} 读取 Release notes ({len(notes)} 字符)")
        except Exception as e:
            print(f"[!] 读取 --notes-file 失败: {e}")
            return 1

    print("=" * 60)
    print("  DevBoard Toolkit — Build & Release")
    print("=" * 60)
    print(f"  包含 {_USER_YAML}:    {'是 (会覆盖用户本地, 慎用)' if args.include_user_yaml else '否'}")
    print(f"  包含 {_SYSTEM_YAML}:  {'是' if args.include_system_yaml else '否'}")
    print("=" * 60)

    # --build-only: 只打包, 不 git 也不发布
    if args.build_only:
        if not args.skip_build:
            _build_exe()
        git_hash = _get_git_hash()
        _write_version_file(git_hash)
        _copy_yamls_to_dist(
            include_user=args.include_user_yaml,
            include_system=args.include_system_yaml,
        )
        print(f"\n  exe 在: {_DIST_DIR}")
        print(f"  版本号: {git_hash}")
        return 0

    # 1. git commit + push
    if args.skip_git:
        print("\n[1] 跳过 git 提交推送 (--skip-git)")
    else:
        print("\n[1] git 提交推送...")
        ok = _git_commit_push(commit_msg=args.message)
        if not ok:
            print("[!] git push 失败, 流程终止")
            return 1

    # 2. 获取 git hash (commit 后的最新 hash)
    print("\n[2] 获取版本信息...")
    git_hash = _get_git_hash()
    branch = _get_git_branch()
    print(f"  commit: {git_hash}")
    print(f"  branch: {branch}")

    # 3. 写仓库根目录 _version.txt + 二次 commit + push (确保 raw.githubusercontent.com 拉到最新)
    root_ver_path = os.path.join(_PROJECT_ROOT, _VERSION_FILE)
    with open(root_ver_path, "w", encoding="utf-8") as f:
        f.write(git_hash)
    print(f"  [+] 写入仓库根目录: {root_ver_path}")

    if not args.skip_git:
        _run(["git", "add", _VERSION_FILE], check=True)
        _run(["git", "commit", "-m", f"chore: update _version.txt ({git_hash[:7]})"], check=False)
        _run(["git", "push", "origin", "master"], check=False)
        print(f"  [+] _version.txt 已推送 (raw.githubusercontent.com 可拉取)")

    # 4. 打包
    if not args.skip_build:
        _build_exe()
    else:
        print("\n[3] 跳过打包 (--skip-build)")

    # 5. 注入版本号 (dist/) + 复制 yaml(按参数决定)
    _write_version_file(git_hash)
    _copy_yamls_to_dist(
        include_user=args.include_user_yaml,
        include_system=args.include_system_yaml,
    )

    # 6. 发布
    ok = _create_release(git_hash, notes=notes, draft=args.draft)
    if ok:
        print("\n" + "=" * 60)
        print("  发布完成！用户端 exe 启动后会自动检测到新版本。")
        print("=" * 60)
    else:
        print("\n[!] 发布未完成, 请检查上方错误信息")

    return 0


if __name__ == "__main__":
    sys.exit(main())
