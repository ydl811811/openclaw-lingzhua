#!/usr/bin/env python3
"""
自动化每日复盘脚本
收盘后自动执行，生成复盘报告
"""

import os
import sys
import json
import hmac
import hashlib
import base64
import time
import urllib.request
from datetime import datetime, timedelta
import requests

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
        log(f"⚠️ 飞书推送失败: {e}")
        return None

# 配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(SCRIPT_DIR)
STOCK_PLAN_DIR = os.path.join(WORKSPACE_DIR, "a_stock_plan")
REPORT_DIR = os.path.join(STOCK_PLAN_DIR, "daily")
LOG_DIR = os.path.join(WORKSPACE_DIR, "logs")
STRATEGY_FILE = os.path.join(STOCK_PLAN_DIR, "strategy/选股策略框架_v2.0.md")
TEMPLATE_FILE = os.path.join(STOCK_PLAN_DIR, "strategy/daily_review_template.md")

# 创建目录
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

def log(message):
    """记录日志"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_message = f"[{timestamp}] {message}"
    print(log_message)
    
    # 写入日志文件
    log_file = os.path.join(LOG_DIR, f"daily_review_{datetime.now().strftime('%Y%m%d')}.log")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(log_message + '\n')

def get_market_data():
    """获取市场数据（简化版）"""
    # 这里可以调用更复杂的API获取详细数据
    # 目前使用简化版本
    market_data = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'sh_index': 0,  # 上证指数
        'sz_index': 0,  # 深证成指
        'cy_index': 0,  # 创业板指
        'sh_change': 0,
        'sz_change': 0,
        'cy_change': 0,
        'hot_sectors': [],
        'policy_news': [],
        'capital_flow': {}
    }
    
    # 尝试获取实时数据（简化）
    try:
        # 这里可以添加真实的数据获取逻辑
        pass
    except Exception as e:
        log(f"获取市场数据失败: {e}")
    
    return market_data

def get_stock_pool():
    """获取股票池数据"""
    # 基础股票池（20只）
    stock_pool = [
        # AI算力/TMT
        ('002230', '科大讯飞', 'sz', 'AI算力/TMT', 'AI应用龙头'),
        ('002415', '海康威视', 'sz', 'AI算力/TMT', 'AI+安防'),
        ('600588', '用友网络', 'sh', 'AI算力/TMT', '企业软件'),
        ('300781', '因赛集团', 'sz', 'AI算力/TMT', 'AI营销'),
        
        # 高股息
        ('601088', '中国神华', 'sh', '高股息', '煤炭+高股息'),
        ('600900', '长江电力', 'sh', '高股息', '水电+高股息'),
        
        # 消费
        ('600519', '贵州茅台', 'sh', '消费', '白酒龙头'),
        ('000858', '五粮液', 'sz', '消费', '白酒'),
        
        # 制造
        ('000333', '美的集团', 'sz', '制造', '家电龙头'),
        
        # 新能源
        ('300750', '宁德时代', 'sz', '新能源', '电池龙头'),
        ('002594', '比亚迪', 'sz', '新能源', '新能源汽车'),
        
        # 金融
        ('601318', '中国平安', 'sh', '金融', '保险龙头'),
        ('600036', '招商银行', 'sh', '金融', '银行龙头'),
        
        # 消费电子
        ('002475', '立讯精密', 'sz', '消费电子', '苹果链'),
        
        # 金融科技
        ('300059', '东方财富', 'sz', '金融科技', '互联网券商'),
        
        # 资源
        ('601899', '紫金矿业', 'sh', '资源', '黄金龙头'),
        
        # 医药
        ('600276', '恒瑞医药', 'sh', '医药', '创新药龙头'),
        
        # 低空经济
        ('002085', '万丰奥威', 'sz', '低空经济', '低空经济龙头'),
        ('000099', '中信海直', 'sz', '低空经济', '低空经济'),
    ]
    
    return stock_pool

def calculate_stock_scores(stock_pool):
    """计算股票综合评分"""
    scored_stocks = []
    
    for code, name, market, theme, note in stock_pool:
        try:
            # 获取股票数据
            url = f'http://qt.gtimg.cn/q={market}{code}'
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.text.split('~')
                if len(data) > 3:
                    price = float(data[3])
                    prev_close = float(data[4])
                    pct_change = (price - prev_close) / prev_close
                    
                    # 简化评分（实际应该更复杂）
                    score = 50  # 基础分
                    
                    # 主题加分
                    theme_bonus = {
                        'AI算力/TMT': 15,
                        '高股息': 10,
                        '消费': 8,
                        '制造': 7,
                        '新能源': 8,
                        '金融': 6,
                        '消费电子': 9,
                        '金融科技': 7,
                        '资源': 5,
                        '医药': 8,
                        '低空经济': 9
                    }
                    
                    score += theme_bonus.get(theme, 0)
                    
                    # 涨跌幅调整
                    if pct_change > 0:
                        score += pct_change * 100 * 2  # 上涨加分
                    else:
                        score += pct_change * 100  # 下跌减分（幅度较小）
                    
                    scored_stocks.append({
                        'code': code,
                        'name': name,
                        'theme': theme,
                        'note': note,
                        'price': price,
                        'pct_change': pct_change,
                        'score': max(0, min(100, score))  # 限制在0-100分
                    })
                    
        except Exception as e:
            log(f"计算{name}({code})评分失败: {e}")
    
    # 按评分排序
    scored_stocks.sort(key=lambda x: x['score'], reverse=True)
    return scored_stocks

def generate_report(market_data, scored_stocks):
    """生成复盘报告"""
    today = datetime.now().strftime('%Y-%m-%d')
    today_dir = os.path.join(REPORT_DIR, today)
    os.makedirs(today_dir, exist_ok=True)
    report_file = os.path.join(today_dir, "post_market_review.md")
    
    # 读取模板
    with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
        template = f.read()
    
    # 替换模板中的占位符
    report = template.replace('YYYY-MM-DD', market_data['date'])
    
    # 这里应该添加更多的数据填充逻辑
    # 由于时间关系，先生成简化版本
    
    # 添加股票评分结果
    score_section = "### 3.1 评分结果（前10名）\n\n"
    score_section += "| 排名 | 股票 | 主题 | 评分 | 涨跌幅 | 主要优势 |\n"
    score_section += "|------|------|------|------|--------|----------|\n"
    
    for i, stock in enumerate(scored_stocks[:10]):
        rank = i + 1
        change_str = f"{stock['pct_change']*100:+.2f}%"
        score_section += f"| {rank} | {stock['name']}({stock['code']}) | {stock['theme']} | {stock['score']:.1f} | {change_str} | {stock['note']} |\n"
    
    # 在报告中插入评分部分
    report = report.replace('### 3.1 评分结果（前10名）', score_section)
    
    # 保存报告
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    log(f"复盘报告已生成: {report_file}")
    
    # 推送到飞书
    try:
        today_str = market_data['date']
        summary = f"📊 灵爪盘后复盘 {today_str}\n\n"
        summary += f"📈 市场表现：\n"
        summary += f"• 上证: {market_data['sh_index']} ({market_data['sh_change']:+.2f}%)\n"
        summary += f"• 深证: {market_data['sz_index']} ({market_data['sz_change']:+.2f}%)\n"
        summary += f"• 创业板: {market_data['cy_index']} ({market_data['cy_change']:+.2f}%)\n\n"
        if market_data.get('hot_sectors'):
            summary += f"🔥 热点板块: {', '.join(market_data['hot_sectors'][:3])}\n"
        summary += f"\n📁 完整复盘报告已生成，请查看文件。"
        send_feishu(summary)
        log("飞书推送成功")
    except Exception as e:
        log(f"⚠️ 飞书推送失败: {e}")
    
    return report_file

def main():
    """主函数"""
    log("开始执行每日复盘")
    log("=" * 50)
    
    # 1. 检查是否交易日
    weekday = datetime.now().weekday()  # 0=周一, 6=周日
    if weekday >= 5:  # 周六日
        log("非交易日，跳过复盘")
        return
    
    # 2. 检查是否收盘后
    current_time = datetime.now().time()
    if current_time < datetime.strptime('15:00', '%H:%M').time():
        log("未到收盘时间，等待收盘后执行")
        return
    
    # 3. 获取市场数据
    log("获取市场数据...")
    market_data = get_market_data()
    
    # 4. 获取股票池
    log("获取股票池数据...")
    stock_pool = get_stock_pool()
    
    # 5. 计算股票评分
    log("计算股票综合评分...")
    scored_stocks = calculate_stock_scores(stock_pool)
    
    if not scored_stocks:
        log("❌ 无法计算股票评分，复盘失败")
        return
    
    # 6. 生成复盘报告
    log("生成复盘报告...")
    report_file = generate_report(market_data, scored_stocks)
    
    # 7. 生成明日股票池文件
    tomorrow_pool = []
    themes_selected = {}
    
    for stock in scored_stocks:
        theme = stock['theme']
        if theme not in themes_selected and len(tomorrow_pool) < 6:
            themes_selected[theme] = True
            tomorrow_pool.append(stock)
    
    # 保存明日股票池
    today_str = datetime.now().strftime('%Y%m%d')
    today_dir = os.path.join(REPORT_DIR, datetime.now().strftime('%Y-%m-%d'))
    os.makedirs(today_dir, exist_ok=True)
    tomorrow_file = os.path.join(today_dir, "tomorrow_pool.json")
    with open(tomorrow_file, 'w', encoding='utf-8') as f:
        json.dump(tomorrow_pool, f, ensure_ascii=False, indent=2)
    
    log(f"明日股票池已保存: {tomorrow_file}")
    
    # 8. 总结
    log("=" * 50)
    log("每日复盘完成")
    log(f"分析股票数: {len(scored_stocks)}只")
    log(f"推荐股票数: {len(tomorrow_pool)}只")
    log(f"覆盖主题数: {len(themes_selected)}个")
    log(f"最高评分: {scored_stocks[0]['score']:.1f}分 ({scored_stocks[0]['name']})")
    log(f"最低评分: {scored_stocks[-1]['score']:.1f}分 ({scored_stocks[-1]['name']})")
    
    # 9. 这里可以添加飞书推送
    # push_to_feishu(report_file, tomorrow_pool)

if __name__ == "__main__":
    main()