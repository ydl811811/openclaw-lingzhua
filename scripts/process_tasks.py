#!/usr/bin/env python3
"""
定时任务处理器 - 由HEARTBEAT触发
检查任务队列，发现任务时spawn子agent处理

任务队列目录: /home/YDL/.openclaw/workspace/claw-communication/inbox/
任务格式: task_<type>_<timestamp>.json

处理流程:
1. 检查inbox中的task文件
2. 发现pending任务
3. spawn子agent处理
4. 标记任务为processed
"""

import os
import json
from datetime import datetime

WORKSPACE = "/home/YDL/.openclaw/workspace"
QUEUE_DIR = f"{WORKSPACE}/claw-communication/inbox"
LOG_FILE = f"{WORKSPACE}/logs/subagent_trigger.log"

def log(msg):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{now}] [TaskProcessor] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
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
    """标记任务为已处理"""
    try:
        task['status'] = 'processed'
        task['processed_at'] = datetime.now().isoformat()
        with open(task['_file'], 'w') as f:
            json.dump(task, f, indent=2)
        log(f"任务已标记处理: {task['type']}")
    except:
        pass

def spawn_subagent(task):
    """Spawn子Agent处理任务"""
    try:
        from agents import sessions_spawn
        
        task_type = task.get('type', '')
        task_name = task.get('name', '')
        task_desc = task.get('description', '')
        
        # 根据任务类型构建子agent任务描述
        if task_type == 'risk':
            agent_task = f"""# 风控子Agent任务 - {task_name}

执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 任务
{task_desc}

## 执行步骤
1. 读取交易记录台账获取持仓
2. 获取持仓实时价格
3. 检查止损/仓位/异动
4. 生成风控报告

## 输出
将报告追加到 {WORKSPACE}/logs/风控报告_{datetime.now().strftime('%Y-%m-%d')}.md
"""
        elif task_type in ('morning', 'afternoon'):
            agent_task = f"""# 市场分析子Agent任务 - {task_name}

执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 任务
{task_desc}

## 执行步骤
1. 获取大盘数据（指数、北向资金）
2. 检查候选股池触发情况
3. 生成{'晨报' if task_type == 'morning' else '复盘'}报告

## 输出
将报告写入 {WORKSPACE}/a_stock_plan/daily/{datetime.now().strftime('%Y-%m-%d')}/{'晨报' if task_type == 'morning' else '复盘'}_{datetime.now().strftime('%Y-%m-%d')}.md
"""
        else:
            agent_task = f"# 子Agent任务\n\n任务: {task_desc}"
        
        result = sessions_spawn(
            task=agent_task,
            label=f"{task_type}-agent-{datetime.now().strftime('%H%M%S')}",
            mode="run",
            runtime="subagent"
        )
        
        log(f"子Agent已spawn: {task_type} (label: {task_type}-agent-{datetime.now().strftime('%H%M%S')})")
        return True
        
    except ImportError:
        log("无法导入agents模块，尝试通过消息触发")
        # 备用方案：通过发送飞书消息触发主agent
        return False
    except Exception as e:
        log(f"spawn子agent失败: {e}")
        return False

def main():
    log("定时任务处理器启动")
    tasks = get_pending_tasks()
    
    if tasks:
        log(f"发现 {len(tasks)} 个待处理任务")
        for task in tasks:
            success = spawn_subagent(task)
            if success:
                mark_processed(task)
            else:
                # 如果spawn失败，记录但不标记（下次继续处理）
                log(f"任务处理失败，稍后重试: {task.get('type')}")
    else:
        log("暂无待处理任务")

if __name__ == "__main__":
    main()