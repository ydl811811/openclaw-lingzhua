#!/usr/bin/env python3
"""
自动股票监控脚本 - 常驻单例版
- 常驻运行，30秒间隔检查
- 单例模式：只允许一个实例
- PID文件防止重复启动
- 持仓从交易记录台账读取（动态）
"""
import os
import urllib.request
import json
import time
import re
from datetime import datetime

PID_FILE = "/tmp/stock_monitor.pid"
TRADING_LEDGER = "/home/YDL/.openclaw/workspace/a_stock_plan/交易记录台账.md"

# 需要跳过的品种（已清仓/不再监控）
SKIP_CODES = {'588080', '512480', '515980', '603876'}

# 默认持仓
FALLBACK_POSITIONS = {
    '000100': {'name': 'TCL科技', 'cost': 6.16, 'qty': 800, 'stop': 5.40, 'take': 6.50, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '持有'},
    '600267': {'name': '海正药业', 'cost': 10.28, 'qty': 600, 'stop': 9.00, 'take': 11.50, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '持有'},
    '600900': {'name': '长江电力', 'cost': 26.785, 'qty': 200, 'stop': 25.50, 'take': 28.00, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '持有'},
    '159869': {'name': '游戏动漫ETF', 'cost': 1.099, 'qty': 5000, 'stop': 0.95, 'take': 1.18, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '持有'},
    '159516': {'name': '半导体设备ETF', 'cost': 1.766, 'qty': 0, 'stop': 1.68, 'take': 2.10, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '🟡已卖出，等接回区间1.65~1.72/1.53~1.58'},
    '002407': {'name': '多氟多', 'cost': 50.23, 'qty': 0, 'stop': 41.00, 'take': 55.00, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '🟡已卖出，等接回区间44~48'},
}

INDICES = {
    '000001': {'name': '上证指数'},
    '399001': {'name': '深证成指'},
    '399006': {'name': '创业板指'},
    '000688': {'name': '科创50'},
}

def is_trading_hours():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)

def get_realtime(codes):
    results = {}
    for code in codes:
        try:
            market = 'sh' if code.startswith(('6', '5')) or code in ('000001', '000688') else 'sz'
            url = f'https://qt.gtimg.cn/q={market}{code}'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            raw = urllib.request.urlopen(req, timeout=8).read().decode('gbk', errors='replace')
            m = re.search(r'v_' + market + code + r'="([^"]+)"', raw)
            if m:
                parts = m.group(1).split('~')
                price = float(parts[3]) if parts[3] else 0
                change_pct = float(parts[31]) if parts[31] else 0.0
                results[code] = {'price': price, 'change_pct': change_pct}
            else:
                # Try simple split
                parts = raw.split('="')
                if len(parts) > 1:
                    vals = parts[1].split('~')
                    price = float(vals[3]) if vals[3] else 0
                    change_pct = float(vals[31]) if vals[31] else 0.0
                    results[code] = {'price': price, 'change_pct': change_pct}
        except Exception as e:
            print(f'  {code} 获取失败: {e}')
    return results

def read_positions_from_ledger():
    """从交易台账读取当前持仓"""
    try:
        if os.path.exists(TRADING_LEDGER):
            with open(TRADING_LEDGER, 'r', encoding='utf-8') as f:
                content = f.read()
            # 解析台账获取持仓
            positions = {}
            for line in content.split('\n'):
                line = line.strip()
                if '|' not in line or '--' in line:
                    continue
                parts = [p.strip() for p in line.split('|')]
                # 查找包含代码的行
                for code, info in FALLBACK_POSITIONS.items():
                    if code in line:
                        positions[code] = info
            if positions:
                return positions
    except:
        pass
    return dict(FALLBACK_POSITIONS)

