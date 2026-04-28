#!/usr/bin/env python3
"""
收件箱文件监控脚本
监控二哥发送的文件，及时检测新消息
"""

import os
import time
import json
from datetime import datetime
from pathlib import Path
import hashlib

class InboxMonitor:
    """收件箱文件监控"""
    
    def __init__(self):
        # 监控路径配置（根据二哥的统一路径方案）
        # 主沟通室：claw-communication/inbox/ 是收件箱（新消息）
        self.monitor_paths = [
            "/home/YDL/.openclaw/workspace/claw-communication/inbox/"  # 主收件箱
        ]
        
        # 文件状态记录
        self.file_states = {}  # path -> (mtime, size, hash)
        
        # 监控配置
        self.check_interval = 10  # 检查间隔（秒）
        self.max_files_to_keep = 100  # 最大记录文件数
        
        print(f"📁 收件箱文件监控启动")
        print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   监控路径: {len(self.monitor_paths)}个")
        for path in self.monitor_paths:
            print(f"     - {path}")
        print(f"   检查间隔: {self.check_interval}秒")
    
    def calculate_file_hash(self, filepath):
        """计算文件哈希值（用于检测内容变化）"""
        try:
            with open(filepath, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception as e:
            print(f"❌ 计算文件哈希失败 {filepath}: {e}")
            return None
    
    def scan_directory(self, directory):
        """扫描目录，返回文件列表"""
        files = []
        try:
            for root, dirs, filenames in os.walk(directory):
                for filename in filenames:
                    filepath = os.path.join(root, filename)
                    # 只监控特定类型的文件
                    if filename.endswith(('.md', '.txt', '.json')):
                        files.append(filepath)
        except Exception as e:
            print(f"❌ 扫描目录失败 {directory}: {e}")
        
        return files
    
    def check_file_changes(self):
        """检查文件变化"""
        changes = {
            'new_files': [],
            'modified_files': [],
            'deleted_files': []
        }
        
        # 扫描所有监控路径
        all_files = []
        for path in self.monitor_paths:
            if os.path.exists(path):
                all_files.extend(self.scan_directory(path))
        
        # 检查新文件和修改的文件
        current_files = set()
        for filepath in all_files:
            current_files.add(filepath)
            
            try:
                stat = os.stat(filepath)
                mtime = stat.st_mtime
                size = stat.st_size
                
                if filepath not in self.file_states:
                    # 新文件
                    file_hash = self.calculate_file_hash(filepath)
                    self.file_states[filepath] = (mtime, size, file_hash)
                    changes['new_files'].append({
                        'path': filepath,
                        'mtime': mtime,
                        'size': size,
                        'hash': file_hash
                    })
                else:
                    # 检查是否修改
                    old_mtime, old_size, old_hash = self.file_states[filepath]
                    if mtime != old_mtime or size != old_size:
                        # 文件可能已修改，检查哈希
                        new_hash = self.calculate_file_hash(filepath)
                        if new_hash != old_hash:
                            self.file_states[filepath] = (mtime, size, new_hash)
                            changes['modified_files'].append({
                                'path': filepath,
                                'mtime': mtime,
                                'size': size,
                                'hash': new_hash,
                                'old_mtime': old_mtime,
                                'old_size': old_size
                            })
            except Exception as e:
                print(f"❌ 检查文件失败 {filepath}: {e}")
        
        # 检查删除的文件
        deleted_files = set(self.file_states.keys()) - current_files
        for filepath in deleted_files:
            changes['deleted_files'].append(filepath)
            del self.file_states[filepath]
        
        return changes
    
    def process_changes(self, changes):
        """处理文件变化"""
        if not any(changes.values()):
            return
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 新文件处理
        for file_info in changes['new_files']:
            filepath = file_info['path']
            filename = os.path.basename(filepath)
            
            print(f"📥 检测到新文件 [{timestamp}]: {filename}")
            
            # 判断文件类型并处理
            if '龙爪' in filename or '二哥' in filename or 'longzhao' in filename:
                print(f"   ⚡ 二哥的新消息！路径: {filepath}")
                print(f"   📊 大小: {file_info['size']} bytes")
                
                # 尝试读取文件内容（前几行）
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read(500)  # 读取前500字符
                        print(f"   📄 内容预览: {content[:200]}...")
                except:
                    pass
            
            elif '灵爪' in filename or 'lingzhao' in filename:
                print(f"   📝 我的文件: {filename}")
            
            else:
                print(f"   📁 其他文件: {filename}")
        
        # 修改文件处理
        for file_info in changes['modified_files']:
            filepath = file_info['path']
            filename = os.path.basename(filepath)
            print(f"📝 文件已修改 [{timestamp}]: {filename}")
        
        # 删除文件处理
        for filepath in changes['deleted_files']:
            filename = os.path.basename(filepath)
            print(f"🗑️ 文件已删除 [{timestamp}]: {filename}")
    
    def run(self):
        """运行监控"""
        print("🚀 开始监控收件箱...")
        
        try:
            while True:
                changes = self.check_file_changes()
                self.process_changes(changes)
                
                # 定期清理状态记录（避免内存泄漏）
                if len(self.file_states) > self.max_files_to_keep:
                    # 保留最近的文件
                    sorted_files = sorted(
                        self.file_states.items(),
                        key=lambda x: x[1][0],  # 按修改时间排序
                        reverse=True
                    )
                    self.file_states = dict(sorted_files[:self.max_files_to_keep])
                
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            print("\n🛑 监控已停止")
        except Exception as e:
            print(f"❌ 监控异常: {e}")

def main():
    """主函数"""
    monitor = InboxMonitor()
    
    # 先扫描一次，初始化状态
    print("🔍 初始化扫描...")
    changes = monitor.check_file_changes()
    if changes['new_files']:
        print(f"📁 发现 {len(changes['new_files'])} 个现有文件")
        for file_info in changes['new_files']:
            filename = os.path.basename(file_info['path'])
            print(f"   - {filename}")
    
    # 开始监控
    monitor.run()

if __name__ == "__main__":
    main()