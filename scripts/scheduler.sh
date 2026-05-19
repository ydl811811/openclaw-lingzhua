#!/bin/bash
# 定时调度器 - 直接发送消息触发主agent
# 风控Agent: 每30分钟（交易时间内）
# 市场分析Agent: 每天 08:30(晨报) / 15:30(复盘)

WORKSPACE="/home/YDL/.openclaw/workspace"
LOG_FILE="$WORKSPACE/logs/scheduler.log"
PID_FILE="/tmp/scheduler.pid"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

mkdir -p "$(dirname $LOG_FILE)"

if [ -f "$PID_FILE" ]; then
    old_pid=$(cat "$PID_FILE")
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        log "调度器已在运行 (PID $old_pid)"
        exit 0
    fi
fi
echo $$ > "$PID_FILE"

MARKER_FILE="$WORKSPACE/logs/scheduler_markers.json"

get_marker() {
    local key=$1
    if [ -f "$MARKER_FILE" ]; then
        python3 -c "import json; d=json.load(open('$MARKER_FILE')); print(d.get('$key',''))" 2>/dev/null
    fi
}

set_marker() {
    local key=$1
    local val=$2
    python3 -c "
import json, os
d={}
try:
    if os.path.exists('$MARKER_FILE'):
        d=json.load(open('$MARKER_FILE'))
except: pass
d['$key']='$val'
json.dump(d, open('$MARKER_FILE','w'))
" 2>/dev/null
}

should_run() {
    local key=$1
    local interval=$2
    local now_ts=$(date +%s)
    local last=$(get_marker "$key")
    if [ -z "$last" ]; then
        return 0
    fi
    local last_ts=$(date -d "$last" +%s 2>/dev/null)
    if [ -z "$last_ts" ]; then
        return 0
    fi
    local diff=$((now_ts - last_ts))
    [ $diff -ge $interval ]
}

is_trading_day() {
    local day=$(date +%u)
    [ "$day" -lt 6 ]
}

is_trading_hours() {
    local now=$(date +%H%M)
    if [ "$now" -ge 0930 ] && [ "$now" -le 1130 ]; then
        return 0
    fi
    if [ "$now" -ge 1300 ] && [ "$now" -le 1500 ]; then
        return 0
    fi
    return 1
}

trigger_agent() {
    local msg="$1"
    local name="$2"
    log "触发${name}..."
    cd "$WORKSPACE"
    openclaw agent --session-id main --message "$msg" > /dev/null 2>&1 &
    log "${name}已触发"
}

log "定时调度器启动 - PID $$"

while true; do
    if is_trading_day; then
        if is_trading_hours && should_run "risk_agent" 1800; then
            trigger_agent "执行持仓风控检查：读取交易记录台账，获取实时价格，检查止损/仓位/异动，生成风控报告发送飞书" "风控检查"
            set_marker "risk_agent" "$(date -Iseconds)"
        fi
        
        local now=$(date +%H%M)
        if [ "$now" -ge 0830 ] && [ "$now" -lt 0840 ] && should_run "market_morning" 43200; then
            trigger_agent "执行每日晨报：获取大盘数据（指数、北向资金），检查候选股池触发情况，生成晨报报告发送飞书" "晨报"
            set_marker "market_morning" "$(date -Iseconds)"
        fi
        
        if [ "$now" -ge 1530 ] && [ "$now" -lt 1540 ] && should_run "market_afternoon" 43200; then
            trigger_agent "执行每日复盘：获取今日大盘数据，检查持仓状态，生成复盘报告发送飞书" "复盘"
            set_marker "market_afternoon" "$(date -Iseconds)"
        fi
    fi
    
    sleep 60
done
