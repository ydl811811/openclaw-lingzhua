#!/usr/bin/env python3
"""
玲珑股票交易系统 (旧 Loop Engineer, 归档旧名) - P1 历史 K 线缓存脚本（v3，按二哥 20:43 拍板切 baostock）
按 D2 任务规格：缓存 A 股日线，减少 API 调用
存储：SQLite /home/YDL/.openclaw/workspace/loop_engineer/kline_cache.db

变更日志：
- v3 (2026-07-19 20:43) 龙爪拍板：
  - 数据源 akshare → baostock（NAS 独立，零配置，免登录）
  - baostock code 格式：sh.600519 / sz.000001
  - 复权：adjustflag=2（前复权，对应 akshare 的 qfq）
- v2 (2026-07-19 20:42) 龙爪拍板：
  - 起始日 2020 → 2018-01-01
  - 砍掉分钟线（60m/30m）
  - cron 09:00 → 15:30 盘后
- v1 初始版
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import baostock as bs
import pandas as pd

DB_PATH = Path("/home/YDL/.openclaw/workspace/loop_engineer/kline_cache.db")
LOG_PREFIX = "[kline_cache]"
START_DATE = "2018-01-01"  # 二哥拍板：覆盖一轮完整牛熊

# 活跃池（按二哥 P1 规格：约 30 只）
DEFAULT_SYMBOLS = [
    "600519", "601318", "600036", "601398", "600276",
    "000858", "000333", "000651", "000001", "000002",
    "600887", "601166", "600030", "600000", "601288",
    "601012", "601888", "600438", "600900", "601628",
    "513050", "513100", "510300", "510500", "159915",
    "002594", "300750", "002475", "300059", "601899",
]

PERIODS = {
    "daily": {"cache_hours": 24},
}


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {LOG_PREFIX} {msg}", flush=True)


def to_bs_code(symbol: str) -> str:
    """6 位代码 → baostock 格式（sh.600519 / sz.000001）"""
    if symbol.startswith("6") or symbol.startswith("5"):
        return f"sh.{symbol}"
    else:
        return f"sz.{symbol}"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS kline_cache (
            symbol     TEXT NOT NULL,
            period     TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open       REAL, high REAL, low REAL, close REAL,
            volume     REAL, amount REAL,
            PRIMARY KEY (symbol, period, trade_date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS kline_meta (
            symbol       TEXT PRIMARY KEY,
            period       TEXT NOT NULL,
            last_refresh TEXT,
            total_rows   INTEGER,
            cache_hours  INTEGER DEFAULT 24
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_kline_lookup ON kline_cache(symbol, period, trade_date DESC)")
    conn.commit()
    return conn


def need_refresh(conn, symbol: str, period: str, cache_hours: int) -> bool:
    c = conn.cursor()
    c.execute("SELECT last_refresh FROM kline_meta WHERE symbol=? AND period=?", (symbol, period))
    row = c.fetchone()
    if not row or not row[0]:
        return True
    last = datetime.fromisoformat(row[0])
    return datetime.now() - last > timedelta(hours=cache_hours)


def fetch_kline(symbol: str, period: str) -> pd.DataFrame:
    """从 baostock 拉 K 线（带重试）"""
    import time as _t
    bs_code = to_bs_code(symbol)
    last_err = None
    for attempt in range(3):
        try:
            rs = bs.query_history_k_data_plus(
                code=bs_code,
                fields="date,open,high,low,close,volume,amount",
                start_date=START_DATE,
                end_date=datetime.now().strftime("%Y-%m-%d"),
                frequency="d",
                adjustflag="2",  # 前复权
            )
            data = []
            while (rs.error_code == "0") and rs.next():
                data.append(rs.get_row_data())
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data, columns=rs.fields)
            break
        except Exception as e:
            last_err = e
            log(f"  ↻ {symbol} 重试 {attempt + 1}/3: {e}")
            _t.sleep(2 + attempt)
    else:
        raise last_err

    if df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"date": "trade_date"})
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df[["trade_date", "open", "high", "low", "close", "volume", "amount"]]


def save_kline(conn, symbol: str, period: str, df: pd.DataFrame):
    if df.empty:
        return
    c = conn.cursor()
    rows = [(
        symbol, period,
        str(r["trade_date"]), float(r["open"]), float(r["high"]),
        float(r["low"]), float(r["close"]), float(r["volume"]), float(r["amount"]),
    ) for _, r in df.iterrows()]
    c.executemany(
        "INSERT OR REPLACE INTO kline_cache (symbol, period, trade_date, open, high, low, close, volume, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    c.execute(
        "INSERT OR REPLACE INTO kline_meta (symbol, period, last_refresh, total_rows, cache_hours) VALUES (?, ?, ?, ?, ?)",
        (symbol, period, datetime.now().isoformat(), len(rows), PERIODS[period]["cache_hours"]),
    )
    conn.commit()


def refresh_one(conn, symbol: str, period: str) -> bool:
    cache_hours = PERIODS[period]["cache_hours"]
    if not need_refresh(conn, symbol, period, cache_hours):
        return False
    try:
        df = fetch_kline(symbol, period)
        save_kline(conn, symbol, period, df)
        log(f"  ✅ {symbol} {period}: {len(df)} 行")
        return True
    except Exception as e:
        log(f"  ❌ {symbol} {period}: {e}")
        return False


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", help="指定股票代码列表（默认活跃池）")
    p.add_argument("--limit", type=int, default=0, help="限制股数（0=全部）")
    args = p.parse_args()

    symbols = args.symbols or DEFAULT_SYMBOLS
    if args.limit:
        symbols = symbols[: args.limit]

    log(f"=== P1 K线缓存启动（v3 baostock）: {len(symbols)} 只 × 日线 ===")
    log(f"起始日: {START_DATE}")

    # baostock 登录
    lg = bs.login()
    if lg.error_code != "0":
        log(f"❌ baostock 登录失败: {lg.error_msg}")
        sys.exit(1)
    log(f"baostock 登录成功")

    conn = init_db()
    log(f"DB: {DB_PATH}")

    refreshed = 0
    skipped = 0
    for sym in symbols:
        if refresh_one(conn, sym, "daily"):
            refreshed += 1
        else:
            skipped += 1

    bs.logout()
    log(f"=== 完成：refreshed={refreshed}, skipped={skipped} ===")
    log(f"DB 大小：{DB_PATH.stat().st_size / 1024:.1f} KB" if DB_PATH.exists() else "")


if __name__ == "__main__":
    main()