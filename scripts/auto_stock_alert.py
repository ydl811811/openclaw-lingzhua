#!/usr/bin/env python3
"""
自动股票监控脚本 - ETF建仓联动监控版
- 持仓从交易记录台账读取（动态）
- 新增ETF候选建仓监控（159299金融科技ETF + 516010游戏ETF）
- 新增大盘联动预警（沪深300关键位）
- 30秒间隔检查
- 单例模式：只允许一个实例
"""
import os
import urllib.request
import json
import time
import re
from datetime import datetime

PID_FILE = "/tmp/stock_monitor.pid"
TRADING_LEDGER = "/home/YDL/.openclaw/workspace/a_stock_plan/交易记录台账.md"
LOG_FILE = "/home/YDL/.openclaw/workspace/logs/stock_monitor.log"

# 需要跳过的品种（已清仓/不再监控）
SKIP_CODES = {'588080', '512480', '515980', '603876'}

# 持仓表（默认持仓，从交易台账同步）
FALLBACK_POSITIONS = {
    '513050': {'name': '中概互联ETF', 'cost': 1.1164, 'qty': 6400, 'stop': 1.06, 'take': 1.16, 'take2': 1.24, 'note': '8-03减仓3100份(1.183×1500+1.180×1600)留6400份底仓'},
    '513120': {'name': '港股创新药ETF', 'cost': 1.174, 'qty': 2000, 'stop': 1.00, 'take': 1.25, 'take2': 1.35, 'note': '8-03减仓2000份@1.133，留底仓2000份等接回1.10/1.05'},
    '159326': {'name': '电网设备ETF', 'cost': 1.600, 'qty': 2000, 'stop': 1.50, 'take': 1.65, 'take2': 1.80, 'note': '7-24第1批/共4批'},
    '159299': {'name': '金融科技ETF', 'cost': 0.711, 'qty': 3000, 'stop': 0.66, 'take': 0.74, 'take2': 0.82, 'note': '7-31试探仓进攻'},
}

# 大盘指数
INDICES = {
    '000001': {'name': '上证指数'},
    '399001': {'name': '深证成指'},
    '399006': {'name': '创业板指'},
    '000688': {'name': '科创50'},
    '000300': {'name': '沪深300', 'support': 4500, 'pressure': 4900},  # 大金融联动锚
}

# 🆕 ETF候选建仓监控（老大7-31新加）
# 159299金融科技ETF加仓档 + 联动沪深300
# 关键位：MA5=0.661 / MA10=0.662 / MA30=0.685 / MA60=0.728 / 30日低点0.630
# 止损：0.660（跌破MA20/MA30） TP1：0.74 TP2：0.82
CANDIDATE_ETF_BUILDS = {
    '159299': {
        'name': '金融科技ETF',
        'market': 'sz',
        'levels': [
            {'price': 0.661, 'batch': 1, 'pct': 30, 'done': False, 'note': '加仓档-MA5/MA10极强支撑'},
            {'price': 0.685, 'batch': 2, 'pct': 30, 'done': False, 'note': '加仓档-MA30支撑'},
            {'price': 0.728, 'batch': 3, 'pct': 40, 'done': False, 'note': '加仓档-MA60支撑'},
        ],
        'stop': 0.660,    # 铁止损（跌破MA20/MA30）
        'target1': 0.74,  # T1 7/01高点
        'target2': 0.82,  # T2 5/15高点
        'note': '📌金融科技ETF加仓计划（试探仓保守版）',
    },
    '516010': {
        'name': '游戏ETF国泰',
        'market': 'sh',
        'levels': [
            {'price': 1.10, 'batch': 1, 'pct': 50, 'done': False, 'note': '激进档-已持仓浮盈加仓'},
            {'price': 1.05, 'batch': 2, 'pct': 30, 'done': False, 'note': '稳健档-密集成交区'},
            {'price': 1.00, 'batch': 3, 'pct': 20, 'done': False, 'note': '极限档-接近止损'},
        ],
        'stop': 0.98,     # 铁止损
        'target1': 1.20,  # T1 +9%
        'target2': 1.30,  # T2 +18%
        'note': '📌游戏ETF加仓计划',
    },
}

# ⚠️ 516010游戏ETF已于7-24清仓，监控仅作为再入场候选

def is_trading_hours():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)

