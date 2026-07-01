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
# === 2026-07-01 更新：只监控ETF接回，移除个股持仓监控（已集成到auto_stock_alert.py） ===
# 移除：长江电力、鼎胜新材（移到auto_stock_alert.py监控）
# 保留：ETF接回监控（只推送触发条件，不触发静默）
WATCHLIST = {
    '515980': {'name': '人工智能ETF', 'targets': [1.146, 1.046, 0.989], 'notes': ['第1批-MA20', '第2批-MA60', '第3批-0.618回撤'], 'done_batches': set()},
    '588080': {'name': '科创50ETF', 'targets': [1.796, 1.625, 1.563], 'notes': ['第1批-MA20', '第2批-MA60', '第3批-0.618回撤'], 'done_batches': set()},
    '512480': {'name': '半导体ETF', 'targets': [2.223, 1.908, 1.861], 'notes': ['第1批-MA20', '第2批-MA60', '第3批-0.618回撤'], 'done_batches': set()},
    '159516': {'name': '半导体设备ETF', 'targets': [1.333, 1.292], 'notes': ['第1批-MA20', '第2批-止损'], 'done_batches': set()},
}

# ============ 已移除个股持仓监控 ============
# 长江电力、鼎胜新材的止损/止盈监控已集成到 auto_stock_alert.py
# 本脚本现在专注于ETF接回监控

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
    """检查ETF是否跌到接回区间，只推送触发条件，不触发静默"""
    codes = list(WATCHLIST.keys())
    quotes = get_realtime(codes)
    
    alerts = []
    for code, info in WATCHLIST.items():
        if code not in quotes:
            continue
        price = quotes[code]['price']
        
        # 检查是否跌到任意接回目标价
        for i, target in enumerate(info['targets']):
            if i in info['done_batches']:
                continue  # 已触发的批次跳过
            if price <= target:
                note = info['notes'][i] if i < len(info['notes']) else f'第{i+1}批'
                alert = f"🚨{info['name']}({code}) 触发接回！\n现价{price:.3f} ≤ 目标{target:.3f}（{note}）\n建议买入操作"
                alerts.append((code, alert, i))
                info['done_batches'].add(i)
                break  # 每个ETF只报一次
    
    return alerts

def main():
    print("="*60)
    print("📋 ETF接回监控脚本启动")
    print("="*60)
    print(f"监控标的: {len(WATCHLIST)} 只")
    for code, info in WATCHLIST.items():
        print(f"  {code} {info['name']} 接回目标: {info['targets']}")
    print("="*60)
    print("规则: 只有触发接回价时才推送，不触发静默")
    print()
    
    last_alert_time = {}
    
    while True:
        try:
            now = datetime.now()
            
            # 非交易时间跳过
            if not is_trading_hours():
                time.sleep(60)
                continue
            
            alerts = check_watchlist()
            
            for code, alert, batch_idx in alerts:
                if code in last_alert_time:
                    if (now - last_alert_time[code]).seconds < 300:
                        continue
                
                print(f"\n{'='*50}")
                print(alert)
                print(f"{'='*50}")
                send_alert(alert)
                last_alert_time[code] = now
            
            # 没有触发时：完全静默，不打印任何内容
            
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