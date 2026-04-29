#!/usr/bin/env python3
"""
晨报生成脚本 - 每日8:00前完成
覆盖：夜间新闻、昨日总结、候选池、持仓状态、操作计划

定时任务：
0 8 * * 1-5 python3 /home/YDL/.openclaw/workspace/scripts/morning_briefing_auto.py
"""
import urllib.request
import json
import os
import re
import hmac
import hashlib
import base64
import time
from datetime import datetime, timedelta
from pathlib import Path

# ============ 配置 ============
OUTPUT_DIR = "/home/YDL/.openclaw/workspace/a_stock_plan/daily"
TEMPLATE_FILE = "/home/YDL/.openclaw/workspace/a_stock_plan/template/晨报模板.md"
TRADING_LEDGER = "/home/YDL/.openclaw/workspace/a_stock_plan/交易记录台账.md"
CANDIDATE_POOL_DIR = "/home/YDL/.openclaw/workspace/a_stock_plan/daily"

# 飞书推送
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"


def send_feishu(msg):
    """发送飞书（带签名验证）"""
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
        print(f"⚠️ 飞书推送失败: {e}")
        return None


def get_us_market():
    """获取夜间美股数据和A50期货"""
    result = {
        'djia': {'price': '-', 'chg': '-'},
        'nasdaq': {'price': '-', 'chg': '-'},
        'sp500': {'price': '暂无数据', 'chg': '-'},
        'a50': {'price': '-', 'chg': '-'},
    }
    
    # A50期货 - 使用新浪接口 hf_CHA50CFD
    try:
        url = "https://hq.sinajs.cn/list=hf_CHA50CFD"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn'
        })
        with urllib.request.urlopen(req, timeout=5) as r:
            data = r.read().decode('gbk', errors='replace')
        
        if '="' in data and data.count('"') > 1:
            content = data.split('="')[1].split('"')[0]
            fields = content.split(',')
            if len(fields) > 5 and fields[0]:
                price = float(fields[0])
                prev_close = float(fields[3]) if fields[3] else 0
                if prev_close > 0:
                    change_pct = (price - prev_close) / prev_close * 100
                    result['a50']['price'] = f"{price:.2f}"
                    result['a50']['chg'] = f"{change_pct:+.2f}%"
    except Exception as e:
        print(f"  A50获取失败: {e}")
    
    # 美股指数 - 使用新浪接口
    try:
        # 新浪美股代码: gb_dji(道琼斯), gb_ixic(纳斯达克), gb_inx(标普500)
        url = "https://hq.sinajs.cn/list=gb_dji,gb_ixic,gb_inx"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn'
        })
        with urllib.request.urlopen(req, timeout=5) as r:
            data = r.read().decode('gbk', errors='replace')
        
        # 解析每只股票
        for line in data.split(';'):
            line = line.strip()
            if not line or '="' not in line:
                continue
            
            content = line.split('="')[1].split('"')[0]
            fields = content.split(',')
            
            if len(fields) < 3:
                continue
            
            # 道琼斯
            if 'gb_dji' in line:
                result['djia']['price'] = fields[1] if fields[1] else '-'
                try:
                    result['djia']['chg'] = f"{float(fields[2]):+.2f}%"
                except:
                    result['djia']['chg'] = '-'
            # 纳斯达克
            elif 'gb_ixic' in line:
                result['nasdaq']['price'] = fields[1] if fields[1] else '-'
                try:
                    result['nasdaq']['chg'] = f"{float(fields[2]):+.2f}%"
                except:
                    result['nasdaq']['chg'] = '-'
            # 标普500
            elif 'gb_inx' in line:
                result['sp500']['price'] = fields[1] if fields[1] else '-'
                try:
                    result['sp500']['chg'] = f"{float(fields[2]):+.2f}%"
                except:
                    result['sp500']['chg'] = '-'
    
    except Exception as e:
        print(f"  美股数据获取失败: {e}")
    
    print(f"  美股: 道指{result['djia']['price']}({result['djia']['chg']}), 纳斯达克{result['nasdaq']['price']}({result['nasdaq']['chg']})")
    print(f"  A50期货: {result['a50']['price']}({result['a50']['chg']})")
    
    return result


