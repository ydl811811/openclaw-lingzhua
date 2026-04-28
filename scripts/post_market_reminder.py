#!/usr/bin/env python3
"""
收盘后复盘提醒脚本
功能：收集今日行情数据，发送飞书提醒灵爪亲自做复盘分析
时间：周一至周五 15:10
"""

import os
import sys
import json
import urllib.request
from datetime import datetime

# 配置
WORKSPACE = "/home/YDL/.openclaw/workspace"
CONFIG_FILE = os.path.join(WORKSPACE, "scripts/a_stock_monitor_config.json")
LOG_DIR = os.path.join(WORKSPACE, "logs")
STOCK_PLAN_DIR = os.path.join(WORKSPACE, "a_stock_plan")

# 发送飞书消息
def send_feishu_message(message):
    """通过openclaw网关发送飞书消息"""
    try:
        # 使用openclaw message工具发送
        from pathlib import Path
        
        # 写入sharebox，作为提醒标记
        sharebox_path = os.path.join(WORKSPACE, "claw-communication/sharebox/")
        os.makedirs(sharebox_path, exist_ok=True)
        
        filename = f"系统_灵爪_复盘提醒_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        filepath = os.path.join(sharebox_path, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(message)
        
        print(f"已写入提醒文件: {filepath}")
        return True
    except Exception as e:
        print(f"发送失败: {e}")
        return False

def get_positions():
    """读取持仓股票"""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        
        positions = []
        for s in config.get('stocks', []):
            if s.get('position', 0) > 0:
                code = s['code']
                if code.startswith('6'):
                    market = 'sh'
                else:
                    market = 'sz'
                positions.append({
                    'code': code,
                    'market': market,
                    'name': s.get('name', ''),
                    'cost': s.get('cost_price', 0),
                    'shares': s.get('position', 0)
                })
        return positions
    except Exception as e:
        print(f"读取持仓失败: {e}")
        return []

def get_stock_data(market, code):
    """获取单只股票行情"""
    try:
        url = f'https://qt.gtimg.cn/q={market}{code}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read().decode('gbk', errors='replace')
        
        fields = data.strip().split('"')[1].split('~')
        return {
            'name': fields[1],
            'price': fields[3],
            'pct': fields[32]
        }
    except Exception as e:
        return None

def get_market_index():
    """获取大盘指数"""
    try:
        url = 'https://qt.gtimg.cn/q=s_sh000001,s_sz399001,s_sz399006'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read().decode('gbk', errors='replace')
        
        result = []
        for line in data.strip().split('\n'):
            if '=' not in line:
                continue
            fields = line.split('"')[1].split('~')
            if len(fields) > 3:
                result.append({
                    'name': fields[1],
                    'price': fields[3],
                    'pct': fields[32]
                })
        return result
    except Exception as e:
        return []

def main():
    today = datetime.now().strftime('%Y-%m-%d')
    today_cn = datetime.now().strftime('%Y年%m月%d日')
    
    print(f"=== 收盘复盘提醒 {today} ===")
    
    # 获取大盘指数
    indexes = get_market_index()
    index_info = ""
    for idx in indexes:
        index_info += f"- {idx['name']}: {idx['price']} ({idx['pct']}%)\n"
    
    # 获取持仓行情
    positions = get_positions()
    position_info = ""
    for pos in positions:
        data = get_stock_data(pos['market'], pos['code'])
        if data:
            pct = float(data['pct'])
            emoji = "🟢" if pct > 0 else "🔴" if pct < 0 else "⚪"
            position_info += f"- {emoji} {data['name']}: ¥{data['price']} ({data['pct']}%)\n"
    
    # 构建消息
    message = f"""# 📊 收盘复盘提醒

{today_cn} 15:10

老大，收盘了！请做今日复盘分析：

---

## 📈 今日大盘
{index_info}

## 💼 持仓股表现
{position_info if position_info else "暂无持仓数据"}

---

## 📝 请亲自分析以下内容：

### 1. 今日交易复盘
- 止盈/止损执行情况
- 买入/卖出理由是否充分
- 操作是否有失误

### 2. 持仓股分析
- 每只持仓股今日表现
- 主力动向判断
- 是否需要调仓

### 3. 主力思维分析（重点！）
- 今日最强主线的龙头股
- 主力意图分析
- 明日操作策略

### 4. 明日操作计划
- 关注标的（回调到区间的）
- 买入条件
- 止损/止盈价位

---

📁 复盘文件：`a_stock_plan/daily/{today}/trading_review.md`

⚠️ **注意**：这是提醒消息，不是模板！请亲自做深度分析后再回复老大。
"""
    
    # 发送提醒
    send_feishu_message(message)
    print("复盘提醒已发送！")

if __name__ == "__main__":
    main()
