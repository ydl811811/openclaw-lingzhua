#!/bin/bash
# 检查监控脚本是否运行，没运行则重启

PROC_NAME="auto_stock_alert.py"
LOG_FILE="/home/YDL/.openclaw/workspace/logs/stock_monitor.log"
SCRIPT_PATH="/home/YDL/.openclaw/workspace/scripts/auto_stock_alert.py"

# 检查进程是否存在
if ! pgrep -f "$PROC_NAME" > /dev/null; then
    echo "[$(date)] ⚠️ 监控脚本已停止，正在重启..." >> "$LOG_FILE"
    nohup python3 "$SCRIPT_PATH" >> "$LOG_FILE" 2>&1 &
    echo "[$(date)] ✅ 监控脚本已重启" >> "$LOG_FILE"
else
    echo "[$(date)] ✅ 监控进程正常" >> "$LOG_FILE"
fi
