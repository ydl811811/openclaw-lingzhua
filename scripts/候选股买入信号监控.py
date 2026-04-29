#!/usr/bin/env python3
"""
自选股买入提醒脚本
- 监控未建仓股票，进入买入区间时提醒
- 30秒间隔检查
- 只发买入提醒，不发止盈提醒（因为还没买）
"""
import os
import urllib.request
import json
import time
from datetime import datetime

PID_FILE = "/tmp/watchlist_monitor.pid"

# ============ 配置 ============
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"

# 自选股池（未建仓，等待买入提醒）
# === 04-24 候选池（老大精选）+ 04-27 新增 ===
WATCHLIST = {
    '002475': {'name': '立讯精密', 'buy_low': 61.64, 'buy_high': 65.73, 'stop': 62.40, 'note': '消费电子苹果链，等回调'},
    '301510': {'name': '固高科技', 'buy_low': 34.00, 'buy_high': 35.00, 'stop': 32.50, 'note': '机器人运动控制，短线低吸10%，目标40'},
    '002810': {'name': '山东赫达', 'buy_low': 26.00, 'buy_high': 27.00, 'stop': 25.00, 'note': '植物胶囊龙头，等26-27区间，目标30（已从高点回调）'},
    '300613': {'name': '富瀚微', 'buy_low': 58.00, 'buy_high': 60.00, 'stop': 54.00, 'note': 'AI芯片主线，等回调到58-60区间，目标72（主力成本42元，当前62偏高）'},
    # 603876 已于2026-04-27建仓持有，移出候选池
    # 株冶集团(600961) 高管闪辞+筹码混乱，移出（2026-04-27）
    # 美新科技(301588) 纯资金炒作，估值偏高，移出（2026-04-27）
    # 金螳螂(002081) 高危！纯题材炒作崩盘，移出（2026-04-27）
}

# ============ 函数 ============

def get_realtime(codes):
    """获取实时数据（新浪接口）"""
    import requests
    import re
    prefixed = []
    for code in codes:
        if code.startswith(('5', '6', '7', '9')):
            prefixed.append(f"sh{code}")
        else:
            prefixed.append(f"sz{code}")
    
    url = f'http://hq.sinajs.cn/list={",".join(prefixed)}'
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://finance.sina.com.cn/'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = 'gbk'
    except:
        return {}
    
    results = {}
    for line in resp.text.split('\n'):
        m = re.search(r'hq_str_(\w+)="(.+?)"', line)
        if not m:
            continue
        code_raw = m.group(1).replace('sh', '').replace('sz', '')
        content = m.group(2)
        parts = content.split(',')
        if len(parts) < 10:
            continue
        try:
            price = float(parts[3]) if parts[3] != '0' else 0
            prev_close = float(parts[2]) if parts[2] else 0
            if prev_close > 0:
                pct = (price - prev_close) / prev_close * 100
            else:
                pct = 0
            results[code_raw] = {'price': price, 'pct': pct}
        except:
            pass
    return results

def send_alert(msg):
    """发送飞书提醒"""
    import hashlib
    import hmac
    import base64
    try:
        timestamp = str(int(time.time()))
        string_to_sign = timestamp + '\n' + FEISHU_SECRET
        sign = base64.b64encode(hmac.new(string_to_sign.encode(), digestmod=hashlib.sha256).digest()).decode()
        
        payload = {
            "msg_type": "text",
            "content": {"text": msg},
            "timestamp": timestamp,
            "sign": sign
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            FEISHU_BOT_URL,
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"发送失败: {e}")

def is_trading_hours():
    """检查是否在交易时间内"""
    from datetime import time as dt_time
    now = datetime.now()
    
    if now.weekday() >= 5:
        return False
    
    current_time = now.time()
    morning_start = dt_time(9, 15)
    morning_end = dt_time(11, 30)
    afternoon_start = dt_time(13, 0)
    afternoon_end = dt_time(15, 0)
    
    in_morning = morning_start <= current_time <= morning_end
    in_afternoon = afternoon_start <= current_time <= afternoon_end
    
    return in_morning or in_afternoon

def check_watchlist():
    """检查自选股是否进入买入区间"""
    codes = list(WATCHLIST.keys())
    quotes = get_realtime(codes)
    
    alerts = []
    for code, info in WATCHLIST.items():
        if code in quotes:
            price = quotes[code]['price']
            pct = quotes[code]['pct']
            buy_low = info['buy_low']
            buy_high = info['buy_high']
            stop = info['stop']
            
            if buy_low <= price <= buy_high:
                alert = f"🎯买入提醒\n{info['name']}({code}) 现价{price:.2f}元\n已进入买入区间: {buy_low}-{buy_high}\n备注: {info['note']}"
                alerts.append(alert)
    
    return alerts

def main():
    print("="*60)
    print("📋 自选股买入提醒脚本启动")
    print("="*60)
    print(f"监控标的: {len(WATCHLIST)} 只")
    for code, info in WATCHLIST.items():
        print(f"  {code} {info['name']} 买入区间:{info['buy_low']}-{info['buy_high']}")
    print("="*60)
    print()
    
    last_alert_time = {}
    
    while True:
        try:
            now = datetime.now()
            alerts = check_watchlist()
            
            for alert in alerts:
                code = alert.split('\n')[1].split('(')[1].split(')')[0]
                
                if not is_trading_hours():
                    continue
                
                if code in last_alert_time:
                    if (now - last_alert_time[code]).seconds < 300:
                        continue
                
                print(f"\n{'='*50}")
                print(alert)
                print(f"{'='*50}")
                send_alert(alert)
                last_alert_time[code] = now
            
            if not alerts:
                if now.second < 5:
                    quotes = get_realtime(list(WATCHLIST.keys()))
                    print(f"[{now.strftime('%H:%M:%S')}] 监控中...", end=" ")
                    for code, q in quotes.items():
                        name = WATCHLIST[code]['name']
                        price = q['price']
                        pct = q['pct']
                        in_zone = "✅买入区" if WATCHLIST[code]['buy_low'] <= price <= WATCHLIST[code]['buy_high'] else "⏳等待"
                        print(f"{name}:{price:.2f}({pct:+.2f}%){in_zone}", end=" ")
                    print()
        
        except Exception as e:
            print(f"错误: {e}")
        
        time.sleep(30)

if __name__ == "__main__":
    if os.path.exists(PID_FILE):
        old_pid = open(PID_FILE).read().strip()
        try:
            os.kill(int(old_pid), 0)
            print(f"已有实例运行 (PID {old_pid})")
            exit(1)
        except:
            pass
    
    open(PID_FILE, 'w').write(str(os.getpid()))
    
    try:
        main()
    except KeyboardInterrupt:
        print("\n停止监控")
        os.unlink(PID_FILE)