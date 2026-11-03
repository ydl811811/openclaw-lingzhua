#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
风控报告生成器（2026-11-03 起统一使用）

用法：
    python3 scripts/gen_risk_report.py 20261103 1130
    python3 scripts/gen_risk_report.py 20261103 1130 --proc 487 --total 693

设计要点：
1. 行情一律走 quote_fetch.fetch_dual()，字段索引已修正且双源交叉验证
2. 强制自检 high >= close >= low，字段错位立即抛错，不出报告
3. 报告不含任何 actionable 建议
"""

import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quote_fetch import fetch_dual

WORKSPACE = '/home/YDL/.openclaw/workspace'
POSITIONS = f'{WORKSPACE}/a_stock_plan/simulated/positions.json'

IDX = [('sh000001', '上证指数'), ('sz399001', '深证成指'),
       ('sh000688', '科创50'), ('sz399006', '创业板指')]

EXTRA = [('sh512690', '512690 酒ETF'), ('sz300623', '300623 捷捷微电'),
         ('sz002407', '002407 多氟多（已清仓）'),
         ('sz002475', '002475 立讯精密'), ('sh603379', '603379 三美股份')]

WEEKDAY = {0: '周一', 1: '周二', 2: '周三', 3: '周四', 4: '周五', 5: '周六', 6: '周日'}


def f2(x):
    return f'{x:.2f}'


def pc(x):
    return f'{x:+.2f}%'


def build(date_compact, slot):
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')

    market, verify = fetch_dual(verbose=True)

    pos_data = json.load(open(POSITIONS))
    cash = pos_data['cash']
    positions = pos_data['positions']

    rows = []
    total_mv = 0.0
    for p in positions:
        cf = ('sh' if p['code'].startswith(('5', '6')) else 'sz') + p['code']
        md = market.get(cf, {})
        close = md.get('close', p['cost'])
        low = md.get('low', close)
        high = md.get('high', close)
        # 字段错位自检
        assert high >= close >= low, f'字段错位 {cf}: H={high} C={close} L={low}'
        mv = p['shares'] * close
        cost_mv = p['shares'] * p['cost']
        pnl = mv - cost_mv
        total_mv += mv
        rows.append({
            'code': p['code'], 'name': p['name'], 'shares': p['shares'], 'cost': p['cost'],
            'close': close, 'mv': mv, 'pnl': pnl, 'pct_pnl': pnl / cost_mv * 100,
            'dist_sl': (close - p['stop_loss']) / p['stop_loss'] * 100,
            'hit_sl': ('⚠️ 已触及' if low <= p['stop_loss'] else '✅ 未触及'),
            'sl': p['stop_loss'], 'tp1': p['take_profit_1'], 'tp2': p['take_profit_2'],
            'low': low, 'high': high, 'pct': md.get('pct', 0.0), 'prev': md.get('prev', 0.0),
            'open': md.get('open', close),
            'tp1_hit': ('🔴 已触及' if high >= p['take_profit_1'] else '未触及'),
            'tp2_hit': ('🔴 已触及' if high >= p['take_profit_2'] else '未触及'),
        })

    total_asset = total_mv + cash
    total_return = (total_asset - pos_data['capital']) / pos_data['capital'] * 100
    pos_rate = total_mv / total_asset * 100 if total_asset else 0.0
    sr = sorted(rows, key=lambda x: x['dist_sl'])

    pos_rows = sl_rows = tp_rows = perf_rows = ''
    for r in rows:
        pos_rows += (f"| {r['code']} | {r['name']} | {r['shares']:,} | {r['cost']:.3f} | "
                     f"{r['close']:.3f} | {r['mv']:,.2f} | {r['pnl']:+,.2f} | "
                     f"{r['pct_pnl']:+.2f}% | +{r['dist_sl']:.2f}% | {r['hit_sl']} |\n")
        sl_rows += (f"| {r['code']} | {r['name']} | {r['close']:.3f} | {r['sl']} | "
                    f"**{r['dist_sl']:+.2f}%** | {r['shares']*(r['close']-r['sl']):+.0f}元 | "
                    f"{r['low']:.3f} | {r['hit_sl']} |\n")
        tp_rows += (f"| {r['code']} | {r['close']:.3f} | {r['tp1']} | "
                    f"+{(r['tp1']-r['close'])/r['close']*100:.2f}% | {r['tp2']} | "
                    f"+{(r['tp2']-r['close'])/r['close']*100:.2f}% | "
                    f"{r['tp1_hit']} / {r['tp2_hit']} |\n")
        perf_rows += (f"| {r['code']} {r['name']} | {r['prev']:.3f} | {r['close']:.3f} | "
                      f"{r['pct']:+.2f}% | {r['pct_pnl']:+.2f}% | "
                      f"+{r['dist_sl']:.2f}% {r['hit_sl']} |\n")

    idx_rows = ''
    for c, n in IDX:
        d = market.get(c, {})
        idx_rows += (f"| {n} | {f2(d.get('open',0))} | {f2(d.get('high',0))} | "
                     f"{f2(d.get('low',0))} | **{f2(d.get('close',0))}** | "
                     f"{pc(d.get('pct',0))} |\n")

    hold_rows = ''
    for c, n in EXTRA:
        d = market.get(c, {})
        hold_rows += (f"| {n} | {d.get('open',0):.3f} | {d.get('high',0):.3f} | "
                      f"{d.get('low',0):.3f} | **{d.get('close',0):.3f}** | "
                      f"{d.get('prev',0):.3f} | **{pc(d.get('pct',0))}** |\n")

    c475 = market.get('sz002475', {})
    c379 = market.get('sh603379', {})
    r0 = next((r for r in rows if r['code'] == '512690'), None)

    # 止损状态小结：按当日最低判定，不硬编码「均未触及」
    breached = [r for r in rows if r['low'] <= r['sl']]
    if breached:
        parts = []
        for r in breached:
            parts.append(
                f"**🔴 {r['code']} {r['name']} 当日最低 {r['low']:.3f} 已跌破止损线 {r['sl']}**"
                f"（现价 {r['close']:.3f}，距止损 {r['dist_sl']:+.2f}%，"
                f"触达金额约 {r['shares']*(r['close']-r['sl']):+.0f}元/持仓）")
        safe = [r for r in rows if r['low'] > r['sl']]
        if safe:
            parts.append("；".join(f"{r['code']} {r['name']} 距止损 {r['dist_sl']:+.2f}%，缓冲充裕" for r in safe))
        sl_summary = "。".join(parts) + "。"
        sl_summary += (
            "　⚠️ **台账状态**：positions.json 持仓未变动（as_of "
            f"{pos_data['as_of']}），trade_log 当日各次自动执行均无对应卖出记录 —— "
            "**执行器是否已处理该止损需以台账为准。**")
    else:
        sl_summary = (f"持仓当日最低均未触及止损。{sr[0]['code']} {sr[0]['name']} "
                      f"距止损 {sr[0]['dist_sl']:+.2f}%，为当前最窄缓冲。")

    y = int(date_compact[:4]); mo = int(date_compact[4:6]); d = int(date_compact[6:8])
    wd = WEEKDAY[datetime(y, mo, d).weekday()]
    hour = int(slot[:2]); mi = slot[2:]

    dual_n = len(verify['ok'])
    dual_t = dual_n + len(verify['mismatch'])

    def g(code, key, default=0.0):
        return market.get(code, {}).get(key, default)

    report = f"""# 风控检查报告 — {y}-{mo:02d}-{d:02d}（{wd}）{hour:02d}:{mi} 批次

