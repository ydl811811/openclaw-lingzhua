#!/usr/bin/env python3
"""
自选股买入提醒脚本
- 监控未建仓股票，进入买入区间时提醒
- 30秒间隔检查
- 只发买入提醒，不发止盈提醒（因为还没买）
- 增加缩量条件：价格到位 + 缩量（今日量<昨日量）才提醒
- 2016-05-19: 修复重复发送问题，使用文件锁保证单实例
"""
import os
import sys
import fcntl
import time
from datetime import datetime
import subprocess

PID_FILE = "/tmp/watchlist_monitor.pid"
LOCK_FILE = "/tmp/watchlist_monitor.lock"
VOLUME_FILE = "/tmp/watchlist_volume.json"

# ============ 单实例锁定 ============
def acquire_lock():
    """使用文件锁确保只有一个实例运行"""
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd
    except (IOError, OSError) as e:
        if 'LOCK_NB' not in str(e) and hasattr(e, 'errno') and e.errno == 11:
            print(f"已有实例运行，无法获取锁 (PID_FILE: {PID_FILE})")
        else:
            print(f"无法获取锁: {e}")
        sys.exit(1)

def release_lock(lock_fd):
    """释放文件锁"""
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
        if os.path.exists(LOCK_FILE):
            os.unlink(LOCK_FILE)
    except:
        pass

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
# === 2026-06-18 收盘后基于前3天重新设定区间 ===
# 自选股池（未建仓，等待买入提醒）
# === 2026-06-22 清理：移除趋势走坏的个股，只保留ETF ===
# 移除：新劲刚/盛视科技/传艺科技/中国神华/立讯精密/万集科技/盛新锂能/宝信软件/华映科技/维信诺
# 原因：全部跌破MA20/MA60，短期不适合操作
WATCHLIST = {
    '515980': {'name': '人工智能ETF', 'buy_low': 1.10, 'buy_high': 1.25, 'stop': 1.00, 'note': '🟢长线轨，等接回，理想区间1.10-1.25（成本1.089已止盈），目标1.40-1.50，止损1.00'},
    '588080': {'name': '科创50ETF', 'buy_low': 1.30, 'buy_high': 1.50, 'stop': 1.20, 'note': '🟢长线轨，等接回，理想区间1.30-1.50（成本1.431已止盈），目标1.80-2.00，止损1.20'},
    '512480': {'name': '半导体ETF', 'buy_low': 1.80, 'buy_high': 2.00, 'stop': 1.70, 'note': '🟢长线轨，多头+站年线，等接回，区间1.80-2.00（成本2.007已止盈），目标2.30-2.50，止损1.70'},
    '159516': {'name': '半导体设备ETF', 'buy_low': 1.333, 'buy_high': 1.565, 'stop': 1.292, 'note': '🟢长线轨，多头+站年线，20日涨16.4%最强，区间1.33-1.57，止损1.29，目标1.80+'},
}

# ============ 已持仓监控（止损+潜在加仓区间）============
# 这些是我们已买入的股票，监控是否触发止损或加仓机会
HOLDINGS = {
    '600900': {'name': '长江电力', 'cost': 26.76, 'stop': 25.00, 'target': 30.00, 'note': '🟢持有200股，成本26.76，止损25.00，目标30.00'},
    '603876': {'name': '鼎胜新材', 'cost': 26.20, 'stop': 23.50, 'target': 30.00, 'note': '🟡持有200股，成本26.20，今日买，止损23.50，目标30.00'},
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
    """发送飞书提醒（带去重）"""
    import hashlib
    import hmac
    import base64
    import urllib.request
    import json
    
    # 去重检查：同一个消息5分钟内不重复发送
    now = time.time()
    msg_hash = hashlib.md5(msg.encode()).hexdigest()
    cache_key = f'/tmp/watchlist_alert_cache_{msg_hash}'
    
    try:
        if os.path.exists(cache_key):
            last_time = float(open(cache_key).read().strip())
            if now - last_time < 300:  # 5分钟内不重复
                return
        open(cache_key, 'w').write(str(now))
        # 清理过期缓存（24小时前）
        for f in os.listdir('/tmp'):
            if f.startswith('watchlist_alert_cache_') and (now - os.path.getmtime(f'/tmp/{f}') > 86400):
                try:
                    os.unlink(f'/tmp/{f}')
                except:
                    pass
    except:
        pass
    
    try:
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
    lock_fd = acquire_lock()
    
    try:
        main()
    except KeyboardInterrupt:
        print("\n停止监控")
    finally:
        release_lock(lock_fd)
        if os.path.exists(PID_FILE):
            os.unlink(PID_FILE)