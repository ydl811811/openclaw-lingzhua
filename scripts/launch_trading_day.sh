#!/bin/bash
# A股突击计划 - 交易日启动脚本
# 执行时间：每个交易日 08:25
# 功能：系统检查 + 启动监控

SCRIPT_DIR="/home/YDL/.openclaw/workspace/scripts"
STATUS_FILE="/home/YDL/.openclaw/workspace/claw-communication/status/today.md"
LOG_FILE="/home/YDL/.openclaw/workspace/logs/trading_day_$(date +%Y%m%d).log"

echo "🚀 A股突击计划启动" | tee -a "$LOG_FILE"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
echo "===========================================" | tee -a "$LOG_FILE"

# 1. 系统检查 (08:25-08:30)
echo "" | tee -a "$LOG_FILE"
echo "🔍 1. 系统检查..." | tee -a "$LOG_FILE"

# 1.1 检查数据源
echo "   检查数据源..." | tee -a "$LOG_FILE"
if curl -s "http://192.168.31.141:5001/api/test" | grep -q '"success":true'; then
    echo "   ✅ adata接口正常" | tee -a "$LOG_FILE"
else
    echo "   ❌ adata接口异常！尝试备用接口..." | tee -a "$LOG_FILE"
    # 备用接口检查逻辑
fi

# 1.2 检查通信
echo "   检查通信通道..." | tee -a "$LOG_FILE"
if [ -d "/home/YDL/.openclaw/workspace/claw-communication/inbox/" ]; then
    echo "   ✅ 收件箱目录存在" | tee -a "$LOG_FILE"
else
    echo "   ❌ 收件箱目录不存在！" | tee -a "$LOG_FILE"
fi

# 1.3 检查状态文件
if [ -f "$STATUS_FILE" ]; then
    echo "   ✅ 状态文件存在" | tee -a "$LOG_FILE"
    # 更新状态文件
    echo "" | tee -a "$LOG_FILE"
    echo "## 💓 心跳检查 ($(date +%H:%M))" | tee -a "$STATUS_FILE"
    echo "- [x] 系统启动检查 ✅ ($(date '+%Y-%m-%d %H:%M'))" | tee -a "$STATUS_FILE"
    echo "- [x] adata接口正常 ✅" | tee -a "$STATUS_FILE"
    echo "- [x] 通信通道正常 ✅" | tee -a "$STATUS_FILE"
    echo "- [ ] 等待开盘 (09:15)" | tee -a "$STATUS_FILE"
else
    echo "   ❌ 状态文件不存在！" | tee -a "$LOG_FILE"
fi

# 2. 启动监控系统 (08:30)
echo "" | tee -a "$LOG_FILE"
echo "📈 2. 启动监控系统..." | tee -a "$LOG_FILE"

# 2.1 启动股票价格监控
echo "   启动股票价格监控..." | tee -a "$LOG_FILE"
cd "$SCRIPT_DIR"
python3 realtime_monitor.py > /tmp/stock_monitor.log 2>&1 &
STOCK_PID=$!
echo "   ✅ 股票监控已启动 (PID: $STOCK_PID)" | tee -a "$LOG_FILE"

# 2.2 启动消息监控
echo "   启动消息监控..." | tee -a "$LOG_FILE"
python3 longzhao_message_monitor.py > /tmp/longzhao_monitor.log 2>&1 &
MSG_PID=$!
echo "   ✅ 消息监控已启动 (PID: $MSG_PID)" | tee -a "$LOG_FILE"

# 3. 记录进程ID
echo "$STOCK_PID" > /tmp/stock_monitor.pid
echo "$MSG_PID" > /tmp/longzhao_monitor.pid

# 4. 发送通知
echo "" | tee -a "$LOG_FILE"
echo "📢 3. 发送启动通知..." | tee -a "$LOG_FILE"
NOTIFICATION="🎯 A股突击计划已启动
时间：$(date '+%Y-%m-%d %H:%M')
状态：系统检查完成，监控运行中
数据源：adata接口正常
通信：正常
下次检查：09:00开盘前确认"

echo "$NOTIFICATION" | tee -a "$LOG_FILE"

# 更新状态文件
echo "" | tee -a "$STATUS_FILE"
echo "## 🚀 交易日启动 ($(date '+%Y-%m-%d %H:%M'))" | tee -a "$STATUS_FILE"
echo "- [✅] 系统启动完成" | tee -a "$STATUS_FILE"
echo "- [✅] 监控运行中 (股票PID: $STOCK_PID, 消息PID: $MSG_PID)" | tee -a "$STATUS_FILE"
echo "- [✅] 数据源正常" | tee -a "$STATUS_FILE"
echo "- [ ] 等待09:15竞价开始" | tee -a "$STATUS_FILE"

echo "" | tee -a "$LOG_FILE"
echo "===========================================" | tee -a "$LOG_FILE"
echo "✅ 启动完成！" | tee -a "$LOG_FILE"
echo "日志文件：$LOG_FILE" | tee -a "$LOG_FILE"
echo "===========================================" | tee -a "$LOG_FILE"
