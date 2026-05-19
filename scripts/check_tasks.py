#!/usr/bin/env python3
"""
HEARTBEAT触发脚本 - 检查任务队列并spawn子agent
每5分钟由HEARTBEAT.md自动触发
"""
import os
import sys
import json
import traceback

WORKSPACE = "/home/YDL/.openclaw/workspace"
QUEUE_DIR = f"{WORKSPACE}/claw-communication/inbox"
LOG_FILE = f"{WORKSPACE}/logs/subagent_trigger.log"

def log(msg):
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(f"[{__import__('datetime').datetime.now().strftime('%H:%M:%S')}] [HEARTBEAT] {msg}\n")
    except:
        pass

def get_pending_tasks():
    """获取所有待处理任务"""
    tasks = []
    if not os.path.exists(QUEUE_DIR):
        return tasks
    try:
        for f in os.listdir(QUEUE_DIR):
            if f.startswith('task_') and f.endswith('.json'):
                path = os.path.join(QUEUE_DIR, f)
                try:
                    with open(path, 'r') as fp:
                        task = json.load(fp)
                    if task.get('status') == 'pending':
                        task['_file'] = path
                        tasks.append(task)
                except:
                    pass
    except:
        pass
    return tasks

def mark_processed(task):
    try:
        task['status'] = 'processed'
        task['processed_at'] = __import__('datetime').datetime.now().isoformat()
        with open(task['_file'], 'w') as f:
            json.dump(task, f, indent=2)
    except:
        pass

def check_and_trigger_tasks():
    """检查任务队列并触发子agent"""
    tasks = get_pending_tasks()
    log(f"检查任务队列，发现 {len(tasks)} 个待处理任务")
    
    for task in tasks:
        try:
            # 尝试导入agents模块spawn子agent
            from agents import sessions_spawn
            
            task_type = task.get('type', '')
            now = __import__('datetime').datetime.now()
            
            if task_type == 'risk':
                from datetime import datetime
                agent_task = f"""# 风控子Agent任务

执行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}

## 任务
执行持仓风控检查：读取交易记录台账，获取实时价格，检查止损/仓位/异动，生成风控报告

## 执行步骤
1. 读取 {WORKSPACE}/a_stock_plan/交易记录台账.md 获取持仓
2. 获取持仓实时价格（腾讯接口 qt.gtimg.cn）
3. 检查止损/仓位/异动
4. 生成风控报告
5. 如有重大风险，发送飞书通知

## 输出
将报告写入 {WORKSPACE}/logs/风控报告_{now.strftime('%Y-%m-%d')}.md
"""
                label = f"risk-agent-{now.strftime('%H%M%S')}"
                
            elif task_type in ('morning', 'afternoon'):
                from datetime import datetime
                report_type = '晨报' if task_type == 'morning' else '复盘'
                agent_task = f"""# 市场分析子Agent任务 - {report_type}

执行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}

## 任务
执行每日{report_type}：获取大盘数据，检查候选股池触发情况，生成{report_type}报告

## 执行步骤
1. 获取大盘数据（指数、北向资金）
2. 检查候选股池触发情况（{WORKSPACE}/scripts/候选股买入信号监控.py）
3. 生成{report_type}报告
4. 发送飞书通知

## 输出
将报告写入 {WORKSPACE}/a_stock_plan/daily/{now.strftime('%Y-%m-%d')}/{report_type}_{now.strftime('%Y-%m-%d')}.md
"""
                label = f"market-{task_type}-{now.strftime('%H%M%S')}"
            else:
                continue
            
            result = sessions_spawn(
                task=agent_task,
                label=label,
                mode="run",
                runtime="subagent"
            )
            
            log(f"子Agent已spawn: {label}")
            mark_processed(task)
            
        except ImportError:
            log("agents模块不可用，跳过spawn")
            break
        except Exception as e:
            log(f"spawn失败: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    try:
        check_and_trigger_tasks()
    except Exception as e:
        log(f"任务触发异常: {e}")
        traceback.print_exc()