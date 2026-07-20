#!/usr/bin/env python3
"""
玲珑股票交易系统 (旧 Loop Engineer, 归档旧名) - P3 子脚本 1/2：持仓快照导出器（按二哥 20:53 拍板 B）
按 D2 任务规格：从 a_stock_plan/交易记录台账.md 自动导出持仓快照
落地路径：/home/YDL/.openclaw/workspace/trading-review/positions/snapshot-YYYYMMDD.csv

变更日志：
- v1 (2026-07-19) 龙爪拍板：
  - 源：a_stock_plan/交易记录台账.md（唯一权威持仓源）
  - 输出：CSV（兼容 P3 shadow_compare 读取）
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

SOURCE_MD = Path("/home/YDL/.openclaw/workspace/a_stock_plan/交易记录台账.md")
OUTPUT_DIR = Path("/home/YDL/.openclaw/workspace/trading-review/positions")
LOG_PREFIX = "[snapshot_export]"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {LOG_PREFIX} {msg}", flush=True)


def parse_holdings_table(md_text: str) -> list:
    """
    从台账.md 解析「当前持仓」Markdown 表格
    跳过带删除线（~~xxx~~）的清仓记录
    """
    holdings = []
    # 匹配 markdown 表格行
    # | **长江电力** | **600900** | **200** | **¥26.76** | ...
    row_re = re.compile(
        r"^\|\s*(?:\*\*)?(?P<name>[^*|]+?)(?:\*\*)?\s*\|\s*"
        r"(?:\*\*)?(?P<code>\d{6})(?:\*\*)?\s*\|\s*"
        r"(?:\*\*)?(?P<shares>\d+)(?:\*\*)?\s*\|\s*"
        r"(?:\*\*)?[¥￥]?(?P<cost>[\d.]+)(?:\*\*)?\s*\|\s*"
        r"(?:\*\*)?[¥￥]?(?P<price>[\d.]+)(?:\*\*)?\s*\|\s*"
        r"(?:\*\*)?[¥￥]?(?P<mv>[\d,.]+)(?:\*\*)?\s*\|\s*"
        r"(?:\*\*)?(?P<pl>[+\-¥￥\d,.]+)(?:\*\*)?\s*\|\s*"
        r"(?:\*\*)?(?P<pl_pct>[+\-\d.%]+)(?:\*\*)?\s*\|"
    )
    for line in md_text.split("\n"):
        # 跳过删除线（已清仓）
        if "~~" in line:
            continue
        m = row_re.match(line.strip())
        if not m:
            continue
        holdings.append({
            "name": m.group("name").strip(),
            "code": m.group("code"),
            "shares": int(m.group("shares")),
            "cost": float(m.group("cost")),
            "current_price": float(m.group("price")),
            "market_value": float(m.group("mv").replace(",", "")),
            "profit_loss": m.group("pl").replace("¥", "").replace("￥", "").replace(",", "").strip(),
            "profit_loss_pct": m.group("pl_pct").strip(),
            "snapshot_time": datetime.now().isoformat(),
        })
    return holdings


def write_csv(holdings: list, target_date: str) -> Path:
    """写出 CSV"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"snapshot-{target_date}.csv"

    import csv
    fieldnames = ["snapshot_time", "code", "name", "shares", "cost",
                  "current_price", "market_value", "profit_loss", "profit_loss_pct"]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for h in holdings:
            w.writerow(h)
    return csv_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="快照日期 YYYYMMDD")
    args = p.parse_args()

    if not SOURCE_MD.exists():
        log(f"❌ 台账文件不存在: {SOURCE_MD}")
        sys.exit(1)

    text = SOURCE_MD.read_text(encoding="utf-8")
    holdings = parse_holdings_table(text)
    log(f"解析到 {len(holdings)} 个持仓")

    if not holdings:
        log("⚠️ 无持仓，跳过导出（清仓状态或台账格式变化）")
        return

    csv_path = write_csv(holdings, args.date)
    log(f"✅ 写入 {csv_path}")
    for h in holdings:
        log(f"  - {h['code']} {h['name']} {h['shares']}股 ¥{h['cost']} → ¥{h['current_price']}")


if __name__ == "__main__":
    main()