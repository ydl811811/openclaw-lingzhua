#!/usr/bin/env python3
"""
定时触发子Agent的脚本
- 风控子Agent：交易时间内每30分钟自动触发
- 市场分析子Agent：晨报(08:30)、复盘(15:30)
- 通过openclaw agent发送消息到飞书，触发主agent处理

使用方法:
  python3 trigger_subagent.py risk  # 触发风控子Agent
  python3 trigger_subagent.py morning  # 触发晨报子Agent
"""
import sys
import os
import subprocess
from datetime import datetime

WORKSPACE = "/home/YDL/.openclaw/workspace"
LOG_FILE = f"{WORKSPACE}/logs/subagent_trigger.log"

RISK_MSG = """执行持仓风控检查：读取交易记录台账，获取实时价格，检查止损/仓位/异动，生成报告"""

MARKET_MORNING_MSG = """执行每日晨报：获取大盘数据（指数、北向资金），检查候选股池触发情况，生成晨报报告"""

MARKET_AFTERNOON_MSG = """执行每日复盘：获取今日大盘数据，检查持仓状态，生成复盘报告"""

def log(msg):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{now}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except:
        pass

def trigger_agent(task_msg, task_name):
    log(f"触发{task_name}...")
    
    try:
        # 使用openclaw agent发送消息到飞书
        # openclaw agent --channel feishu 会触发主agent处理
        cmd = [
            'openclaw', 'agent',
            '--channel', 'feishu',
            '--message', task_msg,
            '--deliver'
        ]
        
        result = subprocess.run(
            cmd,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            log(f"{task_name}触发成功")
            return True
        else:
            log(f"{task_name}触发失败: {result.stderr[:200]}")
            return False
            
    except subprocess.TimeoutExpired:
        log(f"{task_name}触发超时")
        return False
    except Exception as e:
        log(f"{task_name}异常: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 trigger_subagent.py <risk|morning|afternoon>")
        sys.exit(1)
    
    mode = sys.argv[1]
    
    if mode == "risk":
        trigger_agent(RISK_MSG, "风控子Agent")
    elif mode == "morning":
        trigger_agent(MARKET_MORNING_MSG, "晨报子Agent")
    elif mode == "afternoon":
        trigger_agent(MARKET_AFTERNOON_MSG, "复盘子Agent")
    else:
        print(f"未知模式: {mode}")
        sys.exit(1)