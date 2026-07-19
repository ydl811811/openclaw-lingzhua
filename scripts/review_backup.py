#!/usr/bin/env python3
"""
Loop Engineer - P2 复盘数据备份脚本（按二哥 20:30 拍板）
按 D2 任务规格：NAS 本地 trading-review/ → 本地 bare repo
- 每日 15:30 全量自动
- journal/ 新增 .md 时增量
- manual: --manual 触发

变更日志：
- v1 (2026-07-19) 龙爪拍板：
  - 本地 bare repo（NAS 自建，零外网依赖）
  - 目录名 = 仓库名 = trading-review
  - cron 15:30（盘后）
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WORK_DIR = Path("/home/YDL/.openclaw/workspace/trading-review")
REMOTE_REPO = "/home/YDL/.openclaw/workspace/bare-repos/trading-review.git"
LOG_PREFIX = "[review_backup]"
LOG_FILE = Path("/home/YDL/.openclaw/workspace/logs/review_backup.log")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {LOG_PREFIX} {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, cwd=WORK_DIR):
    """运行 git 命令"""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        shell=isinstance(cmd, str),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def backup():
    """执行备份"""
    if not WORK_DIR.exists():
        log(f"❌ 工作目录不存在: {WORK_DIR}")
        return False

    log(f"=== P2 备份启动 ===")

    # 1. git add -A
    rc, out, err = run(["git", "add", "-A"])
    if rc != 0:
        log(f"❌ git add 失败: {err}")
        return False

    # 2. 检查 diff（无变更则跳过）
    rc, out, err = run(["git", "diff", "--cached", "--quiet"])
    if rc == 0:
        log("✅ 无变更，跳过 commit")
        return True

    # 3. commit
    msg = f"review: {datetime.now().strftime('%Y%m%d_%H%M%S')}"
    rc, out, err = run(["git", "commit", "-m", msg])
    if rc != 0:
        log(f"❌ git commit 失败: {err}")
        return False
    log(f"✅ {out.splitlines()[0] if out else msg}")

    # 4. push
    rc, out, err = run(["git", "push", "origin", "main"])
    if rc != 0:
        log(f"❌ git push 失败: {err}")
        return False
    log(f"✅ push 到 origin/main 成功")

    # 5. 归档触发（如有上月 journal 全部 commit 完成，可选移到 archive/）
    archive_old_journals()

    log("=== 备份完成 ===")
    return True


def archive_old_journals():
    """归档上月 journal（按年月分目录）"""
    import re
    from datetime import timedelta
    journal_dir = WORK_DIR / "journal"
    if not journal_dir.exists():
        return
    cutoff = datetime.now() - timedelta(days=60)  # 60 天前归档
    moved = 0
    for f in journal_dir.glob("*.md"):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\.md$", f.name)
        if not m:
            continue
        try:
            file_date = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if file_date < cutoff:
            month_dir = WORK_DIR / "archive" / f"{m.group(1)}-{m.group(2)}"
            month_dir.mkdir(parents=True, exist_ok=True)
            target = month_dir / f.name
            f.rename(target)
            moved += 1
    if moved:
        log(f"📦 归档 {moved} 个老 journal 到 archive/")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manual", action="store_true", help="手动触发模式（来自 '二哥，触发复盘备份'）")
    p.add_argument("--dry-run", action="store_true", help="只检查，不提交")
    args = p.parse_args()

    if args.manual:
        log("📨 手动触发模式")

    if args.dry_run:
        rc, _, _ = run(["git", "diff", "--stat"])
        print(rc)
        log("(dry-run) 跳过实际 commit")
        return

    backup()


if __name__ == "__main__":
    main()