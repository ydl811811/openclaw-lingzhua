#!/bin/bash
# 7天缓存自动清理
find /home/YDL/.openclaw/agent_stock_work/cache_temp/ -mtime +7 -delete 2>/dev/null
echo "[$(date '+%Y-%m-%d %H:%M')] 缓存清理完成" >> /home/YDL/.openclaw/workspace/logs/cache_cleanup.log