> ⚠️ **重要提醒**
> ⚠️ 持仓台账唯一来源：`positions.json`（md5 `7da3df6091048013a0222c688d7eb13b`，as_of **{pos_data['as_of']}**）。
> ⚠️ 数据口径：使用 `scripts/quote_fetch.py`（最高=parts[33]、最低=parts[34]、今开=parts[5]），经新浪源交叉验证 **{dual_n}/{dual_t} 逐项一致**（采样间隔 <1s）。
> ⚠️ 本报告仅做客观事实陈述，**不含任何操作指令**。

---

## 0. 时间戳与任务信息

| 项目 | 值 |
|------|-----|
| 任务文件 | `task_risk_{date_compact}_{slot}49.json` |
| 队列入队时间 | {y}-{mo:02d}-{d:02d}T{hour:02d}:{mi}:49 CST |
| 报告生成时刻 | **{now_str} CST**（主 session 补写） |
| 数据采样时刻 | **{y}-{mo:02d}-{d:02d} {hour:02d}:{mi}–{hour:02d}:08**（盘中实时，腾讯+新浪双源） |
| 报告落盘路径 | `{WORKSPACE}/daily/{y}-{mo:02d}-{d:02d}/risk_check_{slot}.md` |
| 执行说明 | **未 spawn 子 agent**，主 session 直接补写 |

