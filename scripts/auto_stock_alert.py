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

# 飞书 Webhook 配置（2026-08-05 13:39 从 a_stock_plan/scripts/auto_stock_alert.py 恢复）
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"

# 需要跳过的品种（已清仓/不再监控）
SKIP_CODES = {'588080', '512480', '515980', '603876'}

# 持仓表（默认持仓，从交易台账同步）
FALLBACK_POSITIONS = {
    '513050': {'name': '中概互联ETF', 'cost': 1.1164, 'qty': 3400, 'stop': 1.10, 'take': 1.20, 'take2': 1.24, 'note': '8-03减仓6100份 + 8-04减仓3000份 → 剩3400份底仓；8-05 13:53 老大拍板上调TP1 1.16→1.20，TP2 1.24不变/极限 1.28'},
    '513120': {'name': '港股创新药ETF', 'cost': 1.1773, 'qty': 3000, 'stop': 1.00, 'take': 1.22, 'take2': 1.24, 'note': '8-06 09:37 加仓2000份@1.179 成交（底仓1000+新加2000）→ 总仓3000份@均价1.1773；8-05 14:09 挂1.18/2000份条件单expired，今早手动重挂成交'},
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

# 🆕 8-04 562800 稀有金属ETF嘉实（纯观察）
# 背景：13:20 老大指示加入观察股
# 性质：长期下跌 -22% + 今天首次强反弹 +2.10%
# 策略：纯观察不介入，等回踩 0.85（7/20 阶段底 + 二次探底颈线）
# 老大拍板：2026-08-04 13:24
OBSERVATION_WATCH = {
    '562800': {
        'name': '稀有金属ETF嘉实',
        'market': 'sh',
        'observe_only': True,  # 纯观察，不主动触发预警
        'levels': [
            {'price': 0.85, 'batch': 1, 'done': False, 'note': '观察触发-7/20阶段底'},
            {'price': 0.83, 'batch': 2, 'done': False, 'note': '观察触发-二次探底颈线'},
        ],
        'note': '📌562800加入观察股（8-04 13:24 老大拍板，观望等深位）',
    },
}

# 🆕 8-04 513120 港股创新药ETF接回计划（方案A改2批）

# 🆕 8-04 513120 港股创新药ETF接回计划（方案A改2批）
# 背景：8-03减仓2000份@1.133 + 8-04减仓1000份@1.169 → 仅剩1000份底仓@成本1.174
# 目标：分2批接回4000份 → 满仓5000份（综合成本 ¥1.135）
# 老大拍板：2026-08-04 11:05
RECOVERY_PLANS = {
    '513120': {
        'name': '港股创新药ETF',
        'market': 'sh',   # 8-04 灵爪踩坑后确认是 sh 前缀
        'base_qty': 3000,  # 8-06 09:37 加仓 2000 份@1.179 后修正
        'target_qty': 5000,  # 满仓份额（不变）
        'levels': [
            {'price': 1.150, 'batch': 1, 'qty': 2000, 'done': False, 'note': '第1档-跌破早盘低1.156后回踩1.150确认'},
            {'price': 1.100, 'batch': 2, 'qty': 2000, 'done': False, 'note': '第2档-跌到老大8-03原计划接回位'},
        ],
        'stop': 1.000,    # 铁止损（跌破全部清仓）
        'cease_buy': 1.100,  # 跌破此位 → 第2档不接，评估底仓
        'target1': 1.250,  # TP1 老大原计划
        'target2': 1.350,  # TP2 老大原计划
        'profit_taking': [   # 突破加仓（让利润奔跑）
            {'price': 1.180, 'qty': 2000, 'done': True, 'filled_at': '2026-08-06 09:37:58', 'filled_price': 1.179, 'note': '✅ 8-06 09:37:58 已成交2000份@1.179（8-05 14:09 挂单expired后今早手动市价买入）；不再触发预警'},
            {'price': 1.220, 'qty': 1000, 'note': 'W底目标位-再追加'},
        ],
        'note': '📌513120接回计划（8-04 11:05 老大拍板：方案A改2批；8-06 09:37 profit_taking[0] 1.180×2000份 已成交，base_qty 修正为 3000）',
    },
}



# 🆕 8-06 10:18 持久化预警状态（防老预警重发）
def _state_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'position_state.json')

