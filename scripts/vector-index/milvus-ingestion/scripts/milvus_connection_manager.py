#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIQ Milvus 连接管理器 - Milvus Connection Manager
用于SIQ投委会系统的Agent与Milvus数据库连接管理

功能：
- 统一管理所有Agent与Milvus的连接
- 监控Collection数据变化
- 自动触发Agent增量学习
- 保持Agent知识库实时更新

使用示例：
    from milvus_connection_manager import MilvusConnectionManager

    manager = MilvusConnectionManager()
    manager.connect_all_agents()
    manager.start_monitoring()  # 启动变化监控
"""

import json
import time
import threading
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from runtime_compat import normalize_collection_name


# ============================================================================
# 常量配置
# ============================================================================

# Agent与Collection的映射（与Milvus中的Collection名称一致）
# 每个Agent同时连接：私有物理库 + ic_collaboration_shared（共享库）
AGENT_COLLECTION_MAP = {
    "siq_ic_master_coordinator": {
        "private": normalize_collection_name("ic_master_coordinator"),
        "shared": normalize_collection_name("ic_collaboration_shared"),
        "entity_count": 0,
        "description": "协调者"
    },
    "siq_ic_strategist": {
        "private": normalize_collection_name("ic_strategist"),
        "shared": normalize_collection_name("ic_collaboration_shared"),
        "entity_count": 985,
        "description": "战略专家"
    },
    "siq_ic_sector_expert": {
        "private": normalize_collection_name("ic_sector_expert"),
        "shared": normalize_collection_name("ic_collaboration_shared"),
        "entity_count": 3750,
        "description": "行业专家"
    },
    "siq_ic_finance_auditor": {
        "private": normalize_collection_name("ic_finance_auditor"),
        "shared": normalize_collection_name("ic_collaboration_shared"),
        "entity_count": 1335,
        "description": "财务专家"
    },
    "siq_ic_risk_controller": {
        "private": normalize_collection_name("ic_risk_controller"),
        "shared": normalize_collection_name("ic_collaboration_shared"),
        "entity_count": 1239,
        "description": "风控专家"
    },
    "siq_ic_legal_scanner": {
        "private": normalize_collection_name("ic_legal_scanner"),
        "shared": normalize_collection_name("ic_collaboration_shared"),
        "entity_count": 12662,
        "description": "法务专家"
    },
    "siq_ic_chairman": {
        "private": normalize_collection_name("ic_chairman"),
        "shared": normalize_collection_name("ic_collaboration_shared"),
        "entity_count": 1599,
        "description": "主席"
    }
}

AGENT_ID_ALIASES = {
    "ic_master_coordinator": "siq_ic_master_coordinator",
    "ic_strategist": "siq_ic_strategist",
    "ic_sector_expert": "siq_ic_sector_expert",
    "ic_finance_auditor": "siq_ic_finance_auditor",
    "ic_risk_controller": "siq_ic_risk_controller",
    "ic_legal_scanner": "siq_ic_legal_scanner",
    "ic_chairman": "siq_ic_chairman",
}

# 全局共享Collection（固定）
SHARED_COLLECTION = normalize_collection_name("ic_collaboration_shared")
ARCHIVE_COLLECTION = "ic_archive_sop"

# 监控配置
DEFAULT_CHECK_INTERVAL = 300  # 5分钟检查一次
DEFAULT_LEARN_THRESHOLD = 10  # 新增超过10条触发学习


def normalize_agent_id(agent_id: Optional[str]) -> Optional[str]:
    value = str(agent_id or "").strip()
    return AGENT_ID_ALIASES.get(value, value) if value else None


# ============================================================================
# 数据结构
# ============================================================================

class ConnectionStatus(Enum):
    """连接状态"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class LearningStatus(Enum):
    """学习状态"""
    IDLE = "idle"
    LEARNING = "learning"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CollectionSnapshot:
    """Collection快照"""
    name: str
    entity_count: int
    last_updated: str
    fields: List[str] = field(default_factory=list)