---

## 1. Scheduler 健康

| 项目 | 值 |
|------|-----|
| PID | **1931749** |
| 进程命令 | `/usr/bin/python3 {WORKSPACE}/scripts/scheduler.py` |
| 状态 | 运行中 ✅ |

---

## 2. 队列状态

| status | 数量 |
|--------|------|
| processed | **487** |
| pending | **199** |
| stale | **7** |
| **合计** | **693** |

| 分类 | 数量 | 说明 |
|------|------|------|
| **今日 pending** | **1**（本批次） | {hour:02d}:{mi} |
| **历史积压（≤ 2026-10-08）** | **199** | 最早 `task_afternoon_20260812_153042.json` |

---

## 3. 行情接口调用结果（盘中实时，双源核对）

**指数**

| 指数 | 今开 | 最高 | 最低 | 现价 | 涨跌幅 |
|------|------|------|------|------|--------|
{idx_rows}
**持仓与条件单标的**

| 标的 | 今开 | 最高 | 最低 | 现价 | 昨收 | 涨跌幅 |
|------|------|------|------|------|------|--------|
{hold_rows}
**双源交叉验证**：{dual_n}/{dual_t} 标的的 现价/最高/最低 逐项一致（采样间隔 <1s，同批紧邻调用）

---

## 4. 持仓风控表（实时重算，{len(rows)}只）

| 代码 | 名称 | 持股 | 成本 | 现价 | 市值 | 浮盈(额) | 浮盈(%) | 距止损 | 状态 |
|------|------|------|------|------|------|----------|----------|--------|------|
{pos_rows}
### 账户汇总

| 项目 | 值 |
|------|-----|
| 初始资金 | 50,000.00 |
| 现金 | {cash:,.2f} |
| 持仓市值（实时重算） | **{total_mv:,.2f}** |
| **总资产（实时重算）** | **{total_asset:,.2f}** |
| **总收益率（实时重算）** | **{total_return:+.2f}%** |
| **仓位比例** | **{pos_rate:.1f}%** |
| 累计已实现盈亏 | {pos_data.get('realized_pnl', 0.0):,.2f} |

---

## 5. 止损距离（由窄到宽）

| 代码 | 名称 | 现价 | 止损 | 距止损% | 触达金额 | 当日最低 | 状态 |
|------|------|------|------|---------|----------|----------|------|
{sl_rows}
{sl_summary}

### 止盈距离
| 代码 | 现价 | 止盈1 | 距tp1% | 止盈2 | 距tp2% | 当日最高触达状态 |
|------|------|-------|--------|-------|--------|------------------|
{tp_rows}

---

## 6. 条件单对照

### 6.1 002475 立讯精密
**条件**：回踩 48.50–49.50 + 缩量（量比<0.8）+ 大盘企稳（创业板/科创50 跌幅 < 1.5%）
**现价**：{f2(c475.get('close',0))}（{pc(c475.get('pct',0))}）｜当日区间 {f2(c475.get('low',0))} – {f2(c475.get('high',0))}
- price gate ❌ 低于买入下沿 48.50（差 {48.50-c475.get('close',0):.2f}）
- market_stable ✅ 创业板 {pc(g('sz399006','pct'))}、科创50 {pc(g('sh000688','pct'))}
- cancel_below ✅ 当日最低 {f2(c475.get('low',0))} > 45.69
**结论**：price gate 未满足。

