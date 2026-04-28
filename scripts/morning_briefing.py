#!/usr/bin/env python3
"""
晨报生成脚本 - 每日9:00前推送
覆盖：持仓状态、自选池机会、仓位建议、操作计划
"""
import urllib.request
import json
import time

# ============ 配置 ============
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/CLI_A93559E471B8DBD2"

# 持仓（每日更新）
POSITIONS = {
    '000783': {'cost': 7.34, 'qty': 600, 'stop': 6.97, 'name': '长江证券'},
    '002230': {'cost': 48.00, 'qty': 100, 'stop': 45.60, 'name': '科大讯飞'},
    '002415': {'cost': 33.00, 'qty': 100, 'stop': 31.50, 'name': '海康威视'},
    '002475': {'cost': 58.95, 'qty': 200, 'stop': 56.00, 'name': '立讯精密'},
    '000601': {'cost': 6.615, 'qty': 200, 'stop': 6.00, 'name': '韶能股份'},
}

# 自选池阈值
WATCHLIST = {
    '601899': {'buy': (35.50, 36.00), 'stop': 33.50, 'name': '紫金矿业'},
    '688813': {'buy': (68.00, 70.00), 'stop': 65.00, 'name': '泰金新能'},
    '002165': {'buy': 9.50, 'stop': 9.00, 'name': '红宝丽'},
    '000505': {'buy': 6.50, 'stop': 6.10, 'name': '京粮控股'},
}

WATCH_CODES = list(POSITIONS.keys()) + list(WATCHLIST.keys())

# ============ 数据获取 ============

def fetch_tencent():
    codes = []
    for code in WATCH_CODES:
        prefix = 'sz' if code.startswith(('0','3')) else 'sh'
        codes.append(f'{prefix}{code}')
    
    url = f'https://qt.gtimg.cn/q={",".join(codes)}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read().decode('gbk', errors='replace')
    
    results = {}
    for line in data.strip().split('\n'):
        line = line.strip()
        if not line or '=' not in line:
            continue
        key, rest = line.split('=', 1)
        if '"' not in rest:
            continue
        fields = rest.strip('"').split('~')
        if len(fields) < 35:
            continue
        
        sym = key.replace('v_', '')
        code = sym[2:]
        
        results[code] = {
            'price': float(fields[3]),
            'pre_close': float(fields[4]),
            'change_pct': float(fields[32]),
        }
    return results

# ============ 晨报生成 ============

def send_feishu(msg):
    payload = {
        "msg_type": "text",
        "content": {"text": msg}
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        FEISHU_BOT_URL,
        data=data,
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))

def generate_briefing(results):
    lines = []
    lines.append(f"📋 晨报 {time.strftime('%Y-%m-%d %H:%M')} · A股突击计划\n")
    
    # === 持仓状态 ===
    total_cost = 0
    total_value = 0
    total_pnl = 0
    
    lines.append("【持仓状态】")
    pos_list = []
    for code, p in POSITIONS.items():
        if code not in results:
            continue
        price = results[code]['price']
        pnl = (price - p['cost']) * p['qty']
        pnl_pct = (price - p['cost']) / p['cost'] * 100
        cost = p['cost'] * p['qty']
        value = price * p['qty']
        total_cost += cost
        total_value += value
        total_pnl += pnl
        
        emoji = '🟢' if pnl >= 0 else '🔴'
        lines.append(f"{emoji} {p['name']}({code}) 现价¥{price:.2f} 成本¥{p['cost']:.2f} 浮盈亏{'+' if pnl >= 0 else ''}{pnl:.0f}({pnl_pct:+.1f}%)")
    
    lines.append(f"\n持仓总计：成本¥{total_cost:.0f} 现值¥{total_value:.0f} 浮盈亏{'+' if total_pnl >= 0 else ''}{total_pnl:.0f}({total_pnl/total_cost*100:+.1f}%)")
    
    # === 止损风险检查 ===
    lines.append("\n【止损风险检查】")
    risk_count = 0
    for code, p in POSITIONS.items():
        if code not in results:
            continue
        price = results[code]['price']
        dist_to_stop = (price - p['stop']) / price * 100
        if dist_to_stop < 3:
            lines.append(f"🚨 {p['name']}({code}) 距止损仅 {dist_to_stop:.1f}%，现价¥{price} 止损¥{p['stop']}")
            risk_count += 1
    if risk_count == 0:
        lines.append("✅ 所有持仓距止损安全")
    
    # === 自选池机会 ===
    lines.append("\n【今日机会】")
    opp_count = 0
    for code, w in WATCHLIST.items():
        if code not in results:
            continue
        price = results[code]['price']
        buy = w['buy']
        
        if isinstance(buy, tuple):
            in_range = buy[0] <= price <= buy[1]
            range_str = f"¥{buy[0]}-{buy[1]}"
        else:
            in_range = price <= buy
            range_str = f"≤¥{buy}"
        
        if in_range:
            pct = (buy[0] if isinstance(buy, tuple) else buy) - price
            pct_pct = pct / price * 100
            lines.append(f"🟢 可买！{w['name']}({code}) 现价¥{price} 在买入区间{range_str}")
            opp_count += 1
        else:
            lines.append(f"🟡 等待 {w['name']}({code}) 现价¥{price} 目标区间{range_str}")
    
    if opp_count == 0:
        lines.append("今日无标的进入买入区间")
    
    # === 竞价预判 ===
    lines.append("\n【竞价预判】")
    # 找出竞价阶段可能的机会
    auction_stocks = []
    for code, w in WATCHLIST.items():
        if code not in results:
            continue
        price = results[code]['price']
        buy = w['buy']
        if isinstance(buy, tuple):
            in_range = buy[0] <= price <= buy[1]
        else:
            in_range = price <= buy
        if in_range:
            auction_stocks.append(f"{w['name']}({code})")
    
    if auction_stocks:
        lines.append(f"🎯 竞价阶段重点关注：{', '.join(auction_stocks)}进入买入区间")
        lines.append("09:15竞价开始，09:20分析开盘价，09:25决策，09:30执行")
    else:
        lines.append("今日竞价阶段暂无明确机会，关注持仓股开盘表现")

    # === 操作建议 ===
    lines.append("\n【今日操作计划】")
    if opp_count > 0:
        lines.append(f"🎯 今日{opp_count}只标的进入买入区间，可考虑建仓")
    if risk_count > 0:
        lines.append(f"⚠️ 关注{risk_count}只持仓止损风险，随时准备止损")
    if opp_count == 0 and risk_count == 0:
        lines.append("📊 持仓稳定，无紧急操作，观察为主")
    
    # === 资金状况 ===
    lines.append(f"\n【资金状况】可用约¥{(50000-total_cost):.0f} / ¥50,000")
    
    return '\n'.join(lines)

def main():
    try:
        results = fetch_tencent()
    except Exception as e:
        msg = f"❌ 晨报获取数据失败: {e}"
        print(msg)
        try:
            send_feishu(msg)
        except:
            pass
        return
    
    briefing = generate_briefing(results)
    print(briefing)
    
    try:
        send_feishu(briefing)
        print("\n✅ 晨报已推送至飞书")
    except Exception as e:
        print(f"\n❌ 飞书推送失败: {e}")

if __name__ == '__main__':
    main()
