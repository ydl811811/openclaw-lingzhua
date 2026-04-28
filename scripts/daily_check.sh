#!/bin/bash
# A股突击计划每日检查脚本
# 每天早上8:30自动运行，检查系统状态

set -e

# 配置
SCRIPT_DIR="/home/YDL/.openclaw/workspace/scripts"
LOG_DIR="/home/YDL/.openclaw/workspace/logs"
CHECK_LOG="$LOG_DIR/daily_check_$(date +%Y%m%d).log"

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "[$(date '+%H:%M:%S')] $1" | tee -a "$CHECK_LOG"
}

log_section() {
    echo -e "\n${BLUE}=== $1 ===${NC}" | tee -a "$CHECK_LOG"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}" | tee -a "$CHECK_LOG"
}

log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}" | tee -a "$CHECK_LOG"
}

log_error() {
    echo -e "${RED}❌ $1${NC}" | tee -a "$CHECK_LOG"
}

# 开始检查
log_section "A股突击计划每日系统检查"
log "检查时间: $(date '+%Y-%m-%d %H:%M:%S')"
log "检查人: 灵爪（小妹）"

# 1. 检查股票监控脚本状态
log_section "1. 股票监控脚本状态"
if ps aux | grep afternoon_stock_monitor.py | grep -v grep > /dev/null; then
    log_success "股票监控脚本正在运行"
    ps aux | grep afternoon_stock_monitor.py | grep -v grep | head -1 | tee -a "$CHECK_LOG"
else
    log_error "股票监控脚本未运行"
    log_warning "建议: 立即启动监控脚本"
fi

# 2. 检查sharebox监控脚本状态
log_section "2. Sharebox监控脚本状态"
if ps aux | grep fixed_monitor_sharebox.py | grep -v grep > /dev/null; then
    log_success "Sharebox监控脚本正在运行"
    ps aux | grep fixed_monitor_sharebox.py | grep -v grep | head -1 | tee -a "$CHECK_LOG"
else
    log_error "Sharebox监控脚本未运行"
    log_warning "建议: 立即启动sharebox监控"
fi

# 3. 检查日志文件
log_section "3. 日志文件检查"
log_files=(
    "$LOG_DIR/afternoon_stock_monitor.log"
    "$LOG_DIR/fixed_sharebox_monitor.log"
)

for log_file in "${log_files[@]}"; do
    if [ -f "$log_file" ]; then
        size=$(du -h "$log_file" | cut -f1)
        lines=$(wc -l < "$log_file" 2>/dev/null || echo "0")
        log_success "$(basename "$log_file"): $size, $lines 行"
        
        # 检查错误
        if tail -10 "$log_file" | grep -i "error\|fail\|❌" > /dev/null; then
            log_warning "发现错误日志"
            tail -5 "$log_file" | grep -i "error\|fail\|❌" | tee -a "$CHECK_LOG"
        fi
    else
        log_warning "$(basename "$log_file"): 文件不存在"
    fi
done

# 4. 检查通信目录
log_section "4. 通信目录检查"
sharebox_dir="/home/YDL/.openclaw/workspace/claw-communication/sharebox"
if [ -d "$sharebox_dir" ]; then
    file_count=$(find "$sharebox_dir" -type f | wc -l)
    latest_file=$(ls -t "$sharebox_dir" | head -1 2>/dev/null || echo "无")
    log_success "Sharebox目录: $file_count 个文件"
    log "最新文件: $latest_file"
    
    # 检查龙爪最新消息
    longzhao_files=$(find "$sharebox_dir" -name "*龙爪*" -type f | wc -l)
    if [ "$longzhao_files" -gt 0 ]; then
        latest_longzhao=$(ls -t "$sharebox_dir"/*龙爪* 2>/dev/null | head -1 2>/dev/null || echo "无")
        log_success "龙爪文件: $longzhao_files 个"
        log "最新龙爪文件: $(basename "$latest_longzhao" 2>/dev/null || echo "无")"
    else
        log_warning "未找到龙爪文件"
    fi
else
    log_error "Sharebox目录不存在"
fi

# 5. 检查网络连接
log_section "5. 网络连接检查"
# 检查腾讯财经接口
if curl -s --connect-timeout 5 "http://qt.gtimg.cn/q=sz000001" > /dev/null; then
    log_success "腾讯财经接口连接正常"
else
    log_error "腾讯财经接口连接失败"
fi

# 6. 检查磁盘空间
log_section "6. 系统资源检查"
df -h /home | tail -1 | tee -a "$CHECK_LOG"

# 7. 生成今日计划
log_section "7. 今日执行计划"
echo "🕗 08:30 - 系统检查（本脚本）" | tee -a "$CHECK_LOG"
echo "🕘 09:00 - 开盘前最终确认" | tee -a "$CHECK_LOG"
echo "🕘 09:15 - 竞价监控开始" | tee -a "$CHECK_LOG"
echo "🕘 09:30 - 交易执行开始" | tee -a "$CHECK_LOG"
echo "🕐 13:00 - 下午开盘监控" | tee -a "$CHECK_LOG"
echo "🕒 15:00 - 收盘后分析" | tee -a "$CHECK_LOG"

# 8. 检查A股突击计划状态
log_section "8. A股突击计划状态"
plan_file="/home/YDL/.openclaw/workspace/claw-communication/sharebox/灵爪_龙爪_老大_A股突击计划启动确认_20260409_0830.md"
if [ -f "$plan_file" ]; then
    log_success "A股突击计划文件存在"
    # 提取关键信息
    grep -E "(本金|目标|周期|状态)" "$plan_file" | head -5 | tee -a "$CHECK_LOG"
else
    log_warning "A股突击计划文件不存在"
fi

# 总结
log_section "检查完成"
log "总检查项目: 8项"
log "检查日志: $CHECK_LOG"
log "下次检查: 明天 08:30"

# 如果有错误，发送提醒
if grep -q "❌" "$CHECK_LOG"; then
    error_count=$(grep -c "❌" "$CHECK_LOG")
    log_warning "发现 $error_count 个错误，需要立即处理！"
    
    # 这里可以添加飞书推送
    echo "@老大 ⚠️ 系统检查发现 $error_count 个问题，请查看日志: $CHECK_LOG" | tee -a "$CHECK_LOG"
else
    log_success "所有检查项目正常，系统就绪！"
    echo "@老大 ✅ 系统检查完成，所有项目正常，可以开始今日交易" | tee -a "$CHECK_LOG"
fi

exit 0