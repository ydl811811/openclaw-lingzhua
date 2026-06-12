#!/usr/bin/env python3
"""
候选股重新评估脚本
每天收盘后自动重新评估候选股买入区间
2026-06-12 新增
"""
import os
import sys
import json
import time
import requests
import re
from datetime import datetime

FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEv…eUtI"

# 候选股列表（从监控脚本读取）
WATCHLIST_CODES = ['002475', '300552', '600552', '300613', '603876', '300623', '002407', '603379']

def get_realtime(codes):
    """获取实时价格"""
    prefixed = []
    for code in codes:
        if code.startswith(('5', '6', '7', '9')):
            prefixed.append(f'sh{code}')
        else:
            prefixed.append(f'sz{code}')
    
    url = f'http://hq.sinajs.cn/list={chr(44).join(prefixed)}'
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://finance.sina.com.cn/'}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.encoding = 'gbk'
    
    results = {}
    for line in resp.text.split('\n'):
        m = re.search(r'hq_str_(\w+)="(.+?)"', line)
        if not m:
            continue
        code = m.group(1).replace('sh', '').replace('sz', '')
        parts = m.group(2).split(',')
        if len(parts) >= 10 and parts[3] != '0':
            results[code] = {
                'name': parts[0],
                'price': float(parts[3]),
                'prev_close': float(parts[2]),
                'pct': (float(parts[3]) - float(parts[2])) / float(parts[2]) * 100
            }
    return results

def get_kline_ma(code, datalen=30):
    """获取K线数据计算均线"""
    symbol = f'sz{code}' if not code.startswith(('5', '6', '7', '9')) else f'sh{code}'
    url = f'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=5&datalen={datalen}'
    try:
        resp = requests.get(url, timeout=10)
        data = json.loads(resp.text)
        if data and len(data) >= 20:
            closes = [float(d['close']) for d in data]
            return {
                'ma5': sum(closes[-5:])/5,
                'ma10': sum(closes[-10:])/10,
                'ma20': sum(closes[-20:])/20,
                'high30': max(closes[-20:]) if len(closes) >= 20 else max(closes),
                'low30': min(closes[-20:]) if len(closes) >= 20 else min(closes),
            }
    except:
        pass
    return None

def calculate_buy_zone(price, ma5, ma20, low30, high30):
    """计算新的买入区间"""
    # 基于均线和波动区间计算
    # 买入区间下沿：回踩MA5或MA20的低位
    # 买入区间上沿：30日高点下方10%
    
    # 优先用MA20作为支撑参考
    if price > ma20:
        buy_low = max(ma5 *0.98, ma20 * 0.98)
    else:
        buy_low = max(ma5 * 0.95, low30 * 0.98)
    
    buy_high = min(ma20 * 1.03, high30 * 0.95)
    
    # 计算止损（跌破买入区间下沿-5%）
    stop = buy_low * 0.95
    
    # 计算目标（30日高点或买入区间上方15%）
    target_low = high30 * 0.9
    target_high = high30 * 0.95
    
    return {
        'buy_low': round(buy_low, 2),
        'buy_high': round(buy_high, 2),
        'stop': round(stop, 2),
        'target_low': round(target_low, 2),
        'target_high': round(target_high, 2),
    }

def send_feishu(msg):
    """发送飞书消息"""
    import hashlib
    import hmac
    import base64
    import urllib.request
    
    now = time.time()
    timestamp = str(int(now))
    string_to_sign = timestamp + '\n' + FEISHU_SECRET
    sign = base64.b64encode(hmac.new(string_to_sign.encode(), digestmod=hashlib.sha256).digest()).decode()
    
    payload = {
        "msg_type": "text",
        "content": {"text": msg},
        "timestamp": timestamp,
        "sign": sign
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(FEISHU_BOT_URL, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"发送失败: {e}")

def main():
    print("="*60)
    print(f"📊 候选股重新评估 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*60)
    
    # 获取实时价格
    prices = get_realtime(WATCHLIST_CODES)
    print(f"\n获取到 {len(prices)} 只股票价格")
    
    results = []
    for code in WATCHLIST_CODES:
        if code not in prices:
            print(f"⚠️ {code} 获取价格失败")
            continue
        
        info = prices[code]
        name = info['name']
        price = info['price']
        pct = info['pct']
        
        # 获取均线数据
        ma_data = get_kline_ma(code)
        if not ma_data:
            print(f"⚠️ {name} 获取均线失败")
            continue
        
        ma5 = ma_data['ma5']
        ma20 = ma_data['ma20']
        high30 = ma_data['high30']
        low30 = ma_data['low30']
        
        # 计算新的买入区间
        zone = calculate_buy_zone(price, ma5, ma20, low30, high30)
        distance_ma20 = (price - ma20) / ma20 * 100
        
        result = {
            'code': code,
            'name': name,
            'price': price,
            'pct': pct,
            'ma5': ma5,
            'ma20': ma20,
            'high30': high30,
            'low30': low30,
            'distance_ma20': distance_ma20,
            **zone
        }
        results.append(result)
        
        print(f"\n{name}({code}): {price:.2f}元 ({pct:+.2f}%)")
        print(f"  均线: MA5={ma5:.2f} MA20={ma20:.2f}")
        print(f"  30日区间: {low30:.2f} ~ {high30:.2f}")
        print(f"  距MA20: {distance_ma20:+.1f}%")
        print(f"  新买入区间: {zone['buy_low']} - {zone['buy_high']}")
        print(f"  止损: {zone['stop']} 目标: {zone['target_low']}-{zone['target_high']}")
    
    # 生成报告
    if results:
        report = f"📊 **候选股重新评估报告** ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"
        report += "| 代码 | 名称 | 现价 | 距MA20 | 买入区间 | 止损 | 目标 |\n"
        report += "|------|------|------|--------|---------|------|------|\n"
        
        for r in results:
            report += f"| {r['code']} | {r['name']} | {r['price']:.2f} | {r['distance_ma20']:+.1f}% | {r['buy_low']}-{r['buy_high']} | {r['stop']} | {r['target_low']}-{r['target_high']} |\n"
        
        report += "\n🎀 数据基于MA20均线+30日波动区间计算"
        
        print("\n" + "="*60)
        print("报告内容:")
        print(report)
        print("="*60)
        
        # 发送到飞书
        send_feishu(report)
        print("\n✅ 报告已发送到飞书")
        
        # 保存结果到文件
        save_path = f"/home/YDL/.openclaw/workspace/a_stock_plan/daily/{datetime.now().strftime('%Y-%m-%d')}/候选股评估_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'w') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存到: {save_path}")
    
    return results

if __name__ == "__main__":
    main()