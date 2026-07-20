#!/usr/bin/env python3
"""
玲珑股票交易系统 (旧 Loop Engineer, 归档旧名) - P3 子脚本 2/2：综合信号生成器（按二哥 20:53 拍板 B）
按 D2 任务规格：融合 P0 RSS 新闻因子 + P1 K线技术因子 → 信号记录
落地路径：/home/YDL/.openclaw/workspace/trading-review/signals/signals-YYYYMMDD.json

变更日志：
- v1 (2026-07-19) 龙爪拍板：
  - 双通道融合：P0 新闻（情绪） + P1 K线（技术）
  - 输出：JSON 信号记录（兼容 P3 shadow_compare）
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/home/YDL/.openclaw/workspace/loop_engineer/kline_cache.db")
NEWS_CACHE_DIR = Path("/home/YDL/.openclaw/workspace/loop_engineer/news_cache")
OUTPUT_DIR = Path("/home/YDL/.openclaw/workspace/trading-review/signals")
LOG_PREFIX = "[signals_generator]"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {LOG_PREFIX} {msg}", flush=True)


def load_news_today() -> dict:
    """加载 P0 今日新闻，按 source 聚合"""
    today = datetime.now().strftime("%Y-%m-%d")
    cache_file = NEWS_CACHE_DIR / f"{today}.json"
    if not cache_file.exists():
        log(f"⚠️ 今日新闻缓存不存在: {cache_file}")
        return {}
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    by_source = {}
    code_mentions = {}
    for item in data.get("items", []):
        src = item.get("source", "unknown")
        by_source.setdefault(src, 0)
        by_source[src] += 1
        for code in item.get("codes", []):
            code_mentions[code] = code_mentions.get(code, 0) + 1
    return {
        "total": data.get("total_count", 0),
        "by_source": by_source,
        "top_codes": sorted(code_mentions.items(), key=lambda x: x[1], reverse=True)[:10],
        "fetch_time": data.get("fetch_time", ""),
    }


def compute_tech_factors(symbol: str, conn: sqlite3.Connection) -> dict:
    """从 P1 K线缓存算技术因子（MA5/MA20/趋势/位置）"""
    c = conn.cursor()
    c.execute("""
        SELECT trade_date, close, volume FROM kline_cache
        WHERE symbol=? AND period='daily'
        ORDER BY trade_date DESC LIMIT 30
    """, (symbol,))
    rows = c.fetchall()
    if len(rows) < 20:
        return {"symbol": symbol, "error": "数据不足 20 日"}

    closes = [r[1] for r in rows][::-1]  # 旧的在前，新的在后
    dates = [r[0] for r in rows][::-1]
    volumes = [r[2] for r in rows][::-1]

    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    last = closes[-1]
    pos_vs_ma5 = (last - ma5) / ma5 * 100
    pos_vs_ma20 = (last - ma20) / ma20 * 100

    # 趋势判断
    if last > ma5 > ma20:
        trend = "uptrend"
    elif last < ma5 < ma20:
        trend = "downtrend"
    else:
        trend = "sideways"

    # 量比（最近 5 日 vs 前 20 日均量）
    vol_recent = sum(volumes[-5:]) / 5
    vol_avg = sum(volumes[-20:]) / 20
    vol_ratio = vol_recent / vol_avg if vol_avg > 0 else 0

    return {
        "symbol": symbol,
        "last_date": dates[-1],
        "last_close": round(last, 2),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "pct_above_ma5": round(pos_vs_ma5, 2),
        "pct_above_ma20": round(pos_vs_ma20, 2),
        "trend": trend,
        "volume_ratio_5d_vs_20d": round(vol_ratio, 2),
    }


def compute_market_sentiment(news: dict) -> dict:
    """综合市场情绪（基于 P0 新闻）"""
    total = news.get("total", 0)
    if total == 0:
        return {"level": "neutral", "score": 0, "reason": "无新闻数据"}

    # 简单规则：新闻量大 + 多股被提及 → 情绪高
    code_mention_count = len(news.get("top_codes", []))
    if total > 100 and code_mention_count > 5:
        level, score = "high", 2
    elif total > 50:
        level, score = "moderate", 1
    elif total < 20:
        level, score = "low", -1
    else:
        level, score = "neutral", 0

    return {
        "level": level,
        "score": score,
        "news_total": total,
        "top_mentioned_codes": news.get("top_codes", [])[:5],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", help="指定股票代码列表")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="信号日期 YYYYMMDD")
    args = p.parse_args()

    log("=== P3 信号生成启动 ===")

    # 1. 加载 P0 新闻
    news = load_news_today()
    market = compute_market_sentiment(news)
    log(f"市场情绪: {market['level']} (score={market['score']}, news={market.get('news_total', 0)})")

    # 2. 默认盯 30 只活跃池
    default_symbols = [
        "600519", "601318", "600036", "601398", "600276",
        "000858", "000333", "000651", "000001", "000002",
        "600887", "601166", "600030", "600000", "601288",
        "601012", "601888", "600438", "600900", "601628",
        "513050", "513100", "510300", "510500", "159915",
        "002594", "300750", "002475", "300059", "601899",
    ]
    symbols = args.symbols or default_symbols

    # 3. 加载 K线缓存
    if not DB_PATH.exists():
        log(f"❌ K线缓存 DB 不存在: {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)

    signals = []
    for sym in symbols:
        tech = compute_tech_factors(sym, conn)
        if "error" in tech:
            continue
        # 信号合成：news_mention + trend + market_score
        news_hit = next((c for c, n in news.get("top_codes", []) if c == sym), None)
        signal = {
            "code": sym,
            "trend": tech.get("trend"),
            "pct_above_ma5": tech.get("pct_above_ma5"),
            "pct_above_ma20": tech.get("pct_above_ma20"),
            "volume_ratio_5d_vs_20d": tech.get("volume_ratio_5d_vs_20d"),
            "last_close": tech.get("last_close"),
            "last_date": tech.get("last_date"),
            "news_mentioned": news_hit is not None,
            "news_mention_count": news.get("top_codes", []).count(sym),
            "market_sentiment": market["level"],
            "generated_at": datetime.now().isoformat(),
        }
        # 综合信号（简单打分）
        score = 0
        if signal["trend"] == "uptrend":
            score += 1
        elif signal["trend"] == "downtrend":
            score -= 1
        score += market["score"]
        if signal["news_mentioned"]:
            score += 1
        if signal["volume_ratio_5d_vs_20d"] > 1.5:
            score += 1
        elif signal["volume_ratio_5d_vs_20d"] < 0.7:
            score -= 1
        signal["composite_score"] = score
        signals.append(signal)

    conn.close()

    # 4. 按综合分排序
    signals.sort(key=lambda x: x["composite_score"], reverse=True)

    payload = {
        "date": args.date,
        "generated_at": datetime.now().isoformat(),
        "market_sentiment": market,
        "news_summary": {
            "total": news.get("total", 0),
            "by_source": news.get("by_source", {}),
            "top_codes": news.get("top_codes", [])[:10],
        },
        "signal_count": len(signals),
        "signals": signals,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"signals-{args.date}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"✅ 写入 {out_path}")
    log(f"信号总数: {len(signals)}, Top5:")
    for s in signals[:5]:
        log(f"  - {s['code']} trend={s['trend']} score={s['composite_score']} news={s['news_mentioned']}")


if __name__ == "__main__":
    main()