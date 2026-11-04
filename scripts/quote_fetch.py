#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
腾讯/新浪行情统一抓取器（2026-11-03 修正字段索引）

【背景 / BUG 修复】
腾讯 qt.gtimg.cn 返回的 ~ 分隔字段中：
  parts[3]  = 现价
  parts[4]  = 昨收
  parts[5]  = 今开
  parts[30] = 时间戳
  parts[31] = 涨跌额
  parts[32] = 涨跌幅(%)   ← 旧代码误当作"最高价"
  parts[33] = 最高价
  parts[34] = 最低价       ← 旧代码把 parts[33] 当作"最低价"
  parts[36] = 成交量(手)
  parts[37] = 成交额(万元)

旧解析器用 parts[32] 当 high、parts[33] 当 low，导致：
  1) 报告表格"当日最高/最低"两列数值全错
  2) 止损触及判定 (low <= stop_loss) 实际拿最高价比较，可能漏报/误报触发
  3) 止盈触及判定 (high >= take_profit) 同理失真

本模块以新浪源为交叉验证基准（新浪字段顺序固定可靠），并做一致性断言。
"""

import urllib.request
import re

TENCENT_URL = 'https://qt.gtimg.cn/q='
SINA_URL = 'https://hq.sinajs.cn/list='

DEFAULT_CODES = [
    'sh000001', 'sz399001', 'sh000688', 'sz399006',   # 四大指数
    'sh512690', 'sz002407', 'sz300623',                # 持仓
    'sz002475', 'sh603379', 'sz002407',                # 条件单 + 已清仓
]

HEADERS_TENCENT = {'Referer': 'https://gu.qq.com/', 'User-Agent': 'Mozilla/5.0'}
HEADERS_SINA = {'Referer': 'https://finance.sina.com.cn/', 'User-Agent': 'Mozilla/5.0'}


def _get(url, headers):
    resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8)
    return resp.read().decode('gbk', errors='replace')


def fetch_tencent(codes=None):
    """腾讯源。字段映射已修正（见模块 docstring）。"""
    codes = codes or DEFAULT_CODES
    text = _get(TENCENT_URL + ','.join(codes), HEADERS_TENCENT)
    out = {}
    for line in text.strip().split('\n'):
        m = re.search(r'v_(\w+)="(.+?)";', line)
        if not m:
            continue
        code = m.group(1)
        p = m.group(2).split('~')
        if len(p) < 38:
            continue
        try:
            close = float(p[3]); prev = float(p[4]); open_ = float(p[5])
            high = float(p[33]); low = float(p[34])     # ✅ 修正
            pct = (close - prev) / prev * 100
            out[code] = {
                'close': close, 'prev': prev, 'open': open_,
                'high': high, 'low': low, 'pct': pct,
                'ts': p[30],
                'vol': int(float(p[36])) if p[36] else 0,
                'amt': int(float(p[37])) if p[37] else 0,
                'source': 'tencent',
            }
        except (ValueError, IndexError):
            continue
    return out


def fetch_sina(codes=None):
    """新浪源，作为交叉验证基准。"""
    codes = codes or DEFAULT_CODES
    text = _get(SINA_URL + ','.join(codes), HEADERS_SINA)
    out = {}
    for line in text.strip().split('\n'):
        m = re.search(r'var hq_str_(\w+)="(.+?)";', line)
        if not m:
            continue
        code = m.group(1)
        p = m.group(2).split(',')
        if len(p) < 32:
            continue
        try:
            open_ = float(p[1]); prev = float(p[2]); close = float(p[3])
            high = float(p[4]); low = float(p[5])
            pct = (close - prev) / prev * 100
            out[code] = {
                'close': close, 'prev': prev, 'open': open_,
                'high': high, 'low': low, 'pct': pct,
                'ts': p[31] if len(p) > 31 else '',
                'source': 'sina',
            }
        except (ValueError, IndexError):
            continue
    return out


def fetch_dual(codes=None, verbose=False):
    """双源抓取 + 交叉验证。返回 (merged, verify_report)。"""
    tq = fetch_tencent(codes)
    sn = fetch_sina(codes)

    merged = dict(tq)
    verify = {'ok': [], 'mismatch': [], 'only_tencent': [], 'only_sina': [],
              'no_sina_data': []}

    for code in sorted(set(tq) | set(sn)):
        if code not in tq:
            verify['only_sina'].append(code); continue
        if code not in sn:
            verify['only_tencent'].append(code); continue
        t, s = tq[code], sn[code]
        # 新浪在开盘前/无成交时段可能只给现价，high/low 返回 0 → 逐字段校验，只比对有值的字段
        fields = [('close', t['close'], s['close']),
                  ('high', t['high'], s.get('high') or 0),
                  ('low', t['low'], s.get('low') or 0)]
        checked = [(n, tv, sv) for n, tv, sv in fields if sv > 0]
        if not checked:
            verify['no_sina_data'].append(code)
            merged[code]['sina_close'] = None
            merged[code]['dual_verified'] = None
            continue
        # 价格一致：两源是两次独立 HTTP 调用，落在不同时刻，相邻采样间现价会跳动。
        # 容差按相对价格 0.05% 取（另加 0.05 绝对下限），覆盖毫厘级刷新时差。
        tol = max(0.05, abs(t['close']) * 0.0005)
        same = all(abs(tv - sv) <= tol for _, tv, sv in checked)
        if same:
            verify['ok'].append(code)
        else:
            verify['mismatch'].append({
                'code': code,
                'tencent': f"close={t['close']:.3f} H={t['high']:.3f} L={t['low']:.3f}",
                'sina': f"close={s['close']:.3f} H={s['high']:.3f} L={s['low']:.3f}",
            })
        merged[code]['sina_close'] = s['close']
        merged[code]['sina_high'] = s.get('high')
        merged[code]['sina_low'] = s.get('low')
        merged[code]['dual_verified'] = same
        merged[code]['dual_fields_checked'] = [n for n, _, _ in checked]

    if verbose:
        nosina = len(verify.get('no_sina_data', []))
        print(f"  双源一致: {len(verify['ok'])} | 不一致: {len(verify['mismatch'])}"
              + (f" | 新浪无数据(盘前/停牌): {nosina}" if nosina else ""))
        for m in verify['mismatch']:
            print(f"    ⚠️ {m['code']}: 腾讯[{m['tencent']}] vs 新浪[{m['sina']}]")

    return merged, verify


if __name__ == '__main__':
    import json
    merged, verify = fetch_dual(verbose=True)
    for code in ['sh000001', 'sh512690', 'sz300623']:
        d = merged.get(code)
        if not d:
            continue
        print(f"  {code}: open={d['open']:.3f} high={d['high']:.3f} low={d['low']:.3f} "
              f"close={d['close']:.3f} ({d['pct']:+.2f}%) ts={d['ts']}")