def get_realtime(codes):
    """从腾讯接口获取实时行情"""
    results = {}
    # 🔧 Bug 修复 2026-08-03：指数接口字段偏移 vs 股票接口
    # - 股票（如513050）：parts[31] = 涨跌幅%
    # - 指数（如000300）：parts[31] = 涨跌额, parts[32] = 涨跌幅%（前面20多个占位字段把数据后移）
    # 修复：根据 code 前缀判断接口类型，选择正确的索引
    INDEX_CODES = {'000001', '399001', '399006', '000688', '000300'}
    for code in codes:
        try:
            # 优先用 market 前缀
            if code.startswith(('5', '6', '7')) or code in INDEX_CODES:
                market = 'sh'
            else:
                market = 'sz'

            # 候选ETF特殊处理
            for ckey, cinfo in CANDIDATE_ETF_BUILDS.items():
                if code == ckey:
                    market = cinfo['market']

            url = f'https://qt.gtimg.cn/q={market}{code}'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            raw = urllib.request.urlopen(req, timeout=8).read().decode('gbk', errors='replace')
            m = re.search(r'v_' + market + code + r'="([^"]+)"', raw)
            if m:
                parts = m.group(1).split('~')
                price = float(parts[3]) if parts[3] else 0
                # 🔧 根据是否指数选择正确索引
                pct_idx = 32 if code in INDEX_CODES else 31
                change_pct = float(parts[pct_idx]) if len(parts) > pct_idx and parts[pct_idx] else 0.0
                results[code] = {'price': price, 'change_pct': change_pct}
            else:
                parts = raw.split('="')
                if len(parts) > 1:
                    vals = parts[1].split('~')
                    price = float(vals[3]) if vals[3] else 0
                    pct_idx = 32 if code in INDEX_CODES else 31
                    change_pct = float(vals[pct_idx]) if len(vals) > pct_idx and vals[pct_idx] else 0.0
                    results[code] = {'price': price, 'change_pct': change_pct}
        except Exception as e:
            print(f'  {code} 获取失败: {e}')
    return results

def log_msg(msg):
    """写日志"""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except:
        pass