@dataclass
class AgentConnection:
    """Agent连接状态"""
    agent_id: str
    agent_name: str
    private_collection: str
    shared_collections: List[str]
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    last_connected: str = None
    entity_counts: Dict[str, int] = field(default_factory=dict)
    error_message: str = None


@dataclass
class LearningTask:
    """学习任务"""
    task_id: str
    agent_id: str
    collection_name: str
    new_entities_count: int
    status: LearningStatus
    created_at: str
    new_entities: List[Dict] = field(default_factory=list)
    completed_at: str = None
    result: str = None


# ============================================================================
# 主类
# ============================================================================

class MilvusConnectionManager:
    """
    Milvus连接管理器
    
    功能：
    1. 统一连接管理 - 所有Agent与Milvus的连接
    2. 状态监控 - 实时监控连接状态
    3. 数据变化检测 - 检测Collection数据变化
    4. 自动学习触发 - 当数据变化时触发Agent学习
    """
    
    def __init__(
        self,
        host: str = "localhost",
        port: str = "19530",
        check_interval: int = DEFAULT_CHECK_INTERVAL
    ):
        """
        初始化连接管理器
        
        Args:
            host: Milvus主机
            port: Milvus端口
            check_interval: 检查间隔（秒）
        """
        self.host = host
        self.port = port
        self.check_interval = check_interval
        
        self.connections: Dict[str, AgentConnection] = {}
        self.snapshots: Dict[str, CollectionSnapshot] = {}
        self.learning_tasks: List[LearningTask] = []
        
        self.milvus_client = None
        self.is_connected = False
        self.is_monitoring = False
        self.monitor_thread = None
        
        print(f"✅ Milvus连接管理器初始化")
        print(f"   主机: {host}:{port}")
        print(f"   检查间隔: {check_interval}秒")
        print(f"   管理Agent数: {len(AGENT_COLLECTION_MAP)}")
    
    # =========================================================================
    # 连接管理
    # =========================================================================
    
    def connect(self) -> bool:
        """
        连接到Milvus
        
        Returns:
            是否连接成功
        """
        print(f"\n🔌 连接到Milvus...")
        
        try:
            from pymilvus import connections, Collection
            
            connections.connect(
                host=self.host,
                port=self.port,
                alias="default"
            )
            
            self.milvus_client = Collection
            self.is_connected = True
            
            print(f"   ✅ Milvus连接成功")
            
            # 获取Collection列表
            from pymilvus import utility
            collections = utility.list_collections()
            print(f"   📦 已存在的Collection: {len(collections)}")
            
            return True
            
        except Exception as e:
            print(f"   ❌ Milvus连接失败: {e}")
            self.is_connected = False
            return False
    
    def disconnect(self):
        """断开Milvus连接"""
        print(f"\n🔌 断开Milvus连接...")
        
        try:
            from pymilvus import connections
            connections.disconnect("default")
            self.is_connected = False
            print(f"   ✅ 已断开连接")
        except Exception as e:
            print(f"   ⚠️ 断开连接时出错: {e}")
    
    def connect_all_agents(self) -> Dict[str, AgentConnection]:
        """
        连接所有Agent的Collection
        
        Returns:
            各Agent的连接状态
        """
        if not self.is_connected:
            if not self.connect():
                return {}
        
        print(f"\n🔗 连接所有Agent的Collection...")
        
        for agent_id, config in AGENT_COLLECTION_MAP.items():
            connection = self._connect_agent(agent_id, config)
            self.connections[agent_id] = connection
        
        # 打印汇总
        connected_count = sum(
            1 for c in self.connections.values() 
            if c.status == ConnectionStatus.CONNECTED
        )
        
        print(f"\n📊 连接汇总:")
        print(f"   总数: {len(self.connections)}")
        print(f"   已连接: {connected_count}")
        print(f"   失败: {len(self.connections) - connected_count}")
        
        return self.connections
    
    def _connect_agent(self, agent_id: str, config: Dict) -> AgentConnection:
        """
        连接单个Agent的Collection（私有库 + 共享库）
        
        Args:
            agent_id: Agent ID
            config: 配置信息
        
        Returns:
            Agent连接状态
        """
        from pymilvus import connections, Collection, utility
        
        agent_name = config.get("description", agent_id)
        private_col = config["private"]
        shared_col = config["shared"]
        
        connection = AgentConnection(
            agent_id=agent_id,
            agent_name=agent_name,
            status=ConnectionStatus.CONNECTING,
            private_collection=private_col,
            shared_collections=[shared_col]
        )
        
        try:
            entity_counts = {}
            
            # 1. 连接私有库
            if utility.has_collection(private_col):
                coll = Collection(private_col)
                coll.load()
                entity_count = coll.num_entities
                entity_counts[private_col] = entity_count
                print(f"   ✅ {agent_id} 私有库: {private_col} ({entity_count}条)")
            else:
                print(f"   ⚠️ {agent_id} 私有库: {private_col} 不存在")
            
            # 2. 连接共享库
            if utility.has_collection(shared_col):
                coll_shared = Collection(shared_col)
                coll_shared.load()
                shared_count = coll_shared.num_entities
                entity_counts[shared_col] = shared_count
                print(f"   ✅ {agent_id} 共享库: {shared_col} ({shared_count}条)")
            else:
                print(f"   ⚠️ {agent_id} 共享库: {shared_col} 不存在")
            
            connection.status = ConnectionStatus.CONNECTED
            connection.last_connected = datetime.now().isoformat()
            connection.entity_counts = entity_counts
            
        except Exception as e:
            connection.status = ConnectionStatus.ERROR
            connection.error_message = str(e)
            print(f"   ❌ {agent_id}: {e}")
        
        return connection
    
    def get_agent_connection(self, agent_id: str) -> Optional[AgentConnection]:
        """
        获取指定Agent的连接状态
        
        Args:
            agent_id: Agent ID
        
        Returns:
            Agent连接状态
        """
        canonical_agent_id = normalize_agent_id(agent_id)
        return self.connections.get(canonical_agent_id)
    
    def check_connection_status(self) -> Dict[str, ConnectionStatus]:
        """
        检查所有Agent的连接状态
        
        Returns:
            各Agent的连接状态
        """
        statuses = {}
        
        for agent_id, connection in self.connections.items():
            # 尝试重新获取实体数
            try:
                from pymilvus import Collection
                coll = Collection(connection.private_collection)
                entity_count = coll.num_entities
                
                if entity_count != connection.entity_counts.get(connection.private_collection):
                    # 数据已变化
                    connection.entity_counts[connection.private_collection] = entity_count
                    print(f"\n🔔 {agent_id}: 数据变化检测")
                    print(f"   {connection.private_collection}: {entity_count} 实体")
                
                connection.status = ConnectionStatus.CONNECTED
                
            except Exception as e:
                connection.status = ConnectionStatus.ERROR
                connection.error_message = str(e)
            
            statuses[agent_id] = connection.status
        
        return statuses
    
    # =========================================================================
    # 数据变化监控
    # =========================================================================
    
    def take_snapshot(self, collection_name: str = None) -> Dict[str, CollectionSnapshot]:
        """
        获取Collection快照
        
        Args:
            collection_name: Collection名称（None表示所有）
        
        Returns:
            Collection快照字典
        """
        from pymilvus import Collection, utility
        
        snapshots = {}
        
        # 获取所有需要监控的Collection
        all_collections = set()
        for config in AGENT_COLLECTION_MAP.values():
            all_collections.add(config["private"])
            all_collections.add(config["shared"])
        all_collections.add(SHARED_COLLECTION)
        all_collections.add(ARCHIVE_COLLECTION)
        
        collections = [normalize_collection_name(collection_name)] if collection_name else list(all_collections)
        
        for coll_name in collections:
            try:
                if not utility.has_collection(coll_name):
                    continue
                
                coll = Collection(coll_name)
                snapshot = CollectionSnapshot(
                    name=coll_name,
                    entity_count=coll.num_entities,
                    last_updated=datetime.now().isoformat()
                )
                snapshots[coll_name] = snapshot
                
            except Exception as e:
                print(f"   ⚠️ 获取快照失败 {coll_name}: {e}")
        
        if collection_name is None:
            self.snapshots = snapshots
        
        return snapshots
    
    def compare_snapshots(
        self, 
        before: Dict[str, CollectionSnapshot], 
        after: Dict[str, CollectionSnapshot]
    ) -> Dict[str, Dict]:
        """
        比较快照差异
        
        Returns:
            变化详情
        """
        changes = {}
        
        for coll_name, after_snapshot in after.items():
            before_snapshot = before.get(coll_name)
            
            if before_snapshot is None:
                # 新增Collection
                changes[coll_name] = {
                    "type": "new",
                    "entity_count": after_snapshot.entity_count,
                    "change": after_snapshot.entity_count
                }
            elif before_snapshot.entity_count != after_snapshot.entity_count:
                # 数据变化
                changes[coll_name] = {
                    "type": "changed",
                    "before": before_snapshot.entity_count,
                    "after": after_snapshot.entity_count,
                    "change": after_snapshot.entity_count - before_snapshot.entity_count
                }
        
        return changes
    
    def start_monitoring(
        self, 
        on_change_callback: Callable = None,
        learn_callback: Callable = None
    ):
        """
        启动变化监控
        
        Args:
            on_change_callback: 变化检测回调(collection_name, change_info)
            learn_callback: 触发学习的回调(agent_id, new_entities)
        """
        if self.is_monitoring:
            print(f"⚠️ 监控已在运行中")
            return
        
        if not self.is_connected:
            if not self.connect():
                print(f"❌ 无法启动监控：Milvus未连接")
                return
        
        self.is_monitoring = True
        
        # 获取初始快照
        self.take_snapshot()
        
        # 启动监控线程
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(on_change_callback, learn_callback),
            daemon=True
        )
        self.monitor_thread.start()
        
        print(f"\n🔔 变化监控已启动")
        print(f"   检查间隔: {self.check_interval}秒")
    
    def stop_monitoring(self):
        """停止变化监控"""
        self.is_monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        print(f"\n🔕 变化监控已停止")
    
    def _monitor_loop(
        self, 
        on_change_callback: Callable,
        learn_callback: Callable
    ):
        """监控循环"""
        while self.is_monitoring:
            try:
                # 获取当前快照
                current_snapshots = self.take_snapshot()
                
                # 比较差异
                changes = self.compare_snapshots(self.snapshots, current_snapshots)
                
                if changes:
                    print(f"\n🔔 [{datetime.now().strftime('%H:%M:%S')}] 检测到变化:")
                    
                    for coll_name, change_info in changes.items():
                        print(f"   {coll_name}: {change_info}")
                        
                        # 触发回调
                        if on_change_callback:
                            on_change_callback(coll_name, change_info)
                        
                        # 触发学习
                        if learn_callback and change_info.get("change", 0) > 0:
                            # 找出受影响的Agent
                            affected_agents = self._find_affected_agents(coll_name)
                            
                            for agent_id in affected_agents:
                                # 获取新增实体
                                new_entities = self._get_new_entities(
                                    agent_id, 
                                    coll_name,
                                    change_info.get("change", 0)
                                )
                                
                                if learn_callback:
                                    learn_callback(agent_id, coll_name, new_entities)
                    
                    # 更新快照
                    self.snapshots = current_snapshots
                
                # 等待下次检查
                time.sleep(self.check_interval)
                
            except Exception as e:
                print(f"   ⚠️ 监控循环异常: {e}")
                time.sleep(self.check_interval)
    
    def _find_affected_agents(self, collection_name: str) -> List[str]:
        """找出受影响的Agent"""
        affected = []
        canonical_collection = normalize_collection_name(collection_name)

        for agent_id, config in AGENT_COLLECTION_MAP.items():
            if (config["private"] == canonical_collection or
                config["shared"] == canonical_collection):
                affected.append(agent_id)

        return affected

    def _get_new_entities(
        self, 
        agent_id: str, 
        collection_name: str, 
        change_count: int
    ) -> List[Dict]:
        """
        获取新增实体
        
        Args:
            agent_id: Agent ID
            collection_name: Collection名称
            change_count: 变化数量
        
        Returns:
            新增实体列表
        """
        try:
            from pymilvus import Collection
            
            coll = Collection(collection_name)
            
            # 获取最新N条数据
            # 假设有自增ID或时间戳字段
            results = coll.query(
                expr="",
                output_fields=["*"],
                limit=change_count,
                offset=max(0, coll.num_entities - change_count)
            )
            
            return results
            
        except Exception as e:
            print(f"   ⚠️ 获取新增实体失败: {e}")
            return []
    
    # =========================================================================
    # Agent学习管理
    # =========================================================================
    
    def trigger_agent_learning(
        self,
        agent_id: str,
        collection_name: str,
        new_entities: List[Dict] = None,
        force: bool = False
    ) -> LearningTask:
        """
        触发Agent学习
        
        Args:
            agent_id: Agent ID
            collection_name: Collection名称
            new_entities: 新增实体（如不提供则自动获取）
            force: 是否强制学习（即使没有新数据）
        
        Returns:
            学习任务
        """
        canonical_agent_id = normalize_agent_id(agent_id) or agent_id
        canonical_collection = normalize_collection_name(collection_name)
        if new_entities is None:
            new_entities = self._get_new_entities(canonical_agent_id, canonical_collection, 10)
        
        task = LearningTask(
            task_id=f"learn_{canonical_agent_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            agent_id=canonical_agent_id,
            collection_name=canonical_collection,
            new_entities_count=len(new_entities),
            new_entities=new_entities,
            status=LearningStatus.LEARNING,
            created_at=datetime.now().isoformat()
        )
        
        self.learning_tasks.append(task)
        
        print(f"\n📚 触发Agent学习:")
        print(f"   Agent: {canonical_agent_id}")
        print(f"   Collection: {canonical_collection}")
        print(f"   新增实体: {len(new_entities)}")
        
        # OpenClaw used to notify agents from here. Hermes IC runs should go
        # through Deal OS workflow jobs and startup-retrieval receipts instead.
        self._execute_learning(task)
        
        return task
    
    def _execute_learning(self, task: LearningTask):
        """执行学习任务"""
        try:
            print(f"   🔄 执行学习...")
            
            # 模拟学习过程
            # 实际应该：
            # 1. 通过 Deal OS workflow job 通知对应 Agent
            # 2. Agent调用 retrieval 获取新数据
            # 3. Agent分析并更新知识
            
            # 这里只是模拟完成
            task.status = LearningStatus.COMPLETED
            task.completed_at = datetime.now().isoformat()
            task.result = f"学习完成，Agent: {task.agent_id}"
            
            print(f"   ✅ 学习完成")
            
        except Exception as e:
            task.status = LearningStatus.FAILED
            task.result = str(e)
            print(f"   ❌ 学习失败: {e}")
    
    def get_learning_tasks(
        self, 
        agent_id: str = None,
        status: LearningStatus = None
    ) -> List[LearningTask]:
        """
        获取学习任务
        
        Args:
            agent_id: Agent ID（None表示所有）
            status: 任务状态（None表示所有）
        
        Returns:
            学习任务列表
        """
        tasks = self.learning_tasks
        
        if agent_id:
            canonical_agent_id = normalize_agent_id(agent_id) or agent_id
            tasks = [t for t in tasks if t.agent_id == canonical_agent_id]
        
        if status:
            tasks = [t for t in tasks if t.status == status]
        
        return tasks
    
    # =========================================================================
    # 便捷方法
    # =========================================================================
    
    def quick_connect(self, agent_id: str = None) -> bool:
        """
        快速连接
        
        Args:
            agent_id: Agent ID（None表示连接所有）
        
        Returns:
            是否成功
        """
        if not self.connect():
            return False
        
        if agent_id:
            canonical_agent_id = normalize_agent_id(agent_id) or agent_id
            config = AGENT_COLLECTION_MAP.get(canonical_agent_id)
            if config:
                connection = self._connect_agent(canonical_agent_id, config)
                self.connections[canonical_agent_id] = connection
                return connection.status == ConnectionStatus.CONNECTED
        else:
            self.connect_all_agents()
        
        return True
    
    def get_status_report(self) -> str:
        """
        获取状态报告
        
        Returns:
            格式化的状态报告
        """
        lines = []
        lines.append("=" * 70)
        lines.append("Milvus连接管理器 - 状态报告")
        lines.append("=" * 70)
        lines.append(f"\nMilvus连接: {'✅ 已连接' if self.is_connected else '❌ 未连接'}")
        lines.append(f"监控状态: {'🔔 运行中' if self.is_monitoring else '🔕 已停止'}")
        lines.append(f"管理Agent数: {len(self.connections)}")
        
        lines.append("\n" + "-" * 70)
        lines.append("Agent连接详情（每个Agent连接：私有库 + 共享库）")
        lines.append("-" * 70)
        
        for agent_id, conn in self.connections.items():
            status_icon = {
                ConnectionStatus.CONNECTED: "✅",
                ConnectionStatus.CONNECTING: "🔄",
                ConnectionStatus.DISCONNECTED: "❌",
                ConnectionStatus.ERROR: "⚠️"
            }.get(conn.status, "⚪")
            
            lines.append(f"\n{status_icon} {conn.agent_name} ({agent_id})")
            
            # 打印私有库
            private_col = conn.private_collection
            private_count = conn.entity_counts.get(private_col, "N/A")
            lines.append(f"   ├─ 私有库: {private_col} ({private_count}条)")
            
            # 打印共享库
            shared_col = conn.shared_collections[0] if conn.shared_collections else "N/A"
            shared_count = conn.entity_counts.get(shared_col, "N/A")
            lines.append(f"   └─ 共享库: {shared_col} ({shared_count}条)")
            
            if conn.error_message:
                lines.append(f"     ⚠️ 错误: {conn.error_message}")
        
        lines.append("\n" + "-" * 70)
        lines.append("Collection总览")
        lines.append("-" * 70)
        
        # 按Collection汇总
        all_collections = {}
        for conn in self.connections.values():
            for coll_name, count in conn.entity_counts.items():
                if coll_name not in all_collections:
                    all_collections[coll_name] = count
        
        for coll_name, count in all_collections.items():
            lines.append(f"  {coll_name}: {count}条")
        
        lines.append("\n学习任务:")
        completed = len([t for t in self.learning_tasks if t.status == LearningStatus.COMPLETED])
        pending = len([t for t in self.learning_tasks if t.status == LearningStatus.LEARNING])
        lines.append(f"  已完成: {completed}")
        lines.append(f"  进行中: {pending}")
        
        lines.append("=" * 70)
        
        return "\n".join(lines)


# ============================================================================
# 使用示例
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*70)
    print("SIQ Milvus连接管理器 - 测试")
    print("="*70)
    
    # 创建管理器
    manager = MilvusConnectionManager()
    
    # 快速连接
    print("\n📡 快速连接...")
    success = manager.quick_connect()
    
    if success:
        # 获取状态报告
        print(manager.get_status_report())
        
        # # 启动监控（取消注释以启用实时监控）
        # def on_change(coll_name, change_info):
        #     print(f"   变化详情: {change_info}")
        # 
        # def trigger_learn(agent_id, coll_name, new_entities):
        #     print(f"   📚 触发 {agent_id} 学习，新增 {len(new_entities)} 条数据")
        # 
        # manager.start_monitoring(
        #     on_change_callback=on_change,
        #     learn_callback=trigger_learn
        # )
        
        # 手动触发学习
        print("\n📚 测试触发Agent学习...")
        task = manager.trigger_agent_learning(
            agent_id="ic_strategist",
            collection_name="ic_strategist",  # Collection名称与Agent ID一致
            force=True
        )
        
        print(f"\n学习任务状态: {task.status.value}")
    
    else:
        print("❌ 连接失败，请检查Milvus服务")
    
    print("\n" + "="*70)