def send_feishu(msg):
    """通过飞书Webhook发送预警"""
    try:
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/your_webhook_url"
        data = json.dumps({"msg_type": "text", "content": {"text": msg}}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
    except:
        pass  # webhook失败不阻塞

SHAREBOX_PATH = "/home/YDL/.openclaw/workspace/claw-communication/sharebox/longzhua-box"

def write_sharebox_alert(msg):
    """写文件到longzhua-box供龙爪读取"""
    try:
        os.makedirs(SHAREBOX_PATH, exist_ok=True)
        fname = f"lingzhua_alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(os.path.join(SHAREBOX_PATH, fname), 'w', encoding='utf-8') as f:
            f.write(msg)
    except:
        pass

def check(positions):
    """检查持仓和ETF接回信号"""
    alerts = []
    all_codes = list(positions.keys())
    all_codes.extend(list(INDICES.keys()))
    results = get_realtime(all_codes)
    
    # 指数信息
    for code, info in INDICES.items():
        if code in results:
            d = results[code]
            info['price'] = d['price']
            info['change_pct'] = d['change_pct']
    
    # 持仓检查
    for code, pos in positions.items():
        if code in SKIP_CODES:
            continue
        if code not in results:
            continue
        price = results[code]['price']
        change_pct = results[code]['change_pct']
        
        stop = pos.get('stop')
        take = pos.get('take')
        take2 = pos.get('take2')
        reduce_price = pos.get('reduce')
        add_price = pos.get('add')
        breakout_add = pos.get('breakout_add')
        buy_price = pos.get('buy')
        cost = pos.get('cost', 0)
        qty = pos.get('qty', 0)
        note = pos.get('note', '')
        
        if qty <= 0:
            continue
        
        # 止损
        if stop and price <= stop:
            alerts.append(f"🚨止损！{pos['name']} 现价{price} ≤ 止损{stop:.2f}")
        # 止盈TP2
        elif take2 and price >= take2:
            alerts.append(f"🎯止盈(TP2)！{pos['name']} 现价{price} ≥ TP2目标{take2:.2f}")
        # 止盈TP1
        elif take and price >= take:
            alerts.append(f"🎯止盈(TP1)！{pos['name']} 现价{price} ≥ TP1目标{take:.2f}")
        # 减仓提醒
        if reduce_price and price <= reduce_price:
            alerts.append(f"📍减仓提醒！{pos['name']} 现价{price} ≤ 减仓线{reduce_price:.2f}，建议减仓50%")
        # 突破加仓
        if breakout_add and price >= breakout_add:
            alerts.append(f"🚀突破加仓！{pos['name']} 现价{price} ≥ 突破线{breakout_add:.2f}，可考虑加仓30%")
        # 加仓
        if add_price and price <= add_price:
            alerts.append(f"📥加仓机会！{pos['name']} 现价{price} ≤ 加仓线{add_price:.2f}，可考虑加仓")
        
        # 浮亏检查
        if cost > 0 and qty > 0:
            profit_pct = (price - cost) / cost * 100
            if profit_pct <= -3:
                alerts.append(f"⚠️浮亏超3%！{pos['name']} {profit_pct:.1f}%")
    
    # ========== ETF接回检查 ==========
    ETF_REBUY = {
        '588080': {'name': '科创50ETF', 'levels': [
            {'price': 1.796, 'batch': 1, 'done': False},
            {'price': 1.625, 'batch': 2, 'done': False},
            {'price': 1.563, 'batch': 3, 'done': False},
        ]},
        '515980': {'name': 'AI ETF', 'levels': [
            {'price': 1.146, 'batch': 1, 'done': False},
            {'price': 1.046, 'batch': 2, 'done': False},
            {'price': 0.989, 'batch': 3, 'done': False},
        ]},
    }
    
    # 获取ETF实时价格
    for code, info in ETF_REBUY.items():
        if code not in results:
            continue
        price = results[code]['price']
        for level in info['levels']:
            if level['done']:
                continue
            if price <= level['price']:
                alert_msg = f"🚨{info['name']} 触发第{level['batch']}批接回！现价{price:.3f} ≤ 目标{level['price']:.3f}，建议买入"
                alerts.append(alert_msg)
                level['done'] = True
    
    return alerts

def main():
    import sys
    sys.stdout.reconfigure(line_buffering=True)  # 即时输出
    
    # 单例检查
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            os.kill(old_pid, 0)  # 如果存在会成功
            print(f"已有实例运行 (PID {old_pid})，退出")
            return
        except (ProcessLookupError, ValueError, OSError):
            os.remove(PID_FILE)
    
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    print(f"启动 PID {os.getpid()}", flush=True)
    
    last_alert_time = 0
    alert_cooldown = 300  # 5分钟冷却
    last_position_read = 0
    positions = FALLBACK_POSITIONS
    position_refresh_interval = 300  # 每5分钟重新读取持仓
    
    while True:
        try:
            # 每5分钟重新读取持仓
            current_time = time.time()
            if current_time - last_position_read > position_refresh_interval:
                print(f"\n🔄 [{datetime.now().strftime('%H:%M:%S')}] 重新读取交易台账...")
                positions = read_positions_from_ledger()
                last_position_read = current_time
            
            if not is_trading_hours():
                time.sleep(300)  # 非交易时间休眠5分钟
                continue
            
            alerts = check(positions)
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            # 大盘指数
            all_codes = list(INDICES.keys())
            results = get_realtime(all_codes)
            index_lines = [f"📊 {timestamp}"]
            for code, info in INDICES.items():
                if code in results:
                    d = results[code]
                    pct = d['change_pct']
                    note = ""
                    if code == '000001':
                        if d['price'] >= 4050:
                            note = " ⚠️压力位"
                        elif d['price'] <= 4000:
                            note = " ⚠️支撑位"
                    index_lines.append(f"  {info['name']}: {d['price']} ({pct:+.2f}%){note}")
            print('\n'.join(index_lines))
            
            if alerts:
                if current_time - last_alert_time > alert_cooldown:
                    msg_lines = [f"⚠️ 预警 {timestamp}\n"]
                    msg_lines.extend(alerts)
                    msg = '\n'.join(msg_lines)
                    print(msg)
                    last_alert_time = current_time
                else:
                    remaining = alert_cooldown - (current_time - last_alert_time)
                    print(f"⏳ 预警冷却中({remaining:.0f}秒)")
            else:
                print(f"✅ [{timestamp}] 持仓正常")
            
            time.sleep(30)
            
        except KeyboardInterrupt:
            print("\n监控已停止")
            break
        except Exception as e:
            print(f"错误: {e}")
            time.sleep(30)

if __name__ == '__main__':
    main()
