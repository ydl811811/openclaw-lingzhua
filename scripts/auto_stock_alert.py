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

# ============ 配置 ============
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"

# 备用持仓配置（当台账读取失败时使用）
FALLBACK_POSITIONS = {
    '600900': {'name': '长江电力', 'cost': 26.76, 'qty': 200, 'stop': 25.00, 'take': 30.00, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '高股息水电，止损25.00，目标30.00'},
    '603876': {'name': '鼎胜新材', 'cost': 26.20, 'qty': 200, 'stop': 23.50, 'take': 30.00, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '新能源/铝箔，止损23.50，目标30.00'},
    '588080': {'name': '科创50ETF', 'cost': 1.431, 'qty': 0, 'stop': 1.30, 'take': 2.00, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '🟡已卖出等接回，理想区间1.30-1.50'},
    '512480': {'name': '半导体ETF', 'cost': 2.007, 'qty': 0, 'stop': 1.80, 'take': 2.50, 'reduce': None, 'add': None, 'breakout_add': None, 'note': '🟡已卖出等接回，理想区间1.80-2.00'},
}

# 大盘指数
INDICES = {
    '000001': {'name': '上证指数', 'key_level': 4050, 'support': 4000},
    '399001': {'name': '深证成指'},
    '399006': {'name': '创业板指'},
}
INDEX_PREFIX = {'000001': 'sh', '399001': 'sz', '399006': 'sz'}

# ============ 函数 ============

def read_positions_from_ledger():
    """从交易记录台账读取当前持仓"""
    positions = {}
    
    if not os.path.exists(TRADING_LEDGER):
        print(f"⚠️ 交易台账不存在: {TRADING_LEDGER}")
        return FALLBACK_POSITIONS
    
    try:
        with open(TRADING_LEDGER, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 找到"当前持仓"表格
        in_position_section = False
        lines = content.split('\n')
        
        for line in lines:
            # 检测到"当前持仓"标题
            if '## 当前持仓' in line:
                in_position_section = True
                continue
            
            # 如果遇到另一个##开头的段落，退出持仓区域
            if in_position_section and line.strip().startswith('##'):
                break
            
            # 解析表格行
            if '|' in line and in_position_section:
                cells = [c.strip() for c in line.split('|')]
                if len(cells) < 8:
                    continue
                
                # 跳过表头
                if '股票' in cells[1] or '代码' in cells[1]:
                    continue
                
                # 跳过已卖出的行（包含~~）
                if '~~' in cells[1]:
                    continue
                
                try:
                    name = cells[1].replace('**', '')
                    code = cells[2].replace('**', '')
                    qty_str = cells[3].replace('**', '').replace('¥', '').replace(',', '')
                    qty = int(qty_str)
                    cost_str = cells[4].replace('**', '').replace('¥', '')
                    cost = float(cost_str)
                    stop_str = cells[6].replace('**', '').replace('¥', '')
                    stop = float(stop_str)
                    take_str = cells[7].replace('**', '').replace('¥', '')
                    # 提取止盈价（支持 TP1:27.50/TP2:29.00 格式 或 简单价格 27.50）
                    import re
                    # 先尝试匹配 TP1/TP2:价格 格式
                    tp1_match = re.search(r'TP1:?(\d+\.?\d*)', take_str)
                    tp2_match = re.search(r'TP2:?(\d+\.?\d*)', take_str)
                    if tp1_match:
                        take = float(tp1_match.group(1))
                        take2 = float(tp2_match.group(1)) if tp2_match else 0.0
                    else:
                        # 降级：提取第一个数字作为止盈价
                        all_nums = re.findall(r'[\d.]+', take_str)
                        take = float(all_nums[0]) if all_nums else 0.0
                        take2 = 0.0
                    
                    # 检查状态列
                    status = cells[8].replace('**', '') if len(cells) > 8 else ''
                    
                    # 排除已卖出的（状态包含"已卖出"、"已清仓"、"已止损"、"已止盈"）
                    if '已卖出' in status or '已清仓' in status or '已止损' in status or '已止盈' in status:
                        continue
                    
                    # 解析预警信息（如果有的话）
                    warning = None
                    if '预警:' in status:
                        import re
                        m = re.search(r'预警:?(\d+\.?\d*)', status)
                        if m:
                            warning = float(m.group(1))
                    
                    positions[code] = {
                        'name': name,
                        'cost': cost,
                        'qty': qty,
                        'stop': stop,
                        'take': take,
                        'take2': take2,  # 第二止盈目标（如TP2）
                        'warning': warning,  # 新增预警线（如47元）
                    }
                    print(f"  📥 读取持仓: {name} {code} x{qty}")
                except Exception as e:
                    pass  # 跳过解析失败的行
        
        if positions:
            print(f"  ✅ 从台账读取到 {len(positions)} 只持仓")
        else:
            print(f"  ⚠️ 台账中无有效持仓，使用备用配置")
            return FALLBACK_POSITIONS
            
    except Exception as e:
        print(f"  ❌ 读取台账失败: {e}")
        return FALLBACK_POSITIONS
    
    return positions

def get_realtime(codes):
    """获取实时数据（腾讯接口）"""
    prefixed = []
    for code in codes:
        if code in INDEX_PREFIX:
            prefixed.append(f"{INDEX_PREFIX[code]}{code}")
        elif code.startswith(('0','3')):
            prefixed.append(f"sz{code}")
        else:
            prefixed.append(f"sh{code}")
    
    if not prefixed:
        return {}
    
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
            'change_pct': float(fields[32]) if fields[32] else 0,
        }
    return results

