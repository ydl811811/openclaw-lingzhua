#!/usr/bin/env python3
"""
每小时主动检查脚本 - v2.0（趋势调整版）
根据MA趋势动态调整止损/止盈阈值
"""
import urllib.request
import json
from datetime import datetime
import akshare as ak

# ============ 配置 ============
FEISHU_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/CLI_A93559E471B8DBD2"

POSITIONS = {
    '000783': {'name': '长江证券', 'cost': 7.338, 'qty': 600, 'base_stop': 0.95, 'base_take': 1.05},
    '002475': {'name': '立讯精密', 'cost': 58.95, 'qty': 200, 'base_stop': 0.95, 'base_take': 1.05},
    '000601': {'name': '韶能股份', 'cost': 6.615, 'qty': 200, 'base_stop': 0.95, 'base_take': 1.05},
    '002230': {'name': '科大讯飞', 'cost': 48.00, 'qty': 100, 'base_stop': 0.95, 'base_take': 1.05},
    '600012': {'name': '皖通高速', 'cost': 15.855, 'qty': 200, 'base_stop': 0.95, 'base_take': 1.05},
}

# 趋势调整参数
TREND_ADJUST = {
    '上升':   {'stop_mult': 1.0,  'take_mult': 1.2},   # 上升趋势：止损不变，止盈放宽
    '震荡偏强': {'stop_mult': 0.95, 'take_mult': 1.1},  # 震荡偏强：止损收紧
    '震荡偏弱': {'stop_mult': 0.9,  'take_mult': 0.95}, # 震荡偏弱：止损止盈都收紧
    '下降':   {'stop_mult': 0.85, 'take_mult': 0.9},   # 下降趋势：止损大幅收紧
}

INDICES = {
    '000001': {'name': '上证指数', 'key': 4050, 'support': 4000},
    '399001': {'name': '深证成指'},
    '399006': {'name': '创业板指'},
}

INDEX_PREFIX = {'000001': 'sh', '399001': 'sz', '399006': 'sz'}

# ============ 函数 ============

def get_trend(code):
    """获取股票趋势（MA5/MA10/MA20）"""
    try:
        symbol = f"sh{code}" if code.startswith("6") else f"sz{code}"
        df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq")
        
        if df is None or df.empty or len(df) < 20:
            return "震荡", 50
        
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma10'] = df['close'].rolling(window=10).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()
        
        latest = df.iloc[-1]
        price = latest['close']
        ma5, ma10, ma20 = latest['ma5'], latest['ma10'], latest['ma20']
        
        # 判断趋势
        if ma5 > ma10 > ma20 and price > ma5:
            return "上升", 80
        elif ma5 > ma10 and price > ma5:
            return "上升", 70
        elif ma5 < ma10 < ma20 and price < ma5:
            return "下降", 30
        elif ma5 < ma10 and price < ma5:
            return "下降", 40
        elif price > ma10:
            return "震荡偏强", 55
        else:
            return "震荡偏弱", 45
            
    except:
        return "震荡", 50

def get_realtime(codes):
    """获取实时数据"""
    prefixed = []
    for code in codes:
        if code in INDEX_PREFIX:
            prefixed.append(f"{INDEX_PREFIX[code]}{code}")
        elif code.startswith(('0','3')):
            prefixed.append(f"sz{code}")
        else:
            prefixed.append(f"sh{code}")
    
    url = f'https://qt.gtimg.cn/q={",".join(prefixed)}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read().decode('gbk', errors='replace')
    
    results = {}
    for line in data.strip().split('\n'):
        if '=' not in line:
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
            'pre_close': float(fields[4]) if code in INDEX_PREFIX else float(fields[5]),
            'change_pct': float(fields[32]) if fields[32] else 0,
        }
    return results

def get_stop_take(pos, trend):
    """根据趋势计算调整后的止损止盈价"""
    stop_mult = TREND_ADJUST[trend]['stop_mult']
    take_mult = TREND_ADJUST[trend]['take_mult']
    
    stop_price = pos['cost'] * pos['base_stop'] * stop_mult
    take_price = pos['cost'] * pos['base_take'] * take_mult
    
    return stop_price, take_price

def analyze():
    """分析检查"""
    all_codes = list(POSITIONS.keys()) + list(INDICES.keys())
    results = get_realtime(all_codes)
    
    alerts = []
    now = datetime.now().strftime("%H:%M")
    
    # 大盘信息
    index_info = []
    for code, info in INDICES.items():
        if code in results:
            d = results[code]
            pct = d['change_pct']
            note = ""
            if code == '000001':
                if d['price'] >= 4050:
                    note = " ⚠️接近压力位4050"
                elif d['price'] <= 4000:
                    note = " ⚠️接近支撑位4000"
            index_info.append(f"{info['name']}: {d['price']} ({pct:+.2f}%){note}")
    
    # 持仓分析
    position_info = []
    trend_cache = {}  # 缓存趋势，避免重复获取
    
    for code, pos in POSITIONS.items():
        if code not in results:
            continue
        
        d = results[code]
        price = d['price']
        pct = d['change_pct']
        profit = (price - pos['cost']) * pos['qty']
        profit_pct = (price - pos['cost']) / pos['cost'] * 100
        
        # 获取趋势（缓存）
        if code not in trend_cache:
            trend, trend_score = get_trend(code)
            trend_cache[code] = (trend, trend_score)
        else:
            trend, trend_score = trend_cache[code]
        
        # 计算调整后的止损止盈
        stop_price, take_price = get_stop_take(pos, trend)
        
        # 检查预警
        alert_type = None
        if price <= stop_price:
            alert_type = f"🚨止损！{pos['name']} 现价{price} <= 止损{stop_price:.2f}"
        elif price >= take_price:
            alert_type = f"🎯止盈！{pos['name']} 现价{price} >= 目标{take_price:.2f}"
        elif profit_pct <= -3:
            alert_type = f"⚠️浮亏超3%！{pos['name']} {profit_pct:.1f}%"
        
        if alert_type:
            alerts.append(alert_type)
        
        # 持仓状态（带趋势标注）
        status = "🟢" if profit >= 0 else "🔴"
        position_info.append(f"{status} {pos['name']}({trend})")
        position_info.append(f"   现价:{price} ({pct:+.2f}%) 浮盈{profit:+,.0f}元")
        position_info.append(f"   成本:{pos['cost']} 止损:{stop_price:.2f} 目标:{take_price:.2f}")
    
    return now, index_info, position_info, alerts

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始检查（趋势调整版）...")
    
    now, indices, positions, alerts = analyze()
    
    # 组合消息
    lines = [f"📊 市场检查 {now}"]
    lines.append("")
    lines.append("【大盘】")
    lines.extend(indices)
    lines.append("")
    lines.append("【持仓】")
    lines.extend(positions)
    
    if alerts:
        lines.append("")
        lines.append("【⚠️ 预警】")
        lines.extend(alerts)
    
    msg = '\n'.join(lines)
    print(msg)
    
    if alerts:
        # 发送飞书（webhook已失效，打印通知）
        print(f"\n【需要通知老大】")
        return 1
    else:
        print(f"\n[{now}] 无预警，持仓正常")
        return 0

if __name__ == '__main__':
    exit(main())
