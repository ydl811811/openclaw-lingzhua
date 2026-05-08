#!/usr/bin/env python3
"""
持仓股监控脚本
- 只监控已持仓股票（止损提醒）
- 30秒间隔检查
- 只发止损预警，不频繁提醒
"""
import os
import urllib.request
import json
import time
from datetime import datetime
import subprocess

PID_FILE = "/tmp/holdings_monitor.pid"

# ============ 已持仓监控（止损+潜在加仓区间）============
HOLDINGS = {
    '600900': {'name': '长江电力', 'cost': 26.895, 'stop': 25.00, 'target': 30.00, 'note': '🟢高股息长线持有，止损25，目标30'},
    '603876': {'name': '鼎胜新材', 'cost': 29.163, 'stop': 29.50, 'target': 35.00, 'note': '⚠️持仓200股@29.163，止损29.5，目标35。33是强压力，周一关注是否跌破30.5'},
    '300613': {'name': '富瀚微', 'cost': 58.22, 'stop': 54.00, 'target1': 65.00, 'target2': 72.00, 'note': '🟢AI芯片主线。策略：65突破持有到72，不突破65则做T。止损54'},
    '600845': {'name': '宝信软件', 'cost': 23.861, 'stop': 22.00, 'target': 30.00, 'note': '⭐AIDC翻倍股，持仓100股@23.861，止损22，目标30-35。加仓建议：等回调24.0-24.2或等站稳25'},
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
        FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
        FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"
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

def check_holdings():
    """检查持仓股是否触发止损或加仓信号"""
    codes = list(HOLDINGS.keys())
    quotes = get_realtime(codes)
    
    alerts = []
    for code, info in HOLDINGS.items():
        if code not in quotes:
            continue
        
        price = quotes[code]['price']
        pct = quotes[code]['pct']
        cost = info['cost']
        stop = info['stop']
        target = info.get('target', info.get('target1', 0))
        name = info['name']
        
        # 计算盈亏
        profit_pct = (price - cost) / cost * 100 if cost > 0 else 0
        profit_val = (price - cost) * 100 if cost > 0 else 0
        
        # 检查是否触及止损
        if price > 0 and price <= stop:
            alert = f"🔴【止损预警】\n{name}({code}) 现价{price:.2f}元\n已触及止损价{stop:.2f}！\n成本{info['cost']:.3f}，当前盈亏{profit_pct:+.1f}%\n备注: {info['note']}"
            alerts.append(('stop', alert))
        
        # 检查是否达到第1目标（针对富瀚微）
        elif code == '300613' and price >= 65.00:
            target1 = info.get('target1', 65.00)
            alert = f"🎯【第1目标到达】\n{name}({code}) 现价{price:.2f}元\n已达到第1目标价{target1:.2f}！\n操作建议：突破65则持有等72，不突破则做T\n备注: {info['note']}"
            alerts.append(('target', alert))
    
    return alerts

def main():
    print("="*60)
    print("📋 持仓股监控脚本启动（仅监控持仓）")
    print("="*60)
    print(f"监控标的: {len(HOLDINGS)} 只")
    for code, info in HOLDINGS.items():
        print(f"  {code} {info['name']} 止损:{info['stop']} 目标:{info.get('target', info.get('target2', 'N/A'))}")
    print("="*60)
    print()
    
    last_alert_time = {}
    
    while True:
        try:
            now = datetime.now()
            alerts = check_holdings()
            
            for alert_type, alert in alerts:
                if not is_trading_hours():
                    continue
                
                # 止损预警不限制时间，目标预警5分钟内只发一次
                code = alert.split('\n')[1].split('(')[1].split(')')[0]
                
                if alert_type == 'stop':
                    # 止损预警立即发
                    print(f"\n{'='*50}")
                    print(alert)
                    print(f"{'='*50}")
                    send_alert(alert)
                    last_alert_time[code] = now
                elif alert_type == 'target':
                    # 目标预警5分钟限制
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
                    quotes = get_realtime(list(HOLDINGS.keys()))
                    print(f"[{now.strftime('%H:%M:%S')}] 持仓监控中...", end=" ")
                    for code, q in quotes.items():
                        name = HOLDINGS[code]['name']
                        price = q['price']
                        pct = q['pct']
                        cost = HOLDINGS[code]['cost']
                        profit = (price - cost) / cost * 100 if cost > 0 else 0
                        stop = HOLDINGS[code]['stop']
                        stop_status = "🔴触及止损！" if price <= stop else "✅安全"
                        print(f"{name}:{price:.2f}({pct:+.2f}%)({profit:+.1f}%){stop_status}", end=" ")
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
