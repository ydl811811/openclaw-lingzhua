#!/usr/bin/env python3
"""
盘前持仓分析 + 今日交易策略
运行时间：每个交易日 08:30
自动分析持仓股，生成操作建议并推送飞书
"""
import os
import sys
import json
import time
import warnings
from datetime import datetime, date, timedelta
import urllib.request as urllib

warnings.filterwarnings('ignore')

WORKSPACE = "/home/YDL/.openclaw/workspace"
CACHE_DIR = "/home/YDL/.openclaw/agent_stock_work/cache_temp"

# 飞书推送
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"

# ============ 持仓配置 ============
POSITIONS = {
    '000783': {'name': '长江证券', 'cost': 7.338, 'qty': 600, 'stop': 6.80, 'take': 8.10, 'market': 'sz'},
    '002230': {'name': '科大讯飞', 'cost': 48.00, 'qty': 100, 'stop': 44.60, 'take': 52.80, 'market': 'sz'},
    '601088': {'name': '中国神华', 'cost': 46.73, 'qty': 100, 'stop': 43.00, 'take': 52.00, 'market': 'sh'},
    '600900': {'name': '长江电力', 'cost': 26.895, 'qty': 200, 'stop': 25.00, 'take': 30.00, 'market': 'sh'},
}

# ============ 数据获取 ============

def get_tushare():
    import tushare as ts
    return ts.pro_api('063dde2a5efdda7c004459717c2ca8b93bb63ce24fdbb9abad3e8a3e')

def get_kline(ts_code, days=20):
    try:
        pro = get_tushare()
        end = date.today().strftime('%Y%m%d')
        start = (date.today() - timedelta(days=30)).strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        return df.sort_values('trade_date') if df is not None and len(df) > 0 else None
    except:
        return None

def get_index_data():
    try:
        pro = get_tushare()
        start = (date.today() - timedelta(days=5)).strftime('%Y%m%d')
        end = date.today().strftime('%Y%m%d')
        df = pro.index_daily(ts_code='000001.SH', start_date=start, end_date=end)
        if df is not None and len(df) >= 2:
            latest = df.iloc[-1]
            return {
                'close': latest.get('close', 0),
                'pct': latest.get('pct_chg', 0),
            }
    except:
        pass
    return {'close': 0, 'pct': 0}

# ============ 技术分析 ============

def analyze_position(code, pos_info):
    ts_code = code + '.SH' if pos_info['market'] == 'sh' else code + '.SZ'
    df = get_kline(ts_code, days=20)
    
    result = {
        'code': code,
        'name': pos_info['name'],
        'cost': pos_info['cost'],
        'qty': pos_info['qty'],
        'stop': pos_info['stop'],
        'take': pos_info['take'],
    }
    
    if df is None or len(df) < 5:
        result['error'] = '数据不足'
        return result
    
    import numpy as np
    closes = df['close'].values
    vols = df['vol'].values
    
    latest_close = closes[-1]
    latest_pct = float(df.iloc[-1]['pct_chg']) if 'pct_chg' in df.columns else 0
    
    ma5 = np.mean(closes[-5:]) if len(closes) >= 5 else None
    ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else None
    ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else None
    
    if ma5 and ma10 and ma20:
        # 计算MA5和MA10的差值比例
        ma_diff_pct = abs(ma5 - ma10) / ma10 * 100 if ma10 != 0 else 0
        
        if ma5 > ma10 > ma20:
            if ma_diff_pct < 0.5:
                ma_status = '横盘待变'  # MA5刚金叉MA10，趋势待确认
            else:
                ma_status = '多头排列'
        elif ma5 < ma10 < ma20:
            if ma_diff_pct < 0.5:
                ma_status = '横盘待变'  # MA5接近MA10，不是真正的空头
            else:
                ma_status = '空头排列'
        else:
            ma_status = '混乱'
    else:
        ma_status = '数据不足'
    
    dist_stop = (latest_close - pos_info['stop']) / pos_info['stop'] * 100
    dist_take = (pos_info['take'] - latest_close) / latest_close * 100
    dist_cost = (latest_close - pos_info['cost']) / pos_info['cost'] * 100
    
    avg_vol = np.mean(vols)
    vol_ratio = vols[-1] / avg_vol if avg_vol > 0 else 1
    
    high_20 = np.max(closes[-20:]) if len(closes) >= 20 else np.max(closes)
    low_20 = np.min(closes[-20:]) if len(closes) >= 20 else np.min(closes)
    pos_in_range = (latest_close - low_20) / (high_20 - low_20) * 100 if high_20 != low_20 else 50
    
    result.update({
        'price': round(latest_close, 2),
        'pct': round(latest_pct, 2),
        'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma_status': ma_status,
        'vol_ratio': round(vol_ratio, 1),
        'high_20': round(high_20, 2),
        'low_20': round(low_20, 2),
        'pos_in_range': round(pos_in_range, 0),
        'dist_cost': round(dist_cost, 2),
        'dist_stop': round(dist_stop, 1),
        'dist_take': round(dist_take, 1),
        'pnl': round((latest_close - pos_info['cost']) * pos_info['qty'], 0),
        'pnl_pct': round(dist_cost, 2),
    })
    
    return result

# ============ 策略制定 ============

