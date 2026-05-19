#!/usr/bin/env python3
"""
定时调度器 - 通过任务队列触发子Agent
- 风控子Agent：交易时间内每30分钟
- 市场分析子Agent：晨报(08:30)、复盘(15:30)

任务写入队列文件，主agent检测到后自动spawn子agent
"""
import os
import time
import json
from datetime import datetime

WORKSPACE = "/home/YDL/.openclaw/workspace"
QUEUE_DIR = f"{WORKSPACE}/claw-communication/inbox"
LOG_FILE = f"{WORKSPACE}/logs/scheduler.log"
MARKER_FILE = f"{WORKSPACE}/logs/scheduler_markers.json"

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

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def get_marker(key):
    try:
        if os.path.exists(MARKER_FILE):
            with open(MARKER_FILE, 'r') as f:
                d = json.load(f)
                return d.get(key, '')
    except:
        pass
    return ''

def set_marker(key, val):
    try:
        d = {}
        if os.path.exists(MARKER_FILE):
            with open(MARKER_FILE, 'r') as f:
                d = json.load(f)
        d[key] = val
        with open(MARKER_FILE, 'w') as f:
            json.dump(d, f, indent=2)
    except:
        pass

def should_run(key, interval_sec):
    last = get_marker(key)
    if not last:
        return True
    try:
        last_ts = datetime.fromisoformat(last).timestamp()
        now_ts = datetime.now().timestamp()
        return (now_ts - last_ts) >= interval_sec
    except:
        return True

def is_trading_day():
    return datetime.now().weekday() < 5

def is_trading_hours():
    now = datetime.now()
    h, m = now.hour, now.minute
    t = h * 60 + m
    return (570 <= t <= 690) or (780 <= t <= 900)  # 9:30-11:30, 13:00-15:00

def queue_task(task_type, task_name, task_desc):
    """写入任务到队列"""
    ensure_dir(QUEUE_DIR)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    task_file = f"{QUEUE_DIR}/task_{task_type}_{ts}.json"
    
    task = {
        "type": task_type,
        "name": task_name,
        "description": task_desc,
        "queued_at": datetime.now().isoformat(),
        "status": "pending"
    }
    
    with open(task_file, 'w') as f:
        json.dump(task, f, indent=2)
    
    log(f"任务入队: {task_type} - {task_name}")
    return True

def process_queue():
    """检查并处理任务队列（主agent调用）"""
    if not os.path.exists(QUEUE_DIR):
        return []
    
    tasks = []
    try:
        for f in os.listdir(QUEUE_DIR):
            if f.startswith('task_') and f.endswith('.json'):
                path = os.path.join(QUEUE_DIR, f)
                try:
                    with open(path, 'r') as fp:
                        task = json.load(fp)
                    if task.get('status') == 'pending':
                        tasks.append(task)
                        # 标记为处理中
                        task['status'] = 'processing'
                        with open(path, 'w') as fp:
                            json.dump(task, fp, indent=2)
                except:
                    pass
    except:
        pass
    return tasks

# ========== 调度逻辑 ==========

def check_and_queue_tasks():
    """检查是否需要入队新任务"""
    if not is_trading_day():
        return
    
    now = datetime.now()
    h, m = now.hour, now.minute
    t = h * 60 + m
    
    # 风控Agent：交易时间内每30分钟
    if is_trading_hours() and should_run('risk_agent', 1800):
        queue_task('risk', '风控检查', '执行持仓风控检查')
        set_marker('risk_agent', now.isoformat())
    
    # 晨报：08:30-08:40窗口
    if 510 <= t <= 520 and should_run('market_morning', 43200):
        queue_task('morning', '晨报生成', '执行每日晨报生成')
        set_marker('market_morning', now.isoformat())
    
    # 复盘：15:30-15:40窗口
    if 930 <= t <= 940 and should_run('market_afternoon', 43200):
        queue_task('afternoon', '复盘生成', '执行每日复盘生成')
        set_marker('market_afternoon', now.isoformat())

def main():
    log("定时调度器启动")
    ensure_dir(QUEUE_DIR)
    ensure_dir(os.path.dirname(LOG_FILE))
    
    while True:
        try:
            check_and_queue_tasks()
        except Exception as e:
            log(f"调度错误: {e}")
        
        time.sleep(60)

if __name__ == "__main__":
    main()