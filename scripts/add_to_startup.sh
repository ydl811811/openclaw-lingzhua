#!/bin/bash
# 将A股突击计划服务添加到系统启动

set -e

SCRIPT_DIR="/home/YDL/.openclaw/workspace/scripts"
SERVICE_NAME="stock-plan-recovery"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "🎯 将A股突击计划添加到系统启动"
echo "="$(printf '=%.0s' {1..50})

# 检查是否root
if [ "$EUID" -ne 0 ]; then
    echo "❌ 需要root权限，请使用sudo运行"
    echo "   sudo $0"
    exit 1
fi

# 创建systemd服务文件
echo "创建systemd服务文件: $SERVICE_FILE"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=A股突击计划系统恢复服务
After=network.target
Wants=network.target

[Service]
Type=simple
User=YDL
Group=Users
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/system_recovery.sh
Restart=no
StandardOutput=journal
StandardError=journal
SyslogIdentifier=stock-plan-recovery

# 安全设置
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

echo "✅ 服务文件创建完成"

# 重新加载systemd
echo "重新加载systemd配置..."
systemctl daemon-reload

# 启用服务
echo "启用服务..."
systemctl enable $SERVICE_NAME

# 启动服务
echo "启动服务..."
systemctl start $SERVICE_NAME

# 检查状态
echo "检查服务状态..."
systemctl status $SERVICE_NAME --no-pager

echo ""
echo "🎯 系统启动恢复服务已配置完成"
echo "="$(printf '=%.0s' {1..50})
echo "服务名称: $SERVICE_NAME"
echo "服务文件: $SERVICE_FILE"
echo "启动脚本: $SCRIPT_DIR/system_recovery.sh"
echo ""
echo "💡 管理命令:"
echo "  查看状态: sudo systemctl status $SERVICE_NAME"
echo "  查看日志: sudo journalctl -u $SERVICE_NAME"
echo "  重启服务: sudo systemctl restart $SERVICE_NAME"
echo "  停止服务: sudo systemctl stop $SERVICE_NAME"
echo "  禁用服务: sudo systemctl disable $SERVICE_NAME"
echo ""
echo "📋 服务功能:"
echo "  1. 系统重启后自动恢复sharebox监控"
echo "  2. 根据交易时间自动恢复股票监控"
echo "  3. 检查并恢复cron定时任务"
echo "  4. 运行系统健康检查"
echo "  5. 收盘后自动运行复盘"

exit 0