def make_strategy(analysis):
    p = analysis
    if 'error' in p:
        return {'action': '数据异常', 'signal': '黄灯', 'advice': '等数据恢复', 'priority': 'Y'}
    
    signals = []
    
    # 1. 止损检查
    if p['price'] <= p['stop']:
        return {
            'action': '止损',
            'signal': '红灯',
            'advice': '现价%.2f已跌破止损%.2f，必须执行！' % (p['price'], p['stop']),
            'priority': 'R'
        }
    
    # 2. 目标检查
    if p['price'] >= p['take']:
        signals.append(('目标区', 'G', '已达目标%.2f，考虑部分止盈' % p['take']))
    
    # 3. 均线检查
    if '空头排列' in p['ma_status']:
        signals.append(('均线转空', 'R', 'MA均线空头排列，考虑减仓'))
    elif '多头排列' in p['ma_status']:
        signals.append(('均线多头', 'G', 'MA均线健康，可继续持有'))
    
    # 4. 位置检查
    if p['pos_in_range'] >= 90:
        signals.append(('高位风险', 'Y', '处于20日高点%.2f附近，注意获利了结' % p['high_20']))
    elif p['pos_in_range'] <= 20:
        signals.append(('低位支撑', 'G', '接近20日低点%.2f，可加仓' % p['low_20']))
    
    # 5. 距止损距离
    if p['dist_stop'] < 5:
        signals.append(('止损警戒', 'R', '距止损仅%.1f%%，谨慎' % p['dist_stop']))
    elif p['dist_stop'] < 10:
        signals.append(('止损注意', 'Y', '距止损%.1f%%，关注' % p['dist_stop']))
    
    # 6. 盈亏
    if p['dist_cost'] > 0:
        signals.append(('已盈利', 'G', '浮盈%.1f%%' % p['dist_cost']))
    else:
        signals.append(('亏损中', 'Y', '浮亏%.1f%%' % p['dist_cost']))
    
    # 综合判断
    red_count = sum(1 for s in signals if s[1] == 'R')
    yellow_count = sum(1 for s in signals if s[1] == 'Y')
    green_count = sum(1 for s in signals if s[1] == 'G')
    
    if red_count > 0:
        signal = '红灯'
    elif yellow_count > 1:
        signal = '黄灯'
    elif green_count >= 2 and red_count == 0:
        signal = '绿灯'
    else:
        signal = '黄灯'
    
    advice_parts = [s[2] for s in signals[:4]]
    advice = '；'.join(advice_parts)
    
    return {
        'action': '持有',
        'signal': signal,
        'advice': advice,
        'priority': 'R' if red_count > 0 else ('Y' if yellow_count > 0 else 'G'),
        'signals': signals
    }

# ============ 发送飞书 ============

def send_feishu(msg):
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
        req = urllib.Request(FEISHU_BOT_URL, data=data, headers={'Content-Type': 'application/json'})
        with urllib.urlopen(req, timeout=10):
            pass
        print("飞书推送成功")
    except Exception as e:
        print("飞书推送失败: %s" % e)

# ============ 主程序 ============

def main():
    print("=" * 50)
    print("盘前持仓分析 + 今日策略")
    print("=" * 50)
    print("时间: %s" % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    market = get_index_data()
    print("\n大盘: 上证指数 %.2f (%.2f%%)" % (market['close'], market['pct']))
    
    results = []
    for code, pos in POSITIONS.items():
        print("\n分析 %s（%s）..." % (pos['name'], code))
        analysis = analyze_position(code, pos)
        strategy = make_strategy(analysis)
        results.append(dict(**analysis, **strategy))
        print("  %s: %.2f (%s) %s" % (pos['name'], analysis.get('price', 0), strategy['signal'], strategy['action']))
    
    # 构建飞书消息
    today_str = date.today().strftime('%Y-%m-%d')
    
    red_count = sum(1 for r in results if r.get('signal') == '红灯')
    yellow_count = sum(1 for r in results if r.get('signal') == '黄灯')
    green_count = sum(1 for r in results if r.get('signal') == '绿灯')
    
    header = "【%s 盘前持仓策略】\n\n大盘: 上证指数 %.2f (%.2f%%)\n\n持仓状态：G%d Y%d R%d\n\n" % (
        today_str, market['close'], market['pct'], green_count, yellow_count, red_count
    )
    
    body = ""
    for r in results:
        priority_icon = {'G': 'G', 'Y': 'Y', 'R': 'R'}.get(r.get('priority', 'Y'), '?')
        status_icon = 'G' if r.get('dist_cost', 0) >= 0 else 'R'
        
        if 'error' not in r:
            line = "%s %s(%.2f%%) %s\n  成本%.2f 盈亏%s%.1f%% 区间%.2f~%.2f\n  %s %s\n  %s\n\n" % (
                priority_icon,
                r['name'],
                r['dist_cost'],
                r.get('ma_status', ''),
                r['cost'],
                '+' if r['dist_cost'] >= 0 else '',
                r['dist_cost'],
                r['low_20'],
                r['high_20'],
                r.get('action', ''),
                r.get('advice', ''),
                r.get('signals', [])
            )
        else:
            line = "%s %s - 数据异常\n\n" % (priority_icon, r['name'])
        body += line
    
    if red_count > 0:
        overall = "有持仓触及风险，需处理"
    elif yellow_count > 0:
        overall = "部分持仓需关注"
    else:
        overall = "持仓状态良好，持有为主"
    
    footer = "【今日整体策略】\n%s\n\n生成时间: %s" % (
        overall,
        datetime.now().strftime('%H:%M:%S')
    )
    
    msg = header + body + footer
    print("\n" + msg)
    
    send_feishu(msg)
    
    # 保存报告
    os.makedirs(CACHE_DIR, exist_ok=True)
    report_file = "%s/盘前策略_%s.txt" % (CACHE_DIR, today_str)
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(msg)
    print("\n报告已保存: %s" % report_file)
    
    return results

if __name__ == "__main__":
    main()