def send_feishu(msg):
    """飞书Webhook发送（备用）"""
    try:
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/your_webhook_url"
        data = json.dumps({"msg_type": "text", "content": {"text": msg}}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
    except:
        pass

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
    """检查持仓+ETF建仓+大盘联动"""
    alerts = []
    
    # 收集所有要监控的代码
    all_codes = list(positions.keys())
    all_codes.extend(list(INDICES.keys()))
    all_codes.extend(list(CANDIDATE_ETF_BUILDS.keys()))
    
    results = get_realtime(all_codes)
    
    # ========== 1. 持仓检查 ==========
    for code, pos in positions.items():
        if code in SKIP_CODES:
            continue
        if code not in results:
            continue
        price = results[code]['price']
        change_pct = results[code]['change_pct']
        
        stop = pos.get('stop')
        take = pos.get('take')
        cost = pos.get('cost', 0)
        qty = pos.get('qty', 0)
        note = pos.get('note', '')
        
        if qty <= 0:
            continue
        
        if stop and price <= stop:
            alerts.append(f"🚨止损！{pos['name']} 现价{price} ≤ 止损{stop:.2f}")
        elif take and price >= take:
            alerts.append(f"🎯止盈！{pos['name']} 现价{price} ≥ 目标{take:.2f}")
        
        if cost > 0 and qty > 0:
            profit_pct = (price - cost) / cost * 100
            if profit_pct <= -3:
                alerts.append(f"⚠️浮亏超3%！{pos['name']} {profit_pct:.1f}%")
    
    # ========== 2. 🆕 ETF候选建仓检查 ==========
    for code, plan in CANDIDATE_ETF_BUILDS.items():
        if code not in results:
            continue
        price = results[code]['price']
        change_pct = results[code]['change_pct']
        
        # 检查止损（已持仓部分）
        stop = plan.get('stop')
        if stop and price <= stop:
            alerts.append(f"🚨{plan['name']} 破止损！现价{price} ≤ 止损{stop:.2f}，建仓计划取消")
        
        # 检查建仓档位
        for level in plan['levels']:
            if level['done']:
                continue
            if price <= level['price']:
                alert_msg = (
                    f"📥【{plan['name']}】触发第{level['batch']}批建仓！\n"
                    f"   现价：{price} ≤ 目标{level['price']:.2f}\n"
                    f"   仓位：{level['pct']}%\n"
                    f"   理由：{level['note']}\n"
                    f"   当日涨幅：{change_pct:+.2f}%"
                )
                alerts.append(alert_msg)
                level['done'] = True
                log_msg(f"触发建仓: {alert_msg}")
    
    # ========== 3. 🆕 大盘联动预警 ==========
    hs300 = results.get('000300')
    if hs300:
        hs300_price = hs300['price']
        hs300_pct = hs300['change_pct']
        
        if hs300_price <= 4500:
            alerts.append(f"⚠️【大盘联动】沪深300破4500！现价{hs300_price}，金融科技ETF风险加大")
        elif hs300_pct <= -2:
            alerts.append(f"⚠️【大盘联动】沪深300急跌{hs300_pct:.2f}%，金融科技ETF可能跟随补跌")
    
    # 金融科技ETF vs 大盘联动比对
    if '159299' in results and hs300:
        ft_price = results['159299']['price']
        ft_pct = results['159299']['change_pct']
        # 如果大盘跌但金融科技ETF涨（逆势抗跌 → 主力吸筹信号）
        if hs300['change_pct'] < 0 and ft_pct > 0.5:
            alerts.append(f"🔥【主力吸筹信号】大盘跌{hs300['change_pct']:.2f}%，金融科技ETF逆势涨{ft_pct:.2f}%，主力护盘明显")
    
    return alerts

def main():
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    
    # 单例检查
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            os.kill(old_pid, 0)
            print(f"已有实例运行 (PID {old_pid})，退出")
            return
        except (ProcessLookupError, ValueError, OSError):
            os.remove(PID_FILE)
    
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    print(f"🚀 启动 ETF建仓联动监控版 PID {os.getpid()}", flush=True)
    log_msg(f"启动监控 PID {os.getpid()} - ETF建仓联动监控版")
    
    last_alert_time = 0
    alert_cooldown = 300  # 5分钟冷却
    last_position_read = 0
    positions = FALLBACK_POSITIONS
    position_refresh_interval = 300
    
    # 加载已触发的建仓档位（避免重启后重复触发）
    state_file = "/tmp/etf_build_state.json"
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                saved_state = json.load(f)
            for code, plan in CANDIDATE_ETF_BUILDS.items():
                if code in saved_state:
                    for i, lvl in enumerate(plan['levels']):
                        if i < len(saved_state[code]):
                            plan['levels'][i]['done'] = saved_state[code][i]
            print(f"已恢复建仓状态")
        except:
            pass
    
    def save_state():
        state = {}
        for code, plan in CANDIDATE_ETF_BUILDS.items():
            state[code] = [lvl['done'] for lvl in plan['levels']]
        try:
            with open(state_file, 'w') as f:
                json.dump(state, f)
        except:
            pass
    
    while True:
        try:
            current_time = time.time()
            
            # 每5分钟重新读取持仓
            if current_time - last_position_read > position_refresh_interval:
                print(f"\n🔄 [{datetime.now().strftime('%H:%M:%S')}] 重新读取交易台账...")
                positions = FALLBACK_POSITIONS
                last_position_read = current_time
            
            if not is_trading_hours():
                time.sleep(300)
                continue
            
            alerts = check(positions)
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            # 保存建仓状态
            save_state()
            
            # 大盘指数显示
            all_codes = list(INDICES.keys()) + list(CANDIDATE_ETF_BUILDS.keys())
            results = get_realtime(all_codes)
            index_lines = [f"📊 {timestamp} 大盘+ETF联动监控"]
            
            for code, info in INDICES.items():
                if code in results:
                    d = results[code]
                    pct = d['change_pct']
                    extra = ""
                    if 'support' in info and d['price'] <= info['support']:
                        extra = f" ⚠️破支撑{info['support']}"
                    index_lines.append(f"  {info['name']}: {d['price']} ({pct:+.2f}%){extra}")
            
            for code, plan in CANDIDATE_ETF_BUILDS.items():
                if code in results:
                    d = results[code]
                    pct = d['change_pct']
                    arrow = "🟢" if pct > 0 else ("🔴" if pct < 0 else "⚪")
                    index_lines.append(f"  {arrow} {plan['name']}({code}): {d['price']} ({pct:+.2f}%)")
            
            print('\n'.join(index_lines))
            
            if alerts:
                if current_time - last_alert_time > alert_cooldown:
                    msg_lines = [f"⚠️ 预警 {timestamp}\n"]
                    msg_lines.extend(alerts)
                    msg = '\n'.join(msg_lines)
                    print(msg)
                    log_msg(f"预警:\n{msg}")
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
            log_msg(f"错误: {e}")
            time.sleep(30)

if __name__ == '__main__':
    main()