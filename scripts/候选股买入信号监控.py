#!/usr/bin/env python3
"""
自选股买入提醒脚本
- 监控未建仓股票，进入买入区间时提醒
- 30秒间隔检查
- 只发买入提醒，不发止盈提醒（因为还没买）
- 增加缩量条件：价格到位 + 缩量（今日量<昨日量）才提醒
"""
import os
import urllib.request
import json
import time
from datetime import datetime
import subprocess

PID_FILE = "/tmp/watchlist_monitor.pid"
VOLUME_FILE = "/tmp/watchlist_volume.json"

# ============ 成交量数据管理 ============
def get_volume_from_adata(codes):
    """通过adata API获取今日成交量数据"""
    try:
        cmd = f'/usr/bin/python3.12 /home/yu/.hermes/skills/adata-stock-data/scripts/fetch_data.py realtime {" ".join(codes)}'
        result = subprocess.run(
            ['ssh', '-i', '/home/YDL/.ssh/id_ed25519', '-o', 'StrictHostKeyChecking=no',
             'yu@192.168.31.141', cmd],
            capture_output=True, text=True, timeout=30
        )
        volumes = {}
        for line in result.stdout.strip().split('\n'):
            if line.startswith('stock_code') or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 6:
                code = parts[0]
                try:
                    volume = int(parts[4])  # volume is at index 4
                    volumes[code] = volume
                except:
                    pass
        return volumes
    except Exception as e:
        print(f"获取成交量失败: {e}")
        return {}

def get_5day_avg_volume(codes):
    """通过adata API获取前5个交易日均量（用于缩量判断）"""
    avg_volumes = {}
    try:
        for code in codes:
            # 获取日K线数据（过去5-10天，取5个交易日）
            cmd = f'/usr/bin/python3.12 /home/yu/.hermes/skills/adata-stock-data/scripts/fetch_data.py kline {code} 10'
            proc = subprocess.run(
                ['ssh', '-i', '/home/YDL/.ssh/id_ed25519', '-o', 'StrictHostKeyChecking=no',
                 'yu@192.168.31.141', cmd],
                capture_output=True, text=True, timeout=30
            )
            volumes = []
            for line in proc.stdout.strip().split('\n'):
                if line.startswith('trade_date') or not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        vol = int(float(parts[5]))  # volume字段（可能是浮点数）
                        volumes.append(vol)
                    except:
                        pass
            # 取最后5个交易日的成交量（排除今天，因为今天的K线可能还没走完）
            if len(volumes) >= 5:
                past_5_days = volumes[:5]
                avg_vol = sum(past_5_days) / 5
                avg_volumes[code] = avg_vol
    except Exception as e:
        print(f"获取5日均量失败: {e}")
    return avg_volumes

def load_previous_volume():
    """加载昨日成交量数据（兼容旧版本）"""
    if os.path.exists(VOLUME_FILE):
        try:
            with open(VOLUME_FILE, 'r') as f:
                data = json.load(f)
                return data.get('volumes', {})
        except:
            return {}
    return {}

def save_current_volume(volumes):
    """保存今日成交量数据（供明日使用）"""
    try:
        with open(VOLUME_FILE, 'w') as f:
            json.dump({'volumes': volumes, 'date': datetime.now().strftime('%Y-%m-%d')}, f)
    except Exception as e:
        print(f"保存成交量失败: {e}")

def is_volume_contracted(code, today_volume, avg_volume):
    """检查是否缩量：今日成交量 < 5日均量 × 0.8"""
    if code not in avg_volume or avg_volume[code] == 0:
        # 没有均量数据，跳过缩量检查（假设不缩量）
        return True
    if today_volume == 0:
        return False
    # 缩量条件：今日量 < 5日均量 × 0.8（给予20%弹性空间）
    return today_volume < avg_volume[code] * 0.8



# ============ 配置 ============
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"

# 自选股池（未建仓，等待买入提醒）
# === 04-24 候选池（老大精选）+ 04-27 新增 ===
WATCHLIST = {
    '002240': {'name': '盛新锂能', 'buy_low': 50.00, 'buy_high': 53.00, 'stop': 47.00, 'note': '锂电上游，长线轨入选，新能源爆发，目标60-65，仓位20-30%'},
    '002475': {'name': '立讯精密', 'buy_low': 67.00, 'buy_high': 69.00, 'stop': 65.50, 'note': '消费电子苹果链，建议68元以下买，激进可以在67-69区间买，目标74-75'},
    '301510': {'name': '固高科技', 'buy_low': 37.50, 'buy_high': 38.50, 'stop': 36.50, 'note': '机器人运动控制，建议37.5-38区间买，目标42-43'},
    '002810': {'name': '山东赫达', 'buy_low': 25.50, 'buy_high': 26.00, 'stop': 24.50, 'note': '植物胶囊龙头，建议25.5-26区间分批买（深度分析：主力出货，等缩量企稳），目标30（已回调）'},
    '300552': {'name': '万集科技', 'buy_low': 26.00, 'buy_high': 27.00, 'stop': 24.50, 'note': 'AI基础设施/ETC，业绩增速606%，ROE4.82%优，等回调26-27区间，目标32-33'},
    '300400': {'name': '劲拓股份', 'buy_low': 25.00, 'buy_high': 26.00, 'stop': 23.50, 'note': '自动化设备/SMT，业绩增速439%，等回调25-26区间，目标30-31'},
    # 富瀚微(300613) 已于2026-04-30建仓持有，移出候选池
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
    """检查自选股是否进入买入区间 + 缩量条件"""
    codes = list(WATCHLIST.keys())
    quotes = get_realtime(codes)
    
    # 获取成交量数据
    volumes = get_volume_from_adata(codes)
    avg_volumes = get_5day_avg_volume(codes)  # 使用5日均量
    
    alerts = []
    for code, info in WATCHLIST.items():
        if code in quotes:
            price = quotes[code]['price']
            pct = quotes[code]['pct']
            buy_low = info['buy_low']
            buy_high = info['buy_high']
            stop = info['stop']
            today_vol = volumes.get(code, 0)
            
            # 检查价格是否在买入区间
            price_in_zone = buy_low <= price <= buy_high
            
            # 检查是否缩量（今日量 < 5日均量 × 0.8）
            has_vol_data = code in avg_volumes and avg_volumes[code] > 0
            vol_contracted = is_volume_contracted(code, today_vol, avg_volumes) if has_vol_data else False
            
            if price_in_zone and has_vol_data and vol_contracted:
                vol_info = f"\n成交量: {today_vol/10000:.1f}万 (5日均量{avg_volumes.get(code, 0)/10000:.1f}万)"
                alert = f"🎯买入提醒(缩量确认)\n{info['name']}({code}) 现价{price:.2f}元\n已进入买入区间: {buy_low}-{buy_high}\n{vol_info}\n备注: {info['note']}"
                alerts.append(alert)
            elif price_in_zone and not has_vol_data:
                # 没有均量数据，不发提醒
                print(f"[数据缺失] {info['name']} 价格到位但无法获取均量数据，暂不提醒")
            elif price_in_zone and has_vol_data and not vol_contracted:
                # 价格到位但未缩量，打印日志但不提醒
                vol_info = f"成交量: {today_vol/10000:.1f}万 (5日均量{avg_volumes.get(code, 0)/10000:.1f}万) - 未缩量"
                print(f"[条件未满足] {info['name']} 价格到位但未缩量 {vol_info}")
    
    # 保存今日成交量（供明日使用）
    if volumes:
        save_current_volume(volumes)
    
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