#!/bin/bash
# 系统重启后自动恢复所有服务

set -e

SCRIPT_DIR="/home/YDL/.openclaw/workspace/scripts"
LOG_DIR="/home/YDL/.openclaw/workspace/logs"
RECOVERY_LOG="$LOG_DIR/system_recovery_$(date +%Y%m%d_%H%M%S).log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RECOVERY_LOG"
}

log_section() {
    echo "" | tee -a "$RECOVERY_LOG"
    echo "=== $1 ===" | tee -a "$RECOVERY_LOG"
    echo "" | tee -a "$RECOVERY_LOG"
}

# 开始恢复
log_section "系统恢复启动"
log "恢复时间: $(date '+%Y-%m-%d %H:%M:%S')"
log "恢复脚本: $0"
log "工作目录: $SCRIPT_DIR"

# 1. 确保目录存在
log_section "1. 检查目录结构"
for dir in "$SCRIPT_DIR" "$LOG_DIR" "/home/YDL/.openclaw/workspace/reports/daily"; do
    if [ ! -d "$dir" ]; then
        log "创建目录: $dir"
        mkdir -p "$dir"
    else
        log "目录已存在: $dir"
    fi
done

# 2. 恢复sharebox监控（必须运行）
log_section "2. 恢复sharebox监控"
if ps aux | grep fixed_monitor_sharebox.py | grep -v grep > /dev/null; then
    log "✅ sharebox监控已在运行"
else
    log "启动sharebox监控..."
    cd "$SCRIPT_DIR" && nohup python3 fixed_monitor_sharebox.py > "$LOG_DIR/fixed_sharebox_monitor_recovery.log" 2>&1 &
    sleep 3
    if ps aux | grep fixed_monitor_sharebox.py | grep -v grep > /dev/null; then
        log "✅ sharebox监控启动成功"
    else
        log "❌ sharebox监控启动失败"
    fi
fi

# 3. 根据交易时间恢复股票监控
log_section "3. 恢复股票监控"
current_hour=$(date +%H)
current_minute=$(date +%M)
total_minutes=$((10#$current_hour * 60 + 10#$current_minute))

# 判断是否交易日
weekday=$(date +%u)  # 1=周一, 7=周日
is_trading_day=false
if (( weekday >= 1 && weekday <= 5 )); then
    is_trading_day=true
fi

# 判断是否交易时间
is_trading_time=false
if $is_trading_day; then
    if (( total_minutes >= 9*60+30 && total_minutes <= 11*60+30 )) || \
       (( total_minutes >= 13*60 && total_minutes <= 15*60 )); then
        is_trading_time=true
    fi
fi

log "当前时间: ${current_hour}:${current_minute}"
log "是否交易日: $is_trading_day"
log "是否交易时间: $is_trading_time"

if $is_trading_time; then
    log "⏰ 当前是交易时间，启动股票监控"
    if ps aux | grep afternoon_stock_monitor.py | grep -v grep > /dev/null; then
        log "✅ 股票监控已在运行"
    else
        log "启动股票监控..."
        cd "$SCRIPT_DIR" && nohup python3 afternoon_stock_monitor.py > "$LOG_DIR/afternoon_stock_monitor_recovery.log" 2>&1 &
        sleep 3
        if ps aux | grep afternoon_stock_monitor.py | grep -v grep > /dev/null; then
            log "✅ 股票监控启动成功"
        else
            log "❌ 股票监控启动失败"
        fi
    fi
else
    log "⏸️ 当前是非交易时间，不启动股票监控"
    # 确保没有残留的监控进程
    if ps aux | grep afternoon_stock_monitor.py | grep -v grep > /dev/null; then
        log "清理残留的股票监控进程..."
        ps aux | grep afternoon_stock_monitor.py | grep -v grep | awk '{print $2}' | xargs -I {} kill -TERM {} 2>/dev/null
        sleep 1
        log "✅ 清理完成"
    fi
fi

# 4. 检查cron任务
log_section "4. 检查定时任务"
cron_count=$(crontab -l 2>/dev/null | grep -c "daily_check\|auto_stock_monitor\|auto_daily_review")
if [ "$cron_count" -ge 5 ]; then
    log "✅ cron任务配置正常 ($cron_count 个任务)"
else
    log "⚠️ cron任务不完整，正在恢复..."
    
    # 恢复cron任务
    (crontab -l 2>/dev/null | grep -v "daily_check\|auto_stock_monitor\|weekly_report\|monthly_report\|auto_daily_review"; \
     echo "30 8 * * 1-5 $SCRIPT_DIR/daily_check.sh"; \
     echo "0 9 * * 1-5 $SCRIPT_DIR/auto_stock_monitor_service.sh start"; \
     echo "0 13 * * 1-5 $SCRIPT_DIR/auto_stock_monitor_service.sh start"; \
     echo "0 15 * * 1-5 $SCRIPT_DIR/auto_stock_monitor_service.sh stop"; \
     echo "10 15 * * 1-5 $SCRIPT_DIR/auto_daily_review.py"; \
     echo "30 15 * * 5 $SCRIPT_DIR/weekly_report.sh") | crontab -
    
    log "✅ cron任务已恢复"
fi

# 5. 运行系统检查
log_section "5. 运行系统检查"
if [ -x "$SCRIPT_DIR/ensure_services_running.sh" ]; then
    log "执行系统检查..."
    "$SCRIPT_DIR/ensure_services_running.sh" >> "$RECOVERY_LOG" 2>&1
    log "✅ 系统检查完成"
else
    log "❌ 系统检查脚本不存在"
fi

# 6. 如果是收盘后，运行复盘
log_section "6. 检查是否需要复盘"
if $is_trading_day && (( total_minutes >= 15*60+10 )) && (( total_minutes <= 16*60 )); then
    log "📊 当前是收盘后复盘时间，运行自动复盘"
    if [ -f "$SCRIPT_DIR/auto_daily_review.py" ]; then
        cd "$SCRIPT_DIR" && python3 auto_daily_review.py >> "$RECOVERY_LOG" 2>&1
        log "✅ 自动复盘完成"
    else
        log "❌ 自动复盘脚本不存在"
    fi
else
    log "⏰ 当前不是复盘时间"
fi

# 7. 生成恢复报告
log_section "7. 恢复完成报告"
log "恢复完成时间: $(date '+%Y-%m-%d %H:%M:%S')"
log "恢复日志: $RECOVERY_LOG"

# 显示关键进程状态
log "关键进程状态:"
log "  📁 Sharebox监控: $(ps aux | grep -q fixed_monitor_sharebox && echo '✅ 运行中' || echo '❌ 未运行')"
log "  📈 股票监控: $(ps aux | grep -q afternoon_stock_monitor && echo '✅ 运行中' || echo '❌ 未运行')"
log "  ⏰ 定时任务: $(crontab -l 2>/dev/null | grep -q 'auto_daily_review' && echo '✅ 已配置' || echo '❌ 未配置')"

log ""
log "🎯 系统恢复完成"
log "💡 建议: 每日08:30查看系统检查报告，确保所有服务正常运行"

exit 0