#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIQ 投委会知识库 — Milvus Collection 初始化脚本 V2.0

功能：
  - 一键初始化 / 重建所有投委会 Collection
  - 自动检测 GPU 支持，选择最优索引
  - schema 优化：metadata 存储原文切片 + 标题前缀
  - metric_type 统一为 IP（内积 = cosine，语义检索最优）
  - project_tag 标量倒排索引
  - metadata.source 标量倒排索引（支持关键词过滤）

用法：
  python init_collections.py                  # 初始化全部（保留已有）
  python init_collections.py --reset          # 清空重建全部
  python init_collections.py --reset --target ic_legal_scanner  # 只重建一个
  python init_collections.py --list           # 只查看当前状态
  python init_collections.py --dry-run        # 预览不执行
"""

import argparse
import sys
import time
import json
from datetime import datetime

from pymilvus import (
    connections,
    FieldSchema,
    CollectionSchema,
    Collection,
    DataType,
    utility,
)

# ==================== 配置 ====================

MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = 19530
VECTOR_DIM = 1024

# Collection 注册表（与当前 Milvus 物理名称一致）
WORKSPACES = {
    "ic_chairman":              "投委会主席 (Chairman)",
    "ic_finance_auditor":       "财务审计官 (Finance Auditor)",
    "ic_sector_expert":         "行业专家 (Sector Expert)",
    "ic_legal_scanner":         "法务合规专家 (Legal Scanner)",
    "ic_strategist":            "战略专家 (Strategist)",
    "ic_risk_controller":       "风险管理官 (Risk Controller)",
    "ic_master_coordinator":    "投委会秘书 (Master Coordinator)",
    "ic_collaboration_shared":  "协同共享工作区 (Shared Discussion)",
    "ic_archive_sop":           "机构历史案例库 (SOP Archive)",
}

def resolve_target(name: str) -> str:
    """Return the current physical collection name."""
    return name


# ==================== GPU 检测 ====================

def detect_gpu_support() -> bool:
    """检测 Milvus 是否挂载了 GPU"""
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "inspect", "milvus-standalone",
             "--format", "{{json .HostConfig.DeviceRequests}}"],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout.strip()
        if output and output != "null":
            devices = json.loads(output)
            if isinstance(devices, list) and len(devices) > 0:
                # 检查是否请求了 nvidia GPU
                for req in devices:
                    driver = req.get("Driver", "")
                    if "nvidia" in driver.lower() or "gpu" in driver.lower():
                        return True
                # CDI 格式
                if devices[0].get("CDI"):
                    return True
    except Exception:
        pass

    # 备选：检查 container runtime
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "inspect", "milvus-standalone",
             "--format", "{{.HostConfig.Runtime}}"],
            capture_output=True, text=True, timeout=5,
        )
        if "nvidia" in result.stdout.strip():
            return True
    except Exception:
        pass

    return False


# ==================== 索引策略 ====================

def build_index_params(metric: str = "IP", use_gpu: bool = False) -> dict:
    """
    构建向量索引参数。

    metric 选择逻辑：
    - IP（内积）：向量已 L2 归一化时，IP ≈ cosine similarity，语义检索效果最优
    - L2：如果不归一化或需要精确欧氏距离

    HNSW vs GPU_CAGRA：
    - HNSW：CPU 高性能，M=32 efConstruction=256 已是高精度配置
    - GPU_CAGRA：GPU 加速，适合 1M+ 规模，中小规模收益不大
    """
    if use_gpu:
        return {
            "metric_type": metric,
            "index_type": "GPU_CAGRA",
            "params": {
                "intermediate_graph_degree": 64,
                "graph_degree": 32,
                "efConstruction": 256,
            },
        }
    else:
        return {
            "metric_type": metric,
            "index_type": "HNSW",
            "params": {
                "M": 32,
                "efConstruction": 256,
            },
        }


# ==================== Schema 定义 ====================

def build_schema() -> CollectionSchema:
    """
    统一 Collection Schema。

    字段说明：
    - id:           自增主键
    - vector:       归一化 float32 向量，dim=1024
    - project_tag:  批次标签（INVERTED 索引，支持精确过滤）
    - metadata:     JSON 字段，存储：
        - source:       源文件名（如 "私募投资基金监督管理条例_20230703.md"）
        - text:         切片原文（前 600 字，检索可直接返回）
        - chunk_index:  切片序号
        - total_chunks: 文件总切片数
        - char_count:   切片字符数
        - timestamp:    入库时间
    """
    fields = [
        FieldSchema(
            name="id",
            dtype=DataType.INT64,
            is_primary=True,
            auto_id=True,
            description="自增主键",
        ),
        FieldSchema(
            name="vector",
            dtype=DataType.FLOAT_VECTOR,
            dim=VECTOR_DIM,
            description="L2 归一化 float32 向量",
        ),
        FieldSchema(
            name="project_tag",
            dtype=DataType.VARCHAR,
            max_length=128,
            description="批次标签",
        ),
        FieldSchema(
            name="metadata",
            dtype=DataType.JSON,
            description="元数据 (source, text, chunk_index, ...)",
        ),
    ]
    return CollectionSchema(fields, enable_dynamic_field=False)


# ==================== 核心逻辑 ====================

def init_collections(
    targets: list = None,
    reset: bool = False,
    dry_run: bool = False,
    metric: str = "IP",
    force_gpu: bool = False,
    force_cpu: bool = False,
):
    # 连接 Milvus
    try:
        connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
        print(f"✅ Milvus connected: {MILVUS_HOST}:{MILVUS_PORT}")
    except Exception as e:
        print(f"❌ Milvus 连接失败: {e}")
        sys.exit(1)

    # 确定目标
    if targets:
        resolved = [resolve_target(t) for t in targets]
        todo = {k: v for k, v in WORKSPACES.items() if k in resolved}
        if not todo:
            print(f"❌ 未找到匹配的 Collection: {targets}")
            print(f"   可选: {list(WORKSPACES.keys())}")
            sys.exit(1)
    else:
        todo = dict(WORKSPACES)

    # GPU 检测
    if force_cpu:
        use_gpu = False
    elif force_gpu:
        use_gpu = True
    else:
        use_gpu = detect_gpu_support()

    idx_params = build_index_params(metric=metric, use_gpu=use_gpu)
    idx_type = idx_params["index_type"]
    idx_metric = idx_params["metric_type"]

    print(f"\n{'═' * 60}")
    print(f"  SIQ 知识库 Collection 初始化")
    print(f"{'═' * 60}")
    print(f"  目标 Collection:  {len(todo)} 个")
    print(f"  向量维度:         {VECTOR_DIM}")
    print(f"  距离度量:         {idx_metric}")
    print(f"  索引类型:         {idx_type}")
    print(f"  GPU 加速:         {'✅' if use_gpu else '❌'}")
    print(f"  重置已有:         {'✅' if reset else '❌'}")
    print(f"  模拟运行:         {'✅' if dry_run else '❌'}")
    print(f"{'═' * 60}\n")

    t0 = time.time()

    for col_name, desc in todo.items():
        icon = _get_icon(col_name)
        print(f"{'─' * 50}")
        print(f"  {icon} {col_name}")
        print(f"     描述: {desc}")

        if dry_run:
            print(f"     [DRY-RUN] 将创建 HNSW/IP 索引 + INVERTED 标量索引")
            continue

        # 重置
        if reset and utility.has_collection(col_name):
            old_count = Collection(col_name).num_entities
            utility.drop_collection(col_name)
            print(f"     🗑️  已删除旧数据 ({old_count:,} 条)")

        # 创建
        if utility.has_collection(col_name):
            existing = Collection(col_name)
            entity_count = existing.num_entities
            # 检查索引一致性
            metric_ok = _check_metric(existing, idx_metric)
            has_project_tag_idx = any(
                i.field_name == "project_tag" for i in existing.indexes
            )
            status = "✅ 已存在"
            if not metric_ok:
                status += f" ⚠️ metric 不一致 (actual={_get_metric(existing)}, expected={idx_metric})，建议 --reset"
            if not has_project_tag_idx:
                status += " ⚠️ 缺少 project_tag 索引"
            print(f"     {status} — {entity_count:,} 条实体")
            continue

        # 新建
        schema = build_schema()
        col = Collection(col_name, schema, description=desc)

        # 向量索引
        col.create_index(field_name="vector", index_params=idx_params)
        print(f"     📐 向量索引: {idx_type} / {idx_metric} / dim={VECTOR_DIM}")

        # 标量索引
        col.create_index(
            field_name="project_tag",
            index_params={"index_type": "INVERTED"},
        )
        print(f"     🏷️  标量索引: INVERTED (project_tag)")

        col.flush()
        print(f"     ✅ 创建完成")

    elapsed = time.time() - t0
    print(f"\n{'═' * 60}")
    print(f"  ✅ 完成。耗时 {elapsed:.1f}s")
    print(f"{'═' * 60}")


def _get_icon(name: str) -> str:
    icons = {
        "ic_chairman": "👔", "ic_finance_auditor": "💰",
        "ic_sector_expert": "🔬", "ic_legal_scanner": "⚖️",
        "ic_strategist": "🌐", "ic_risk_controller": "⚠️",
        "ic_master_coordinator": "📋", "ic_collaboration_shared": "🤝",
        "ic_archive_sop": "📚",
    }
    return icons.get(name, "📁")


def _get_metric(col: Collection) -> str:
    for idx in col.indexes:
        if idx.field_name == "vector":
            return idx.params.get("metric_type", "?")
    return "?"


def _check_metric(col: Collection, expected: str) -> bool:
    return _get_metric(col) == expected


def list_collections():
    """打印当前所有 Collection 的状态"""
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    print(f"\n{'═' * 70}")
    print(f"  Milvus Collection 状态一览")
    print(f"{'═' * 70}")
    print(f"  {'Collection':<30s} {'实体数':>8s} {'Metric':>6s} {'Index':>12s} {'状态':>8s}")
    print(f"  {'─' * 68}")

    for name in utility.list_collections():
        col = Collection(name)
        count = col.num_entities
        metric = _get_metric(col)
        idx_type = "N/A"
        for idx in col.indexes:
            if idx.field_name == "vector":
                idx_type = idx.params.get("index_type", "N/A")
                break

        icon = _get_icon(name)
        desc = WORKSPACES.get(name, "")

        # 状态检查
        if count == 0:
            status = "📭 空"
        elif metric == "L2":
            status = "⚠️ L2"
        elif metric == "IP":
            status = "✅"
        else:
            status = "❓"

        print(f"  {icon} {name:<28s} {count:>8,d} {metric:>6s} {idx_type:>12s} {status:>8s}")
        if desc:
            print(f"      {desc}")

    print(f"{'═' * 70}\n")


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(
        description="SIQ 投委会知识库 — Milvus Collection 初始化工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python init_collections.py --list                     # 查看当前状态
  python init_collections.py --reset --target ic_legal_scanner  # 重建法务库
  python init_collections.py --reset --metric L2        # 全部重建，使用 L2 度量
  python init_collections.py --dry-run                   # 预览不执行
  python init_collections.py --reset --force-gpu        # 强制使用 GPU_CAGRA 索引
        """,
    )
    parser.add_argument("--reset", action="store_true", help="清空并重建所有目标 Collection")
    parser.add_argument("--target", nargs="+", help="指定目标 Collection（默认全部）")
    parser.add_argument("--list", action="store_true", help="列出当前所有 Collection 状态")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行，不实际创建")
    parser.add_argument("--metric", choices=["IP", "L2", "COSINE"], default="IP",
                        help="距离度量（默认 IP，向量已归一化时等价 cosine）")
    parser.add_argument("--force-gpu", action="store_true", help="强制使用 GPU_CAGRA 索引")
    parser.add_argument("--force-cpu", action="store_true", help="强制使用 CPU HNSW 索引")

    args = parser.parse_args()

    if args.list:
        list_collections()
        return

    init_collections(
        targets=args.target,
        reset=args.reset,
        dry_run=args.dry_run,
        metric=args.metric,
        force_gpu=args.force_gpu,
        force_cpu=args.force_cpu,
    )


if __name__ == "__main__":
    main()