def load_position_state():
    p = _state_path()
    if not os.path.exists(p):
        return {'take_done': {}, 'profit_taking_done': {}, 'recovery_done': {}}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log_msg(f"⚠️ 读 position_state.json 失败: {e}，用空状态")
        return {'take_done': {}, 'profit_taking_done': {}, 'recovery_done': {}}

def save_position_state(state):
    p = _state_path()
    state['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_msg(f"⚠️ 写 position_state.json 失败: {e}")


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
    """飞书 Webhook 发送（主推送通道，2026-08-05 13:39 恢复 + 加签名验证）
    从 a_stock_plan/scripts/auto_stock_alert.py 完整恢复
    """
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
        resp = urllib.request.urlopen(req, timeout=5)
        result = resp.read().decode('utf-8')
        print(f"  ✅ 飞书推送: {result[:100]}")
        return True
    except Exception as e:
        print(f"  ❌ 飞书推送失败: {e}")
        return False

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

def parse_positions_from_ledger(ledger_path):
    """从交易台账解析当前持仓 (2026-08-05 13:25 v2 修复索引错位+限定持仓段)
    逻辑：只在「当前持仓」段落内匹配加粗持仓行，跳过历史持仓表/候选股池
    返回: { code: {name, cost, qty, stop, take, take2} } 或 None
    """
    try:
        with open(ledger_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"  ❌ 读取台账失败: {e}")
        return None

    # 1) 限定范围：只取「当前持仓」段（以 "## 当前持仓" 到下一个 "## " 之间）
    start = content.find("## 当前持仓")
    if start == -1:
        print("  ❌ 找不到「当前持仓」段落")
        return None
    # 找下一个 ## 标题作为结束
    end = content.find("\n## ", start + 10)
    if end == -1:
        end = len(content)
    section = content[start:end]

    positions = {}
    # 2) 匹配持仓行（带 **¥** 加粗价格的）
    #    格式: | **名称** | **代码** | **数量** | **¥成本** | 现价 | ... | **¥止损** | **¥目标1** | **¥目标2** | 状态 |
    pattern = r'\|\s*\*\*(.+?)\*\*\s*\|\s*\*\*(.+?)\*\*\s*\|\s*\*\*([\d,]+)\*\*\s*\|\s*\*\*¥([\d.]+)\*\*\s*\|'
    for m in re.finditer(pattern, section):
        name, code, qty_str, cost_str = m.groups()
        # 只取数字代码（6 位）
        if not code.isdigit() or len(code) != 6:
            continue
        qty = int(qty_str.replace(',', ''))
        if qty == 0:  # 跳过已清仓
            continue
        cost = float(cost_str)
        # 3) 提取同行后续的加粗 ¥价格
        line_start = m.start()
        line_end = section.find('\n', line_start)
        if line_end == -1:
            line_end = len(section)
        line = section[line_start:line_end]
        prices = re.findall(r'\*\*¥([\d.]+)\*\*', line)
        # 实际字段: [成本, 止损, 止盈1, 止盈2]
        stop  = float(prices[1]) if len(prices) >= 2 else 0
        take  = float(prices[2]) if len(prices) >= 3 else 0
        take2 = float(prices[3]) if len(prices) >= 4 else 0
        positions[code] = {
            'name': name,
            'cost': cost,
            'qty': qty,
            'stop': stop,
            'take': take,
            'take2': take2,
            'note': '',
        }
    return positions if positions else None

def check(positions):
    """检查持仓+ETF建仓+大盘联动"""
    state = load_position_state()  # 🆕 8-06 持久化预警状态
    alerts = []
    
    # 收集所有要监控的代码
    all_codes = list(positions.keys())
    all_codes.extend(list(INDICES.keys()))
    all_codes.extend(list(CANDIDATE_ETF_BUILDS.keys()))
    all_codes.extend(list(RECOVERY_PLANS.keys()))
    all_codes.extend(list(OBSERVATION_WATCH.keys()))
    
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
            take_key = code
            if state['take_done'].get(take_key):
                pass  # 今天已发过，不重复
            else:
                alerts.append(f"🎯止盈！{pos['name']} 现价{price} ≥ 目标{take:.2f}")
                state['take_done'][take_key] = {
                    'triggered_date': datetime.now().strftime('%Y-%m-%d'),
                    'triggered_price': price,
                    'note': f'已挂单/已处理，不再重复预警'
                }
        
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

    # ========== 2.5 🆕 8-04 513120 接回计划检查 ==========
    for code, plan in RECOVERY_PLANS.items():
        if code not in results:
            continue
        price = results[code]['price']
        change_pct = results[code]['change_pct']

        # 跌破铁止损 → 全部清仓预警
        stop = plan.get('stop')
        if stop and price <= stop:
            alerts.append(f"🚨【{plan['name']}】破铁止损！现价{price} ≤ {stop:.3f}，底仓{plan['base_qty']}份全部清仓")

        # 接回档位检测
        for level in plan['levels']:
            if level['done']:
                continue
            if price <= level['price']:
                alert_msg = (
                    f"📥【{plan['name']}】触发接回第{level['batch']}档！\n"
                    f"   现价：{price} ≤ 接回价{level['price']:.3f}\n"
                    f"   数量：{level['qty']}份（约¥{level['qty']*price:.0f}）\n"
                    f"   理由：{level['note']}\n"
                    f"   跳仓后总持仓：{plan['base_qty']} + 已跳档 = {plan['target_qty']}份目标\n"
                    f"   当日涨幅：{change_pct:+.2f}%"
                )
                alerts.append(alert_msg)
                level['done'] = True
                log_msg(f"触发接回: {alert_msg}")

        # 跌破接回截止位 → 提示不进
        cease_buy = plan.get('cease_buy')
        if cease_buy and price < cease_buy:
            alerts.append(f"⚠️【{plan['name']}】现价{price} < 接回截止{cease_buy:.3f}，第2档不接，评估是否清仓底仓")

        # 突破加仓位（让利润奔跑）
        for pt in plan.get('profit_taking', []):
            if pt.get('done'):
                continue  # 已成交的跳过
            pt_key = f"{code}:{pt['price']}"
            if state['profit_taking_done'].get(pt_key):
                continue  # 持久化已记录
            if price >= pt['price']:
                alerts.append(
                    f"🚀【{plan['name']}】突破{pt['price']:.3f}！考虑追加{pt['qty']}份进攻\n"
                    f"   理由：{pt['note']}"
                )
                state['profit_taking_done'][pt_key] = {
                    'triggered_date': datetime.now().strftime('%Y-%m-%d'),
                    'note': pt.get('note', '')
                }

    # ========== 2.6 🆕 8-04 562800 观察股触发检查 ==========
    for code, plan in OBSERVATION_WATCH.items():
        if code not in results:
            continue
        price = results[code]['price']
        change_pct = results[code]['change_pct']

        # 仅监控下跌触发（上涨不预警）
        for level in plan['levels']:
            if level['done']:
                continue
            if price <= level['price']:
                alert_msg = (
                    f"👀【{plan['name']}】跌到观察点！\n"
                    f"   现价：{price} ≤ 观察位{level['price']:.3f}\n"
                    f"   理由：{level['note']}\n"
                    f"   当日涨幅：{change_pct:+.2f}%\n"
                    f"   ⚠️仅提醒，老大决定是否建仓"
                )
                alerts.append(alert_msg)
                level['done'] = True
                log_msg(f"观察触发: {alert_msg}")

        # 上涨越过现价 +5% 以上 → 推送"加速上涨"提醒（不主动买）
        if change_pct >= 5.0:
            alerts.append(f"🚀【{plan['name']}】当日强势 +{change_pct:.2f}%！评估是否介入（老大决定）")
    
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
    
    save_position_state(state)
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
    
    # 加载已触发的建仓档位 + 接回档位（避免重启后重复触发）
    # 🆕 8-04 兼容老平铺格式 {"159299": [bool...]} / 新嵌套格式 {"candidates":{}, "recovery":{}}
    state_file = "/tmp/etf_build_state.json"
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                saved_state = json.load(f)

            # 判断格式：顶层是 code → [bool]（老）还是 candidates/recovery（嵌套新）
            is_old_format = any(k in saved_state for k in CANDIDATE_ETF_BUILDS.keys())

            if is_old_format:
                # 老格式直接读 candidate
                for code, plan in CANDIDATE_ETF_BUILDS.items():
                    if code in saved_state:
                        for i, lvl in enumerate(plan['levels']):
                            if i < len(saved_state[code]):
                                plan['levels'][i]['done'] = saved_state[code][i]
            else:
                # 新格式读 candidates 嵌套
                cand = saved_state.get('candidates', {})
                for code, plan in CANDIDATE_ETF_BUILDS.items():
                    if code in cand:
                        for i, lvl in enumerate(plan['levels']):
                            if i < len(cand[code]):
                                plan['levels'][i]['done'] = cand[code][i]

            # 接回档位（只在新格式里有）
            for code, plan in RECOVERY_PLANS.items():
                if code in saved_state.get('recovery', {}):
                    for i, lvl in enumerate(plan['levels']):
                        if i < len(saved_state['recovery'][code]):
                            plan['levels'][i]['done'] = saved_state['recovery'][code][i]

            # 🆕 8-04 观察股档位恢复（只在新格式里有）
            for code, plan in OBSERVATION_WATCH.items():
                if code in saved_state.get('observation', {}):
                    for i, lvl in enumerate(plan['levels']):
                        if i < len(saved_state['observation'][code]):
                            plan['levels'][i]['done'] = saved_state['observation'][code][i]
            print(f"已恢复建仓 + 接回 + 观察状态")
        except:
            pass
    
    def save_state():
        state = {
            'candidates': {},
            'recovery': {},
            'observation': {}
        }
        for code, plan in CANDIDATE_ETF_BUILDS.items():
            state['candidates'][code] = [lvl['done'] for lvl in plan['levels']]
        # 🆕 8-04 接回状态保存
        for code, plan in RECOVERY_PLANS.items():
            state['recovery'][code] = [lvl['done'] for lvl in plan['levels']]
        # 🆕 8-04 观察股状态保存
        for code, plan in OBSERVATION_WATCH.items():
            state['observation'][code] = [lvl['done'] for lvl in plan['levels']]
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
                # 🆕 2026-08-05 修复：真正从台账读取持仓，而不是用写死的 FALLBACK
                fresh_positions = parse_positions_from_ledger(TRADING_LEDGER)
                if fresh_positions:
                    positions = fresh_positions
                    print(f"  📥 读取持仓: {len(positions)} 只")
                    for code, pos in positions.items():
                        print(f"     {pos['name']}({code}) x{pos['qty']} @¥{pos['cost']}")
                else:
                    print("  ⚠️ 台账读取失败，沿用上次持仓")
                last_position_read = current_time
            
            if not is_trading_hours():
                time.sleep(300)
                continue
            
            alerts = check(positions)
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            # 保存建仓状态
            save_state()
            
            # 大盘指数显示
            all_codes = list(INDICES.keys()) + list(CANDIDATE_ETF_BUILDS.keys()) + list(RECOVERY_PLANS.keys()) + list(OBSERVATION_WATCH.keys())
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
            
            # 🆕 8-04 接回计划显示
            for code, plan in RECOVERY_PLANS.items():
                if code in results:
                    d = results[code]
                    pct = d['change_pct']
                    arrow = "🟢" if pct > 0 else ("🔴" if pct < 0 else "⚪")
                    # 标记哪些档已触发
                    done_str = '/'.join([f"L{l['batch']}✓" if l['done'] else f"L{l['batch']}" for l in plan['levels']])
                    index_lines.append(f"  🔁 {plan['name']}({code}): {d['price']} ({pct:+.2f}%) 接回进度[{done_str}]")
            
            # 🆕 8-04 观察股显示
            for code, plan in OBSERVATION_WATCH.items():
                if code in results:
                    d = results[code]
                    pct = d['change_pct']
                    arrow = "🟢" if pct > 0 else ("🔴" if pct < 0 else "⚪")
                    done_str = '/'.join([f"L{l['batch']}✓" if l['done'] else f"L{l['batch']}" for l in plan['levels']])
                    index_lines.append(f"  👀 {plan['name']}({code}): {d['price']} ({pct:+.2f}%) 观察点[{done_str}]")
            
            print('\n'.join(index_lines))
            
            if alerts:
                if current_time - last_alert_time > alert_cooldown:
                    msg_lines = [f"⚠️ 预警 {timestamp}\n"]
                    msg_lines.extend(alerts)
                    msg = '\n'.join(msg_lines)
                    print(msg)
                    log_msg(f"预警:\n{msg}")
                    # 🆕 2026-08-05 13:40 推送修复：真正调 send_feishu 发给老大
                    send_feishu(msg)
                    # 同时写 sharebox 备份通道
                    write_sharebox_alert(msg)
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