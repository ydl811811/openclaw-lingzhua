#!/bin/bash
# 2026-04-20 收盘后复盘提醒脚本
# 功能：收集今日行情数据，发送飞书提醒灵爪做复盘分析

WORKSPACE="/home/YDL/.openclaw/workspace"
DATE=$(date +%Y-%m-%d)
LOGFILE="$WORKSPACE/logs/post_market_review.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始收集今日行情数据..." >> "$LOGFILE"

# 今日日期格式
TODAY_STR=$(date '+%Y年%m月%d日'))
WEEKDAY=$(date '+%u')
IS_TRADING_DAY=false

# 判断是否为交易日（周一到周五）
if [ "$WEEKDAY" -ge 1 ] && [ "$WEEKDAY" -le 5 ]; then
    IS_TRADING_DAY=true
fi

if [ "$IS_TRADING_DAY" = false ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 今日不是交易日，跳过" >> "$LOGFILE"
    exit 0
fi

# 读取持仓股票代码
POSITIONS=$(python3 -c "
import json
with open('$WORKSPACE/scripts/a_stock_monitor_config.json') as f:
    config = json.load(f)
codes = []
for s in config['stocks']:
    if s.get('position', 0) > 0:
        code = s['code']
        if code.startswith('6'):
            codes.append('sh' + code)
        else:
            codes.append('sz' + code)
print(','.join(codes))
" 2>/dev/null)

if [ -z "$POSITIONS" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 无持仓，跳过" >> "$LOGFILE"
    exit 0
fi

# 获取实时行情
STOCKS_DATA=$(python3 -c "
import urllib.request
import json

stocks = \"$POSITIONS\"
url = f'https://qt.gtimg.cn/q={stocks}'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})

result = []
with urllib.request.urlopen(req, timeout=10) as r:
    data = r.read().decode('gbk', errors='replace')

for line in data.strip().split('\n'):
    if '=' not in line:
        continue
    key, rest = line.split('=', 1)
    if '\"' not in rest:
        continue
    fields = rest.strip('\"').split('~')
    if len(fields) < 35:
        continue
    code = key.replace('v_', '')
    name = fields[1]
    price = fields[3]
    pct = fields[32]
    result.append(f'{name}|{price}|{pct}%')

print('\n'.join(result))
" 2>/dev/null)

# 发送飞书提醒消息
MESSAGE="📊 **今日收盘复盘提醒**

老大，收盘了！请做今日复盘分析：

**今日行情预览**：
${STOCKS_DATA}

**请检查**：
1. 持仓股表现
2. 今日交易记录
3. 主力动向分析
4. 明日操作计划

📝 复盘文件：`a_stock_plan/daily/${DATE}/post_market_review.md`"

# 发送飞书消息（通过openclaw网关）
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 发送复盘提醒..." >> "$LOGFILE"
echo "$MESSAGE" >> "$LOGFILE"

# 通知完成
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 复盘提醒已完成" >> "$LOGFILE"
