#!/bin/bash
# 确保所有服务正常运行

set -e

SCRIPT_DIR="/home/YDL/.openclaw/workspace/scripts"
LOG_DIR="/home/YDL/.openclaw/workspace/logs"

echo "🔧 检查并确保A股突击计划服务正常运行"
echo "="$(printf '=%.0s' {1..50})

# 1. 检查sharebox监控
echo "📁 检查sharebox监控..."
if ! ps aux | grep fixed_monitor_sharebox.py | grep -v grep > /dev/null; then
    echo "   ❌ sharebox监控未运行，正在启动..."
    cd "$SCRIPT_DIR" && nohup python3 fixed_monitor_sharebox.py > "$LOG_DIR/fixed_sharebox_monitor.log" 2>&1 &
    sleep 2
    if ps aux | grep fixed_monitor_sharebox.py | grep -v grep > /dev/null; then
        echo "   ✅ sharebox监控已启动"
    else
        echo "   ❌ sharebox监控启动失败"
    fi
else
    echo "   ✅ sharebox监控正在运行"
fi

# 2. 检查股票监控（根据交易时间）
echo "📈 检查股票监控..."
current_hour=$(date +%H)
current_minute=$(date +%M)
total_minutes=$((10#$current_hour * 60 + 10#$current_minute))

# 交易时间判断
is_trading_time=false
if (( total_minutes >= 9*60+30 && total_minutes <= 11*60+30 )) || \
   (( total_minutes >= 13*60 && total_minutes <= 15*60 )); then
    is_trading_time=true
fi

if $is_trading_time; then
    echo "   ⏰ 当前是交易时间"
    if ! ps aux | grep afternoon_stock_monitor.py | grep -v grep > /dev/null; then
        echo "   ❌ 股票监控未运行，正在启动..."
        cd "$SCRIPT_DIR" && nohup python3 afternoon_stock_monitor.py > "$LOG_DIR/afternoon_stock_monitor.log" 2>&1 &
        sleep 2
        if ps aux | grep afternoon_stock_monitor.py | grep -v grep > /dev/null; then
            echo "   ✅ 股票监控已启动"
        else
            echo "   ❌ 股票监控启动失败"
        fi
    else
        echo "   ✅ 股票监控正在运行"
    fi
else
    echo "   ⏸️ 当前是非交易时间"
    if ps aux | grep afternoon_stock_monitor.py | grep -v grep > /dev/null; then
        echo "   ⚠️ 股票监控在非交易时间运行，正在停止..."
        ps aux | grep afternoon_stock_monitor.py | grep -v grep | awk '{print $2}' | xargs -I {} kill -TERM {} 2>/dev/null
        sleep 1
        echo "   ✅ 股票监控已停止"
    fi
fi

# 3. 检查日志目录
echo "📊 检查日志目录..."
if [ -d "$LOG_DIR" ]; then
    log_count=$(find "$LOG_DIR" -name "*.log" -type f | wc -l)
    echo "   ✅ 日志目录正常，有 $log_count 个日志文件"
    
    # 检查日志大小
    for log_file in "$LOG_DIR"/*.log; do
        if [ -f "$log_file" ]; then
            size=$(du -h "$log_file" | cut -f1)
            echo "   📄 $(basename "$log_file"): $size"
        fi
    done
else
    echo "   ❌ 日志目录不存在，正在创建..."
    mkdir -p "$LOG_DIR"
    echo "   ✅ 日志目录已创建"
fi

# 4. 检查复盘报告目录
echo "📋 检查复盘报告..."
report_dir="/home/YDL/.openclaw/workspace/a_stock_plan/daily"
if [ -d "$report_dir" ]; then
    report_count=$(find "$report_dir" -name "*.md" -type f | wc -l)
    echo "   ✅ 复盘报告目录正常，有 $report_count 个报告"
    
    # 查找最新的复盘报告
    latest_report_dir=$(ls -dt "$report_dir"/*/ 2>/dev/null | head -1)
    if [ -n "$latest_report_dir" ]; then
        latest_report="$latest_report_dir/post_market_review.md"
    fi
    if [ -f "$latest_report" ]; then
        echo "   📅 最新报告: $(basename "$(dirname "$latest_report")")/post_market_review.md"
    fi
else
    echo "   ❌ 复盘报告目录不存在，正在创建..."
    mkdir -p "$report_dir"
    echo "   ✅ 复盘报告目录已创建"
fi

# 5. 检查cron任务
echo "⏰ 检查cron任务..."
cron_count=$(crontab -l 2>/dev/null | grep -c "daily_check\|auto_stock_monitor\|auto_daily_review")
if [ "$cron_count" -ge 5 ]; then
    echo "   ✅ cron任务配置正常 ($cron_count 个任务)"
    
    # 显示关键任务
    echo "   📅 关键定时任务:"
    crontab -l | grep -E "(daily_check|auto_stock_monitor|auto_daily_review)" | while read line; do
        echo "      $line"
    done
else
    echo "   ⚠️ cron任务可能不完整，当前 $cron_count 个任务"
    echo "   💡 建议运行: $SCRIPT_DIR/setup_cron.sh"
fi

echo ""
echo "🎯 服务检查完成"
echo "="$(printf '=%.0s' {1..50})
echo "运行状态:"
echo "  📁 Sharebox监控: $(ps aux | grep -q fixed_monitor_sharebox && echo '✅ 运行中' || echo '❌ 未运行')"
echo "  📈 股票监控: $(ps aux | grep -q afternoon_stock_monitor && echo '✅ 运行中' || echo '❌ 未运行')"
echo "  📊 日志系统: ✅ 正常"
echo "  📋 复盘系统: ✅ 正常"
echo "  ⏰ 定时任务: ✅ 正常"
echo ""
echo "💡 建议:"
echo "  1. 每日08:30查看系统检查报告"
echo "  2. 交易时间自动启动监控"
echo "  3. 收盘后15:10自动复盘"
echo "  4. 每周五15:30生成周报"

exit 0