def send_feishu(msg):
    """发送飞书（带签名验证）"""
    import hmac
    import hashlib
    import base64
    
    timestamp = str(int(time.time()))
    string_to_sign = timestamp + '\n' + FEISHU_SECRET
    sign = base64.b64encode(hmac.new(string_to_sign.encode(), digestmod=hashlib.sha256).digest()).decode()
    
    payload = {"msg_type": "text", "content": {"text": msg}}
    payload['timestamp'] = timestamp
    payload['sign'] = sign
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(FEISHU_BOT_URL, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print(f"飞书推送失败: {e}")
        return None

def write_sharebox_alert(msg):
    """写入sharebox，让灵爪能收到预警"""
    try:
        sharebox_path = "/home/YDL/.openclaw/workspace/claw-communication/sharebox/"
        os.makedirs(sharebox_path, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"股票预警_灵爪_{timestamp}.txt"
        filepath = os.path.join(sharebox_path, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(msg)
        
        print(f"✅ 已写入sharebox: {filename}")
        return True
    except Exception as e:
        print(f"sharebox写入失败: {e}")
        return False

def is_trading_hours():
    """检查是否在交易时间"""
    now = datetime.now()
    hour, minute = now.hour, now.minute
    weekday = now.weekday()
    if weekday >= 5:
        return False
    if 9 <= hour < 11 or (hour == 11 and minute <= 30):
        return True
    if 13 <= hour < 15:
        return True
    return False

def check(positions):
    """执行检查"""
    all_codes = list(positions.keys()) + list(INDICES.keys())
    results = get_realtime(all_codes)
    
    alerts = []
    
    # 检查持仓（含自选监控）
    for code, pos in positions.items():
        if code not in results:
            continue
        
        price = results[code]['price']
        pct = results[code]['change_pct']
        qty = pos.get('qty', 0)
        cost = pos['cost']
        stop = pos['stop']
        take = pos['take']
        take2 = pos.get('take2', 0)
        
        profit = (price - cost) * qty
        profit_pct = (price - cost) / cost * 100 if cost > 0 else 0
        
        # 有持仓的：检查止损/止盈/减仓/加仓/突破加仓
        if qty > 0:
            reduce_price = pos.get('reduce')
            add_price = pos.get('add')
            breakout_add = pos.get('breakout_add')
            
            # 止损（最优先）
            if price <= stop:
                alerts.append(f"🚨止损！{pos['name']} 现价{price} ≤ 止损{stop:.2f}")
            # 止盈检查（分两级）
            if price >= take2 and take2 > 0:
                alerts.append(f"🎯止盈(TP2)！{pos['name']} 现价{price} ≥ TP2目标{take2:.2f}")
            elif price >= take:
                alerts.append(f"🎯止盈(TP1)！{pos['name']} 现价{price} ≥ TP1目标{take:.2f}")
            # 减仓提醒（跌破减仓位但未到止损）
            elif reduce_price and price <= reduce_price:
                alerts.append(f"📍减仓提醒！{pos['name']} 现价{price} ≤ 减仓线{reduce_price:.2f}，建议减仓50%")
            # 突破加仓机会（追涨，突破前高/压力位）
            elif breakout_add and price >= breakout_add:
                alerts.append(f"🚀突破加仓！{pos['name']} 现价{price} ≥ 突破线{breakout_add:.2f}，可考虑加仓30%")
            # 回调加仓机会（低吸，跌到支撑位）
            elif add_price and price <= add_price:
                alerts.append(f"📥加仓机会！{pos['name']} 现价{price} ≤ 加仓线{add_price:.2f}，可考虑加仓")
            # 浮亏超3%
            elif profit_pct <= -3:
                alerts.append(f"⚠️浮亏超3%！{pos['name']} {profit_pct:.1f}%")
        else:
            # 无持仓（自选监控）：检查是否到买入区间
            buy_note = pos.get('note', '')
            if price <= stop:
                alerts.append(f"🟢买入信号！{pos['name']} 现价{price} ≤ 买入区间{stop:.2f} {buy_note}")
            elif price >= take:
                alerts.append(f"🔴价格到目标！{pos['name']} 现价{price} ≥ 目标{take:.2f} {buy_note}")
    
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
                    send_feishu(msg)  # 飞书通道1
                    write_sharebox_alert(msg)  # sharebox通道2 - 灵爪专用
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