#!/bin/bash
# Gateway Watchdog for 灵爪 (老三) - 简化版
# 功能：监控网关状态，异常时重启并备份配置
# 老三作为配置基准源，不需要从外部同步配置

set -e

WORKSPACE="/home/YDL/.openclaw/workspace"
LOG_DIR="$WORKSPACE/logs"
BACKUP_DIR="$WORKSPACE/backups"
CONFIG_FILE="/home/YDL/.openclaw/openclaw.json"
STATE_FILE="$LOG_DIR/watchdog-state.json"

# 创建必要目录
mkdir -p "$LOG_DIR" "$BACKUP_DIR"

# 日志函数
log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $1" >> "$LOG_DIR/watchdog-lingzhao.log"
    echo "[$timestamp] $1"
}

# 配置验证函数
validate_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        log "❌ 配置文件不存在: $CONFIG_FILE"
        return 1
    fi
    
    if python3 -m json.tool "$CONFIG_FILE" > /dev/null 2>&1; then
        log "✅ 配置文件格式验证通过"
        return 0
    else
        log "❌ 配置文件格式错误"
        return 1
    fi
}

# 备份配置（仅在验证通过后）
backup_config() {
    if validate_config; then
        local backup_file="$BACKUP_DIR/openclaw.json.$(date '+%Y%m%d_%H%M%S')"
        cp "$CONFIG_FILE" "$backup_file"
        log "✅ 配置已备份：$backup_file"
        
        # 清理旧备份（保留最近 5 个）
        ls -t "$BACKUP_DIR"/openclaw.json.* 2>/dev/null | tail -n +6 | xargs -r rm -f
        return 0
    else
        log "⚠️  配置验证失败，跳过备份"
        return 1
    fi
}

# 重启网关
restart_gateway() {
    log "尝试重启网关..."
    
    # 备份当前配置
    backup_config
    
    # 停止现有网关
    openclaw gateway stop 2>/dev/null || true
    sleep 2
    
    # 启动网关
    if openclaw gateway start; then
        log "✅ 网关启动成功"
        return 0
    else
        log "❌ 网关启动失败"
        
        # 尝试从备份恢复
        local last_backup=$(ls -t "$BACKUP_DIR"/openclaw.json.* 2>/dev/null | head -1)
        if [ -n "$last_backup" ] && [ -f "$last_backup" ]; then
            log "🔄 从备份恢复配置：$(basename $last_backup)"
            cp "$last_backup" "$CONFIG_FILE"
            
            if openclaw gateway start; then
                log "✅ 从备份恢复后启动成功"
                return 0
            fi
        fi
        
        log "❌ 所有恢复尝试均失败，需要人工干预"
        return 1
    fi
}

# 主循环
RESTART_COUNT=0
MAX_RESTARTS=3

log "🐉 灵爪看门狗启动"
log "工作目录: $WORKSPACE"
log "配置文件: $CONFIG_FILE"

# 初始配置验证和备份
if validate_config; then
    log "✅ 初始配置验证通过"
    backup_config
else
    log "🚨 初始配置验证失败，尝试重启..."
    if ! restart_gateway; then
        log "🚨 初始启动失败，看门狗退出"
        exit 1
    fi
fi

while true; do
    # 检查网关是否在运行
    if ! openclaw gateway status &>/dev/null; then
        log "⚠️  网关未运行，检测到异常"
        
        if [ $RESTART_COUNT -ge $MAX_RESTARTS ]; then
            log "❌ 达到最大重启次数 ($MAX_RESTARTS)，暂停自动恢复"
            log "🚨 需要人工干预！"
            sleep 300
            RESTART_COUNT=0
            continue
        fi
        
        if restart_gateway; then
            RESTART_COUNT=0
        else
            RESTART_COUNT=$((RESTART_COUNT + 1))
            log "重启失败，失败次数：$RESTART_COUNT"
        fi
    else
        if [ $RESTART_COUNT -gt 0 ]; then
            log "✅ 网关正常运行，重置重启计数器"
            RESTART_COUNT=0
        fi
        
        # 每3小时备份一次配置
        local current_hour=$(date +%H)
        local backup_hours="00 03 06 09 12 15 18 21"
        if [[ " $backup_hours " == *" $current_hour "* ]]; then
            if validate_config; then
                backup_config
            fi
        fi
    fi
    
    sleep 60
done