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
def get_volume_from_sina(codes):
    """通过新浪财经接口获取今日成交量数据（更稳定）"""
    import requests
    import re
    try:
        prefixed = []
        for code in codes:
            if code.startswith(('5', '6', '7', '9')):
                prefixed.append(f"sh{code}")
            else:
                prefixed.append(f"sz{code}")
        
        url = f'http://hq.sinajs.cn/list={",".join(prefixed)}'
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://finance.sina.com.cn/'}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'gbk'
        
        volumes = {}
        for line in resp.text.split('\n'):
            m = re.search(r'hq_str_(\w+)="(.+?)"', line)
            if not m:
                continue
            code_raw = m.group(1).replace('sh', '').replace('sz', '')
            content = m.group(2)
            parts = content.split(',')
            if len(parts) >= 9:
                try:
                    # 新浪数据格式: parts[8] = 成交量(股)
                    volume_shares = int(parts[8])
                    volumes[code_raw] = volume_shares
                except:
                    pass
        return volumes
    except Exception as e:
        print(f"[SinaVol] 获取成交量失败: {e}")
        return {}

def get_volume_from_adata(codes):
    """通过adata API获取今日成交量数据（备用）"""
    try:
        cmd = f'/usr/bin/python3.12 /home/yu/.hermes/skills/adata-stock-data/scripts/fetch_data.py realtime {" ".join(codes)}'
        result = subprocess.run(
            ['ssh', '-i', '/home/YDL/.ssh/id_ed25519', '-o', 'StrictHostKeyChecking=no',
             'yu@192.168.31.141', cmd],
            capture_output=True, text=True, timeout=30
        )
        volumes = {}
        for line in result.stdout.strip().split('\n'):
            line = line.strip()  # 去除前后空格
            if line.startswith('stock_code') or not line:
                continue
            parts = line.split()
            if len(parts) >= 6:
                code = parts[0]
                try:
                    volume = int(parts[5])  # volume is at index 5
                    volumes[code] = volume
                except:
                    pass
        return volumes
    except Exception as e:
        print(f"[AdataVol] 获取成交量失败: {e}")
        return {}

def get_5day_avg_volume(codes):
    """通过新浪财经接口获取前5个交易日均量（用于缩量判断，更稳定）"""
    import requests
    import json
    avg_volumes = {}
    try:
        for code in codes:
            try:
                # 新浪财经K线接口
                symbol = f'sz{code}' if not code.startswith(('5', '6', '7', '9')) else f'sh{code}'
                url = f'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=5'
                resp = requests.get(url, timeout=10)
                data = json.loads(resp.text)
                
                if data and len(data) > 0:
                    # 取最新的5日均量数据（ma_volume5字段）
                    latest = data[-1]
                    ma_vol = float(latest.get('ma_volume5', 0))
                    if ma_vol > 0:
                        avg_volumes[code] = ma_vol
            except Exception as e:
                print(f"[SinaKline] 获取{code}均量失败: {e}")
                continue
    except Exception as e:
        print(f"[SinaKline] 获取均量失败: {e}")
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
# === 2026-05-07 持仓更新：已持仓移出候选池，添加持仓监控 ===
WATCHLIST = {
    '002240': {'name': '盛新锂能', 'buy_low': 57.00, 'buy_high': 58.50, 'stop': 55.00, 'note': '等回调到57-58.5区间，目标62-65，止损55'},
    '002475': {'name': '立讯精密', 'buy_low': 68.50, 'buy_high': 70.50, 'stop': 67.00, 'note': '等回调68.5-70区间买，目标74-76，止损67'},
    '301510': {'name': '固高科技', 'buy_low': 39.00, 'buy_high': 41.00, 'stop': 38.50, 'note': '等回调39-40区间，目标43-45，止损38.5'},
    '002810': {'name': '山东赫达', 'buy_low': 26.50, 'buy_high': 28.00, 'stop': 25.50, 'note': '26.5-28区间，目标30-32，止损25.5'},
    '300552': {'name': '万集科技', 'buy_low': 26.50, 'buy_high': 27.50, 'stop': 26.00, 'note': 'AI基础设施/ETC，业绩增速606%，目标30-32，止损26'},
    '300400': {'name': '劲拓股份', 'buy_low': 27.50, 'buy_high': 29.00, 'stop': 27.00, 'note': '等回调27.5-28.5，目标32-35，止损27'},
    '300613': {'name': '富瀚微', 'buy_low': 58.00, 'buy_high': 60.00, 'stop': 54.00, 'note': '🔥AI芯片主线已卖出现价62.4。策略A回调58-60买（优先），止损54，目标65-72；策略B突破65追（次选），止损60，目标72'},
}

# ============ 已持仓监控（止损+潜在加仓区间）============
# 这些是我们已买入的股票，监控是否触发止损或加仓机会
HOLDINGS = {
    '600900': {'name': '长江电力', 'cost': 26.895, 'stop': 25.00, 'target': 30.00, 'note': '🟢高股息长线持有，止损25，目标30'},
    '603876': {'name': '鼎胜新材', 'cost': 29.163, 'stop': 29.50, 'target': 35.00, 'note': '✅持仓200股@29.163，止损29.5，目标35。⚠️05-08分析：关键压力33，能突破则继续持有。05-07主力有出货嫌疑，今日缩量反弹，不加仓。等突破33或回调28.5-29再加'},
    '300613': {'name': '富瀚微', 'cost': 58.22, 'stop': 54.00, 'target1': 65.00, 'target2': 72.00, 'note': '🟢AI芯片主线。05-08分析：突破65持有等72，不突破65则做T。策略：65突破→持有到72；65未破→65卖100股，62-63买回'},
    '600845': {'name': '宝信软件', 'cost': 23.861, 'stop': 22.00, 'target': 30.00, 'note': '✅持仓100股@23.861，止损22，目标30-35。⚠️05-08分析：100日均线(25.0)为当前压力。加仓建议：等回调24.0-24.2（保守）或等站稳25.0（追突破）。当前不追'},
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
    
    # 获取成交量数据（优先使用新浪接口，更稳定）
    volumes = get_volume_from_sina(codes)
    if not volumes:  # 如果新浪接口失败，尝试adata接口
        print("[Warn] Sina volume failed, trying adata...")
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