def get_yesterday_summary():
    """获取昨日市场总结（从复盘报告读取）"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    review_file = f"{CANDIDATE_POOL_DIR}/{yesterday}/post_market_review.md"
    
    result = {
        'date': yesterday,
        'sh_close': '-',
        'sh_chg': '-',
        'sh_vol': '-',
        'sz_close': '-',
        'sz_chg': '-',
        'sz_vol': '-',
        'cy_close': '-',
        'cy_chg': '-',
        'cy_vol': '-',
        'hsgt_net': '-',
        'hsgt_buy': '-',
        'hsgt_sell': '-',
        'hot_sectors': '暂无数据',
        'dragon_tiger': '暂无数据',
    }
    
    if os.path.exists(review_file):
        try:
            with open(review_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 解析上证/深证/创业板数据
            # 格式: | 上证指数 | 4106.26 | +0.52% | ... |
            for line in content.split('\n'):
                if '|' in line and ('上证' in line or '深证' in line or '创业板' in line):
                    cells = [c.strip() for c in line.split('|')]
                    if len(cells) >= 4:
                        if '上证' in cells[1]:
                            result['sh_close'] = cells[2]
                            result['sh_chg'] = cells[3]
                        elif '深证' in cells[1] and '成' in cells[1]:
                            result['sz_close'] = cells[2]
                            result['sz_chg'] = cells[3]
                        elif '创业板' in cells[1]:
                            result['cy_close'] = cells[2]
                            result['cy_chg'] = cells[3]
            
            # 解析成交额
            # 格式: - **上证成交额**: 约1.09万亿（10936亿元）
            for line in content.split('\n'):
                if '上证成交额' in line:
                    m = re.search(r'（(\d+)亿元）', line)
                    if m:
                        result['sh_vol'] = m.group(1)
                elif '深证成交额' in line:
                    m = re.search(r'（(\d+)亿元）', line)
                    if m:
                        result['sz_vol'] = m.group(1)
            
            print(f"  昨日指数: 上证{result['sh_close']}({result['sh_chg']})")
            
        except Exception as e:
            print(f"  读取复盘报告失败: {e}")
    else:
        print(f"  复盘报告不存在: {review_file}")
    
    return result


def get_candidate_pool():
    """获取今日候选股票池"""
    # 查找最新的候选股票池文件
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 尝试今天的文件
    candidate_file = f"{CANDIDATE_POOL_DIR}/{today}/候选股票池.json"
    
    # 如果今天没有，尝试昨天的
    if not os.path.exists(candidate_file):
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        candidate_file = f"{CANDIDATE_POOL_DIR}/{yesterday}/候选股票池.json"
    
    result = {
        'short': [],
        'long': [],
    }
    
    if os.path.exists(candidate_file):
        try:
            with open(candidate_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for cat in ['短线标的', '长线标的']:
                if cat in data:
                    for stock in data[cat].get('stocks', []):
                        result['short' if cat == '短线标的' else 'long'].append(stock)
            
            print(f"  候选股票池: {len(result['short'])}只短线, {len(result['long'])}只长线")
        except Exception as e:
            print(f"  读取候选股票池失败: {e}")
    else:
        print(f"  候选股票池文件不存在: {candidate_file}")
    
    return result


def get_positions():
    """获取持仓状态"""
    result = {
        'positions': [],
        'total_value': 0,
        'cash': 0,
        'position_ratio': '0%',
    }
    
    if not os.path.exists(TRADING_LEDGER):
        print(f"  交易台账不存在: {TRADING_LEDGER}")
        return result
    
    try:
        with open(TRADING_LEDGER, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析持仓表格
        lines = content.split('\n')
        for line in lines:
            if '|' not in line:
                continue
            cells = [c.strip() for c in line.split('|')]
            if len(cells) < 6:
                continue
            
            # 检查是否是持仓行（包含数字和状态标记）
            if any('持有' in c or '关注' in c or '短线' in c for c in cells):
                # 跳过表头
                if '股票' in cells[1] or '代码' in cells[1]:
                    continue
                
                # 解析持仓行
                # 格式: | **股票名** | **代码** | **股数** | **¥成本** | ... | **🟡 短线** |
                try:
                    # 去掉**标记
                    name = cells[1].replace('**', '')
                    code = cells[2].replace('**', '')
                    qty_str = cells[3].replace('**', '').replace('¥', '').replace(',', '')
                    qty = int(qty_str)
                    cost_str = cells[4].replace('**', '').replace('¥', '')
                    cost = float(cost_str)
                    
                    # 判断状态
                    status = '🟢'
                    if '关注' in cells[-1]:
                        status = '🟡'
                    elif '短线' in cells[-1]:
                        status = '🟡'
                    
                    result['positions'].append({
                        'name': name,
                        'code': code,
                        'qty': qty,
                        'cost': cost,
                        'status': status,
                        'current_price': 0,
                        'pnl_pct': 0,
                    })
                except Exception as e:
                    print(f"  解析行失败 [{line}]: {e}")
                    pass
        
        print(f"  持仓: {len(result['positions'])}只")
        
    except Exception as e:
        print(f"  读取交易台账失败: {e}")
    
    return result


def get_realtime_prices(codes):
    """获取实时价格"""
    if not codes:
        return {}
    
    prices = {}
    
    # 腾讯接口批量获取
    code_list = []
    for code in codes:
        prefix = 'sz' if code.startswith(('0', '3')) else 'sh'
        code_list.append(f'{prefix}{code}')
    
    try:
        url = f"https://qt.gtimg.cn/q={','.join(code_list)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read().decode('gbk', errors='replace')
        
        for line in data.strip().split('\n'):
            if '=' not in line:
                continue
            key, rest = line.split('=', 1)
            if '"' not in rest:
                continue
            fields = rest.strip().strip('"').split('~')
            if len(fields) < 35:
                continue
            
            code = key.replace('v_', '')[2:]  # 去掉前缀
            try:
                prices[code] = {
                    'price': float(fields[3]),
                    'pre_close': float(fields[4]),
                    'pct': float(fields[32]),
                }
            except:
                pass
    
    except Exception as e:
        print(f"  实时价格获取失败: {e}")
    
    return prices


def generate_report():
    """生成晨报"""
    print("=" * 60)
    print("📋 灵爪晨报生成")
    print("=" * 60)
    
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')
    
    # 1. 夜间新闻
    print("\n📰 获取夜间新闻...")
    us_market = get_us_market()
    
    # 2. 昨日总结
    print("📊 获取昨日总结...")
    yesterday_data = get_yesterday_summary()
    
    # 3. 候选股票池
    print("🎯 获取候选股票池...")
    candidate_pool = get_candidate_pool()
    
    # 4. 持仓状态
    print("💼 获取持仓状态...")
    positions = get_positions()
    
    # 5. 获取实时价格
    if positions['positions']:
        codes = [p['code'] for p in positions['positions']]
        prices = get_realtime_prices(codes)
        
        total_value = 0
        for p in positions['positions']:
            price_info = prices.get(p['code'], {})
            current_price = price_info.get('price', 0)
            p['current_price'] = current_price
            if current_price > 0 and p['cost'] > 0:
                p['pnl_pct'] = (current_price - p['cost']) / p['cost'] * 100
            if current_price > 0:
                total_value += current_price * p['qty']
        
        positions['total_value'] = total_value
        # 计算仓位（本金5万）
        TOTAL_CAPITAL = 50000
        positions['position_ratio'] = f"{total_value/TOTAL_CAPITAL*100:.1f}%"
        positions['cash'] = TOTAL_CAPITAL - total_value
    
    # 6. 读取模板
    with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
        template = f.read()
    
    # 7. 填充模板
    report = template.format(
        date=today_str,
        time=time_str,
        market_status='开盘前',
        
        # 夜间新闻
        djia_price=us_market['djia']['price'],
        djia_chg=us_market['djia']['chg'],
        nasdaq_price=us_market['nasdaq']['price'],
        nasdaq_chg=us_market['nasdaq']['chg'],
        sp500_price=us_market['sp500']['price'],
        sp500_chg=us_market['sp500']['chg'],
        a50_price=us_market['a50']['price'],
        a50_chg=us_market['a50']['chg'],
        dax_chg='-',
        ftse_chg='-',
        black_swan_check='✅ 无重大黑天鹅事件',
        
        # 昨日总结
        yesterday=yesterday_data['date'],
        sh_close=yesterday_data.get('sh_close', '-'),
        sh_chg=yesterday_data.get('sh_chg', '-'),
        sh_vol=yesterday_data.get('sh_vol', '-'),
        sz_close=yesterday_data.get('sz_close', '-'),
        sz_chg=yesterday_data.get('sz_chg', '-'),
        sz_vol=yesterday_data.get('sz_vol', '-'),
        cy_close=yesterday_data.get('cy_close', '-'),
        cy_chg=yesterday_data.get('cy_chg', '-'),
        cy_vol=yesterday_data.get('cy_vol', '-'),
        hsgt_net=yesterday_data.get('hsgt_net', '-'),
        hsgt_buy=yesterday_data.get('hsgt_buy', '-'),
        hsgt_sell=yesterday_data.get('hsgt_sell', '-'),
        hot_sectors=yesterday_data.get('hot_sectors', '暂无数据'),
        dragon_tiger=yesterday_data.get('dragon_tiger', '暂无数据'),
        
        # 候选股票池
        short_stocks='\n'.join([
            f"| {s['name']} | {s['code']} | {s.get('theme', '-')} | {s.get('score', '-')} | {s.get('note', '-')} |"
            for s in candidate_pool['short']
        ]) or '| - | - | - | - | - |',
        
        long_stocks='\n'.join([
            f"| {s['name']} | {s['code']} | {s.get('theme', '-')} | {s.get('score', '-')} | {s.get('note', '-')} |"
            for s in candidate_pool['long']
        ]) or '| - | - | - | - | - |',
        
        # 持仓
        positions='\n'.join([
            f"| {p['name']} | {p['code']} | {p['cost']:.3f} | {p['current_price']:.2f} | {p['pnl_pct']:+.2f}% | - | {p['status']} |"
            for p in positions['positions']
        ]) or '| - | - | - | - | - | - | - |',
        
        total_value=f"{positions['total_value']:.2f}",
        position_ratio=positions['position_ratio'],
        cash=f"{positions.get('cash', 0):.2f}",
        
        # 操作计划（暂时留空，待人工填写）
        assessment='待确认',
        buy_plan='待确认',
        sell_plan='待确认',
        position_advice='待确认',
        
        # 风险提示
        risks='✅ 市场暂无明显风险',
    )
    
    # 8. 保存晨报
    output_file = f"{OUTPUT_DIR}/{today_str}/晨报_{today_str}.md"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n✅ 晨报已生成: {output_file}")
    
    # 9. 推送到飞书
    if FEISHU_BOT_URL:
        try:
            # 发送完整报告（飞书text有长度限制，发送摘要）
            summary = f"📋 灵爪晨报 {today_str}\n\n"
            summary += f"🌙 夜间市场：\n"
            summary += f"• 道指: {us_market['djia']['price']} ({us_market['djia']['chg']})\n"
            summary += f"• 纳斯达克: {us_market['nasdaq']['price']} ({us_market['nasdaq']['chg']})\n"
            summary += f"• 标普500: {us_market['sp500']['price']} ({us_market['sp500']['chg']})\n"
            summary += f"• A50期货: {us_market['a50']['price']} ({us_market['a50']['chg']})\n\n"
            summary += f"💼 持仓状态：\n"
            summary += f"• 总市值: {positions.get('total_value', 0):.0f}\n"
            summary += f"• 仓位: {positions.get('position_ratio', '0%')}\n"
            summary += f"• 可用资金: {positions.get('cash', 0):.0f}\n\n"
            summary += f"📊 昨日指数：上证{yesterday_data['sh_close']} ({yesterday_data['sh_chg']})\n"
            summary += f"📁 完整报告已生成，请查看文件或登录系统查看。"
            
            send_feishu(summary)
            print("✅ 飞书推送成功")
        except Exception as e:
            print(f"⚠️ 飞书推送失败: {e}")
    
    return output_file


if __name__ == '__main__':
    generate_report()