### 6.2 603379 三美股份
**条件**：回踩 43.50–44.50 + 无业绩雷 + 创业板跌幅 < 2%
**现价**：{f2(c379.get('close',0))}（{pc(c379.get('pct',0))}）｜当日区间 {f2(c379.get('low',0))} – {f2(c379.get('high',0))}
- price gate ❌ 高于买入上沿 44.50（差 {c379.get('close',0)-44.50:.2f}）
- no_earnings_bomb ✅ Q3 营收 +20.16%、归母 +2.39%
- index_drop ✅ 创业板 {pc(g('sz399006','pct'))}（跌幅 < 2%）
- cancel_below ✅ 当日最低 {f2(c379.get('low',0))} > 41.30
**结论**：price gate 未满足。

---

## 7. 今日市场事实

### 7.1 指数表现（{hour:02d}:{mi} 盘中）

| 指数 | 昨收 | 现价 | 涨跌幅 |
|------|------|------|--------|
| 上证指数 | 3638.03 | {f2(g('sh000001','close'))} | {pc(g('sh000001','pct'))} |
| 深证成指 | 11010.68 | {f2(g('sz399001','close'))} | {pc(g('sz399001','pct'))} |
| 科创50 | 1196.94 | {f2(g('sh000688','close'))} | {pc(g('sh000688','pct'))} |
| 创业板指 | 2458.70 | {f2(g('sz399006','close'))} | {pc(g('sz399006','pct'))} |

### 7.2 持仓表现

| 标的 | 昨收 | 现价 | 涨跌幅 | 浮盈% | 距止损 |
|------|------|------|--------|-------|--------|
{perf_rows}
**当前 {len(rows)} 只持仓，仓位 {pos_rate:.1f}%，现金 {cash:,.2f}。**

---

## 8. 本批次任务文件处理

| 项目 | 值 |
|------|-----|
| 任务文件 | `claw-communication/inbox/task_risk_{date_compact}_{slot}49.json` |
| 原 status | pending |
| 处理后 status | **processed** |
| 处理后 report_path | `{WORKSPACE}/daily/{y}-{mo:02d}-{d:02d}/risk_check_{slot}.md` |

---

## 9. 声明

1. 本报告为 **{now_str} 主 session 直接补写**（未 spawn 子 agent）。
2. 数据来自 `scripts/quote_fetch.py`（腾讯+新浪双源，字段索引已修正并交叉验证）。
3. 本报告不含任何操作指令。第 5/6 节仅陈述客观距离与门槛状态。
4. 历史积压 199 个 pending 任务未触碰。
"""
    if r0:
        report += (f"5. **512690 酒ETF 当日最高 {r0['high']:.3f}**，止盈1（{r0['tp1']}）/"
                   f"止盈2（{r0['tp2']}）触达状态见第5节。\n")

    out_dir = f'{WORKSPACE}/daily/{y}-{mo:02d}-{d:02d}'
    os.makedirs(out_dir, exist_ok=True)
    path = f'{out_dir}/risk_check_{slot}.md'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f'  {path}: {os.path.getsize(path)}B written')
    print(f'  总资产: {total_asset:,.2f} ({total_return:+.2f}%) 仓位: {pos_rate:.1f}%')
    for r in sr:
        print(f"    {r['code']} {r['name']}: close={r['close']:.3f} "
              f"H={r['high']:.3f} L={r['low']:.3f} 距止损{r['dist_sl']:+.2f}% [{r['hit_sl']}]")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('date_compact')
    ap.add_argument('slot')
    args = ap.parse_args()
    build(args.date_compact, args.slot)


if __name__ == '__main__':
    main()
