#!/bin/bash
# 启动持续监控脚本
PID_FILE="/tmp/auto_stock_monitor.pid"
LOG_FILE="/home/YDL/.openclaw/workspace/logs/stock_monitor.log"

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "监控已在运行 (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# 启动监控
cd /home/YDL/.openclaw/workspace/scripts
nohup python3 auto_stock_alert.py >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PID_FILE"
echo "监控已启动 (PID: $NEW_PID)"
echo "日志: $LOG_FILE"
