#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIQ 投委会全格式知识库入库系统 V5.0
==========================================
Gradio Web UI + 异步入库引擎
支持：PDF / DOCX / MD / TXT → Milvus 向量库
Embedding：本地 vLLM（Qwen3-VL-Embedding-2B）或 DashScope 云端 API
"""

import os
import re
import sys
import json
import glob
import asyncio
import logging
import base64
import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import aiohttp
import gradio as gr
from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType

# ==================== PDF 解析 ====================
import fitz  # PyMuPDF
from docx import Document  # python-docx

# ==================== 核心配置 ====================
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
VECTOR_DIM = 1024

# 切块参数（基于检索精度优化）
CHUNK_SIZE = 480
CHUNK_OVERLAP = 80
MAX_CHUNKS_PER_FILE = 500  # 安全上限

# 本地 vLLM
VLLM_BASE = os.environ.get("VLLM_BASE", "http://127.0.0.1:8000/v1")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "Qwen3-VL-Embedding-2B")

# DashScope 云端
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_MODEL = "qwen3-vl-embedding"
DASHSCOPE_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"

# 并发控制
MAX_CONCURRENT = 6

# 默认 collection 定义
ROLE_REGISTRY = {
    "ic_chairman":            "投委会主席",
    "ic_finance_auditor":     "财务审计委员",
    "ic_sector_expert":       "行业专家",
    "ic_legal_scanner":       "法务合规委员",
    "ic_strategist":          "战略专家",
    "ic_risk_controller":     "风险管理委员",
    "ic_master_coordinator":  "投委会秘书",
    "ic_collaboration_shared":"协同共享工作区",
    "ic_archive_sop":         "机构历史案例库 (SOP)",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ingest")

# ==================== Milvus 管理 ====================

def milvus_connect():
    """确保 Milvus 连接"""
    try:
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT, timeout=5)
    except Exception:
        pass  # 已连接

def list_collections() -> Dict[str, str]:
    """列出所有 collection，返回 {name: entity_count}"""
    milvus_connect()
    result = {}
    for name in sorted(utility.list_collections()):
        col = Collection(name)
        result[name] = col.num_entities
    return result

def get_collection_info(name: str) -> Dict[str, Any]:
    """获取 collection 详情"""
    milvus_connect()
    if not utility.has_collection(name):
        return None
    col = Collection(name)
    fields = []
    vector_dim = VECTOR_DIM
    for f in col.schema.fields:
        info = {"name": f.name, "type": str(f.dtype)}
        if f.dtype == DataType.FLOAT_VECTOR:
            vector_dim = f.params.get("dim", VECTOR_DIM)
            info["dim"] = vector_dim
        if f.dtype == DataType.VARCHAR:
            info["max_length"] = f.params.get("max_length", "?")
        fields.append(info)
    
    indexes = []
    for idx in col.indexes:
        indexes.append({"field": idx.field_name, "type": idx.params.get("index_type", "?"),
                        "metric": idx.params.get("metric_type", "")})
    
    return {
        "name": name,
        "description": col.description or "",
        "num_entities": col.num_entities,
        "vector_dim": vector_dim,
        "fields": fields,
        "indexes": indexes,
    }

def create_collection(name: str, description: str = "", dim: int = VECTOR_DIM, metric: str = "IP") -> bool:
    """创建新 collection"""
    milvus_connect()
    if utility.has_collection(name):
        return False
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
        FieldSchema(name="project_tag", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="metadata", dtype=DataType.JSON),
    ]
    schema = CollectionSchema(fields, description=description or ROLE_REGISTRY.get(name, ""))
    col = Collection(name, schema)
    # 向量索引（IP = cosine 近似，已归一化向量）
    col.create_index("vector", {
        "metric_type": metric,
        "index_type": "HNSW",
        "params": {"M": 32, "efConstruction": 256}
    })
    # 标签倒排索引（支持按 project_tag 过滤）
    col.create_index("project_tag", {"index_type": "INVERTED"})
    col.flush()
    log.info(f"Created collection '{name}' (dim={dim}, metric={metric})")
    return True

def reset_collection(name: str) -> bool:
    """删除并重建 collection"""
    milvus_connect()
    if utility.has_collection(name):
        utility.drop_collection(name)
        log.info(f"Dropped collection '{name}'")
    return create_collection(name)

# ==================== Embedding 引擎 ====================

async def embed_vllm_batch(session: aiohttp.ClientSession, texts: List[str], semaphore: asyncio.Semaphore) -> List[Optional[np.ndarray]]:
    """本地 vLLM 批量 embedding"""
    results = [None] * len(texts)
    # vLLM 支持批量，但 chunk 太多时分批
    batch_size = 32
    async with semaphore:
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            for attempt in range(3):
                try:
                    async with session.post(f"{VLLM_BASE}/embeddings",
                        json={"model": VLLM_MODEL, "input": batch},
                        timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for item in data["data"]:
                                idx = item["index"] + start
                                vec = np.array(item["embedding"], dtype=np.float32)
                                norm = np.linalg.norm(vec)
                                if norm > 1e-12:
                                    vec = vec / norm
                                results[idx] = vec
                            break
                        else:
                            await asyncio.sleep(1.5 ** attempt)
                except Exception as e:
                    log.warning(f"vLLM retry {attempt}: {e}")
                    await asyncio.sleep(1.5 ** attempt)
    return results

async def embed_dashscope_batch(session: aiohttp.ClientSession, texts: List[str], semaphore: asyncio.Semaphore) -> List[Optional[np.ndarray]]:
    """DashScope 云端批量 embedding（逐条，API 不支持大批量）"""
    results = [None] * len(texts)
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"}
    
    async with semaphore:
        for i, text in enumerate(texts):
            payload = {
                "model": DASHSCOPE_MODEL,
                "input": {"contents": [{"text": text}]},
                "parameters": {"dimension": VECTOR_DIM}
            }
            for attempt in range(3):
                try:
                    async with session.post(DASHSCOPE_ENDPOINT, headers=headers, json=payload,
                        timeout=aiohttp.ClientTimeout(total=45)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            vec = np.array(data["output"]["embeddings"][0]["embedding"], dtype=np.float32)
                            norm = np.linalg.norm(vec)
                            if norm > 1e-12:
                                vec = vec / norm
                            results[i] = vec
                            break
                        elif resp.status == 429:
                            wait = 2 ** attempt + np.random.uniform(0, 1.5)
                            log.warning(f"DashScope 429, wait {wait:.1f}s")
                            await asyncio.sleep(wait)
                        else:
                            await asyncio.sleep(1.5 ** attempt)
                except Exception as e:
                    log.warning(f"DashScope retry {attempt}: {e}")
                    await asyncio.sleep(1.5 ** attempt)
    return results

# ==================== 文件解析 ====================

# 法规条款边界正则（中文法规典型格式）
_ARTICLE_RE = re.compile(r'(第[一二三四五六七八九十百千零\d]+条[：:\s])')

def _smart_split_text(text: str, fname: str, meta_extra: Dict = None) -> List[Dict]:
    """
    智能切块：优先在条款边界切分，兜底固定长度切分
    每块加上标题前缀（提升检索精度）
    """
    items = []
    clean_text = " ".join(text.split())
    title = Path(fname).stem  # 文件名去后缀作为标题前缀
    
    if not clean_text:
        return items
    
    # 尝试按条款边界切分
    parts = _ARTICLE_RE.split(clean_text)
    if len(parts) > 3:
        # 有条款结构，按条款合并
        segments = []
        current = parts[0]  # 第一段（标题/引言部分）
        for i in range(1, len(parts), 2):
            article = parts[i] if i < len(parts) else ""
            body = parts[i + 1] if i + 1 < len(parts) else ""
            current = current + article + body
            if len(current) >= CHUNK_SIZE - CHUNK_OVERLAP or i + 2 >= len(parts):
                segments.append(current.strip())
                current = ""
        if current.strip():
            segments.append(current.strip())
    else:
        # 无条款结构，固定长度切
        segments = []
        start = 0
        while start < len(clean_text):
            end = min(start + CHUNK_SIZE, len(clean_text))
            segments.append(clean_text[start:end])
            if end >= len(clean_text):
                break
            start = end - CHUNK_OVERLAP
    
    for idx, seg in enumerate(segments):
        if len(seg.strip()) < 15:
            continue
        meta = {
            "source": fname,
            "chunk_index": idx,
            "total_chunks": len(segments),
        }
        if meta_extra:
            meta.update(meta_extra)
        items.append({
            "content": f"{title}\n{seg}",
            "meta": meta,
        })
    
    return items

def _parse_pdf(path: str) -> List[Dict]:
    items = []
    doc = fitz.open(str(path))
    fname = os.path.basename(path)
    
    # 策略：按页提取，然后按条款切分
    page_texts = []
    for page_idx, page in enumerate(doc):
        text = page.get_text("text").strip()
        if len(text) > 20:
            page_texts.append((page_idx, text))
    
    # 合并全文
    full_text = "\n".join(t for _, t in page_texts)
    if full_text:
        items.extend(_smart_split_text(full_text, fname, {"format": "pdf", "pages": len(page_texts)}))
    
    # 视觉提取（针对扫描件/图表页 - 文本极少的页）
    for page_idx, page in enumerate(doc):
        text = page.get_text("text").strip()
        if len(text) < 80:
            try:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                img_b64 = base64.b64encode(pix.tobytes("jpg")).decode()
                items.append({
                    "content": f"[视觉页面] {fname} 第{page_idx+1}页",
                    "meta": {
                        "source": fname, "page": page_idx + 1,
                        "is_visual": True, "format": "pdf_visual",
                    },
                    "image": f"data:image/jpeg;base64,{img_b64}",
                })
            except Exception:
                pass
    
    doc.close()
    return items[:MAX_CHUNKS_PER_FILE]

def _parse_docx(path: str) -> List[Dict]:
    doc = Document(str(path))
    full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return _smart_split_text(full_text, os.path.basename(path), {"format": "docx"})[:MAX_CHUNKS_PER_FILE]

def _parse_text(path: str) -> List[Dict]:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    return _smart_split_text(content, os.path.basename(path),
        {"format": Path(path).suffix[1:].lower()})[:MAX_CHUNKS_PER_FILE]

def parse_file(path: str) -> List[Dict]:
    """自动识别格式并解析"""
    ext = Path(path).suffix.lower()
    if ext == '.pdf':
        return _parse_pdf(path)
    elif ext == '.docx':
        return _parse_docx(path)
    elif ext in ('.md', '.txt', '.text'):
        return _parse_text(path)
    else:
        return []

# ==================== 入库引擎 ====================

# 全局状态
_ingest_state = {
    "running": False,
    "total_files": 0,
    "processed": 0,
    "inserted_chunks": 0,
    "failed_files": [],
    "current_file": "",
    "start_time": None,
    "cancelled": False,
    "log_lines": [],
}

def _add_log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    _ingest_state["log_lines"].append(line)
    if len(_ingest_state["log_lines"]) > 200:
        _ingest_state["log_lines"] = _ingest_state["log_lines"][-100:]
    log.info(msg)

async def run_ingest(
    collection_name: str,
    data_path: str,
    batch_tag: str,
    embedding_source: str,  # "vllm" or "dashscope"
    file_formats: List[str],
    reset_collection: bool = False,
    progress=None,
):
    """异步入库主流程"""
    state = _ingest_state
    state.update({
        "running": True, "total_files": 0, "processed": 0,
        "inserted_chunks": 0, "failed_files": [], "current_file": "",
        "start_time": time.time(), "cancelled": False, "log_lines": [],
    })
    
    milvus_connect()
    
    # 验证路径
    if not os.path.isdir(data_path):
        _add_log(f"❌ 路径不存在: {data_path}", "ERROR")
        state["running"] = False
        return
    
    # 准备 collection
    if reset_collection:
        _add_log(f"🔄 重置 collection: {collection_name}")
        if utility.has_collection(collection_name):
            utility.drop_collection(collection_name)
        create_collection(collection_name)
    elif not utility.has_collection(collection_name):
        _add_log(f"🆕 新建 collection: {collection_name}")
        create_collection(collection_name)
    
    col = Collection(collection_name)
    col.load()
    _add_log(f"📊 Collection '{collection_name}' 现有 {col.num_entities} 条")
    
    # 扫描文件
    ext_map = {"PDF": "*.pdf", "DOCX": "*.docx", "Markdown": "*.md", "TXT": "*.txt"}
    files = set()
    for fmt in file_formats:
        pattern = ext_map.get(fmt)
        if pattern:
            files.update(glob.glob(os.path.join(data_path, "**", pattern), recursive=True))
    files = sorted(files)
    
    if not files:
        _add_log("⚠️ 没有找到匹配的文件")
        state["running"] = False
        return
    
    state["total_files"] = len(files)
    _add_log(f"📂 发现 {len(files)} 个文件待处理")
    _add_log(f"🔧 Embedding: {'本地 vLLM' if embedding_source == 'vllm' else 'DashScope 云端'}")
    _add_log(f"📝 切块: size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}, 条款感知=开启")
    
    # 选择 embedding 函数
    embed_fn = embed_vllm_batch if embedding_source == "vllm" else embed_dashscope_batch
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    
    # 断点续传
    progress_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  f".progress_{collection_name}.json")
    processed_set = set()
    if os.path.exists(progress_file) and not reset_collection:
        with open(progress_file, 'r') as f:
            processed_set = set(json.load(f))
        _add_log(f"📂 断点续传: 已处理 {len(processed_set)} 个文件")
    
    # 入库主循环
    batch_vectors = []
    batch_tags = []
    batch_metas = []
    FLUSH_INTERVAL = 100
    
    async with aiohttp.ClientSession() as session:
        for fidx, fpath in enumerate(files):
            if state["cancelled"]:
                _add_log("⛔ 用户取消入库", "WARN")
                break
            
            fname = os.path.basename(fpath)
            if fname in processed_set:
                state["processed"] = fidx + 1
                progress(state["processed"] / state["total_files"],
                        f"跳过(已处理): {fname}")
                continue
            
            state["current_file"] = fname
            progress(state["processed"] / state["total_files"],
                    f"处理中 ({state['processed']}/{state['total_files']}): {fname}")
            
            try:
                # 解析文件
                items = parse_file(fpath)
                if not items:
                    _add_log(f"⚠️ 无内容: {fname}")
                    processed_set.add(fname)
                    state["processed"] = fidx + 1
                    continue
                
                # 分离纯文本和图像
                text_items = [it for it in items if not it.get("image")]
                image_items = [it for it in items if it.get("image")]
                
                # 纯文本 embedding
                if text_items:
                    texts = [it["content"] for it in text_items]
                    vecs = await embed_fn(session, texts, semaphore)
                    
                    for i, vec in enumerate(vecs):
                        if vec is not None:
                            meta = text_items[i]["meta"]
                            meta["batch_tag"] = batch_tag
                            meta["timestamp"] = datetime.now().isoformat()
                            # 存储原文到 metadata（检索时可直接返回片段）
                            meta["text"] = text_items[i]["content"][:CHUNK_SIZE + 100]
                            batch_vectors.append(vec.tolist())
                            batch_tags.append(batch_tag)
                            batch_metas.append(meta)
                            state["inserted_chunks"] += 1
                
                # 图像 embedding（仅 DashScope 支持，vLLM 跳过）
                if image_items and embedding_source == "dashscope":
                    _add_log(f"🖼️ {fname}: {len(image_items)} 页视觉提取（DashScope）")
                    # TODO: 图像 embedding 走 DashScope multimodal API
                
                # 定时 flush
                if len(batch_vectors) >= FLUSH_INTERVAL:
                    col.insert([batch_vectors, batch_tags, batch_metas])
                    col.flush()
                    _add_log(f"💾 已入库 {len(batch_vectors)} 条 (累计 {state['inserted_chunks']})")
                    batch_vectors, batch_tags, batch_metas = [], [], []
                
                processed_set.add(fname)
                
            except Exception as e:
                _add_log(f"❌ 处理失败 {fname}: {e}", "ERROR")
                state["failed_files"].append(fname)
            
            state["processed"] = fidx + 1
            
            # 定期保存进度
            if (fidx + 1) % 10 == 0:
                with open(progress_file, 'w') as f:
                    json.dump(list(processed_set), f)
    
    # 最终 flush
    if batch_vectors:
        col.insert([batch_vectors, batch_tags, batch_metas])
        col.flush()
    
    # 保存进度
    with open(progress_file, 'w') as f:
        json.dump(list(processed_set), f)
    
    elapsed = time.time() - state["start_time"]
    _add_log(f"{'='*50}")
    _add_log(f"🎉 入库完成！")
    _add_log(f"  耗时: {elapsed:.0f}s")
    _add_log(f"  文件: {state['processed'] - len(state['failed_files'])}/{state['total_files']}")
    _add_log(f"  向量: {state['inserted_chunks']} 条")
    _add_log(f"  失败: {len(state['failed_files'])} 个")
    _add_log(f"  Collection: {col.num_entities} 条总计")
    if state["failed_files"]:
        _add_log(f"  失败文件: {', '.join(state['failed_files'][:10])}")
    _add_log(f"{'='*50}")
    
    state["running"] = False

# ==================== Gradio UI ====================

def build_ui():
    with gr.Blocks(
        title="SIQ 知识库入库系统",
    ) as app:
        
        # ===== Header =====
        gr.HTML("""
        <div class="main-header">
            <h1>🏛️ SIQ 知识库入库系统</h1>
            <p>全格式文档 → Milvus 向量库 | PDF / DOCX / MD / TXT | 法规条款感知切片</p>
        </div>
        """)
        
        with gr.Row():
            with gr.Column(scale=2):
                # ===== Collection 管理 =====
                gr.Markdown("### 📦 Collection 管理")
                with gr.Row():
                    coll_name_input = gr.Textbox(
                        label="Collection 名称", placeholder="输入新名称或选择已有...",
                        scale=3
                    )
                    coll_desc_input = gr.Textbox(
                        label="描述", placeholder="可选描述...",
                        scale=2
                    )
                
                with gr.Row():
                    refresh_btn = gr.Button("🔄 刷新列表", size="sm", variant="secondary")
                    create_btn = gr.Button("➕ 新建", size="sm", variant="primary")
                    drop_btn = gr.Button("🗑️ 删除", size="sm", variant="stop")
                
                coll_table = gr.Dataframe(
                    headers=["Collection", "描述", "向量数", "维度"],
                    label="",
                    interactive=False,
                    max_height=200,
                )
                
                # ===== 入库参数 =====
                gr.Markdown("### ⚙️ 入库参数")
                with gr.Row():
                    data_path_input = gr.Textbox(
                        label="文档目录", scale=2,
                        value="/home/maoyd/Desktop/knowledge/",
                        placeholder="/path/to/documents/"
                    )
                    batch_tag_input = gr.Textbox(
                        label="批次标签", scale=1,
                        value=f"ingest_{datetime.now().strftime('%m%d_%H%M')}",
                        placeholder="如: ingest_0415"
                    )
                
                with gr.Row():
                    format_group = gr.CheckboxGroup(
                        choices=["PDF", "DOCX", "Markdown", "TXT"],
                        value=["PDF", "DOCX", "Markdown", "TXT"],
                        label="文件格式",
                    )
                    embedding_radio = gr.Radio(
                        choices=["本地 vLLM", "DashScope 云端"],
                        value="本地 vLLM",
                        label="Embedding 引擎",
                    )
                    reset_checkbox = gr.Checkbox(
                        label="重置 Collection（清空后重新入库）",
                        value=False,
                    )
                
                with gr.Row():
                    select_coll_btn = gr.Button("📌 选择此 Collection 入库", variant="primary", size="lg")
                    ingest_btn = gr.Button("🚀 开始入库", variant="primary", size="lg")
                    cancel_btn = gr.Button("⏹️ 取消", variant="stop", size="lg")
                
                # ===== 检索测试 =====
                gr.Markdown("### 🔍 检索测试")
                with gr.Row():
                    test_coll_input = gr.Dropdown(
                        label="Collection",
                        choices=[],
                        interactive=True,
                        scale=1,
                    )
                    test_query_input = gr.Textbox(
                        label="检索 Query", placeholder="如：私募投资基金监督管理条例",
                        scale=2,
                    )
                    test_engine_radio = gr.Radio(
                        choices=["本地 vLLM", "DashScope 云端"],
                        value="本地 vLLM",
                        label="引擎",
                        scale=1,
                    )
                with gr.Row():
                    test_btn = gr.Button("🔍 检索测试", variant="secondary")
                    test_k_slider = gr.Slider(1, 30, value=10, step=1, label="Top-K")
                test_results = gr.Dataframe(
                    headers=["Rank", "Score", "文件", "片段预览"],
                    label="",
                    interactive=False,
                    max_height=350,
                )
            
            with gr.Column(scale=1):
                # ===== 实时状态 =====
                gr.Markdown("### 📊 实时状态")
                with gr.Row():
                    stat_total = gr.Number(label="总文件", value=0, interactive=False)
                    stat_done = gr.Number(label="已处理", value=0, interactive=False)
                    stat_chunks = gr.Number(label="入库向量", value=0, interactive=False)
                    stat_failed = gr.Number(label="失败", value=0, interactive=False)
                
                current_file_display = gr.Textbox(label="当前文件", value="空闲", interactive=False)
                
                # ===== 运行日志 =====
                gr.Markdown("### 📋 运行日志")
                log_display = gr.Textbox(
                    label="",
                    lines=35,
                    interactive=False,
                    value="等待操作...",
                )
                
                # ===== 配置信息 =====
                gr.Markdown("### ℹ️ 配置信息")
                config_display = gr.JSON(value={
                    "milvus": f"{MILVUS_HOST}:{MILVUS_PORT}",
                    "vector_dim": VECTOR_DIM,
                    "chunk_size": CHUNK_SIZE,
                    "chunk_overlap": CHUNK_OVERLAP,
                    "vllm": f"{VLLM_BASE} ({VLLM_MODEL})",
                    "vllm_dim": "auto",
                    "dashscope_model": DASHSCOPE_MODEL,
                    "concurrency": MAX_CONCURRENT,
                    "metric": "IP (cosine)",
                    "index": "HNSW (M=32, efConstruction=256)",
                    "article_aware_split": True,
                }, label="")
        
        # ===== Toast 状态 =====
        toast = gr.Textbox(visible=False, label="toast")
        
        # ==================== Event Handlers ====================
        
        def refresh_collections():
            cols = list_collections()
            rows = []
            for name, count in cols.items():
                info = get_collection_info(name)
                desc = info["description"] if info else ""
                dim = info["vector_dim"] if info else "?"
                rows.append([name, desc, count, dim])
            choices = [name for name, _ in cols.items()]
            return rows, gr.update(choices=choices, value=choices[0] if choices else None)
        
        def on_create_collection(name, desc):
            if not name.strip():
                return gr.Error("Collection 名称不能为空")
            name = name.strip().lower().replace(" ", "_")
            if create_collection(name, desc):
                return f"✅ 已创建: {name}"
            return f"⚠️ 已存在: {name}"
        
        def on_drop_collection(name):
            if not name.strip():
                return gr.Error("请输入 Collection 名称")
            name = name.strip()
            milvus_connect()
            if utility.has_collection(name):
                utility.drop_collection(name)
                # 清理进度文件
                pf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   f".progress_{name}.json")
                if os.path.exists(pf):
                    os.remove(pf)
                return f"🗑️ 已删除: {name}"
            return f"⚠️ 不存在: {name}"
        
        def select_collection(name):
            """点击 Collection 表格行 → 填入名称和描述"""
            return name
        
        def start_ingest(coll_name, path, tag, formats, engine, reset, progress=gr.Progress()):
            if _ingest_state["running"]:
                return "⚠️ 入库正在进行中，请等待或取消"
            if not coll_name.strip():
                return gr.Error("请选择或输入 Collection 名称")
            if not os.path.isdir(path):
                return gr.Error(f"目录不存在: {path}")
            if not formats:
                return gr.Error("请至少选择一种文件格式")
            
            emb_src = "vllm" if engine == "本地 vLLM" else "dashscope"
            if emb_src == "dashscope" and not DASHSCOPE_API_KEY:
                return gr.Error("DashScope API Key 未配置（设置环境变量 DASHSCOPE_API_KEY）")
            
            # 在后台线程运行 async 任务
            import threading
            loop = asyncio.new_event_loop()
            
            def run():
                asyncio.set_event_loop(loop)
                loop.run_until_complete(run_ingest(
                    coll_name.strip(), path.strip(), tag.strip(),
                    emb_src, formats, reset, progress
                ))
            
            t = threading.Thread(target=run, daemon=True)
            t.start()
            return f"🚀 入库已启动: {coll_name.strip()}"
        
        def cancel_ingest():
            _ingest_state["cancelled"] = True
            return "⏹️ 已发送取消信号"
        
        def update_status():
            """定时刷新状态"""
            s = _ingest_state
            lines = s["log_lines"][-30:]
            return (
                s["total_files"],
                s["processed"],
                s["inserted_chunks"],
                len(s["failed_files"]),
                s["current_file"] or ("空闲" if not s["running"] else "..."),
                "\n".join(lines) if lines else "等待操作...",
            )
        
        async def run_test(query, collection, k, engine):
            if not query.strip():
                return []
            milvus_connect()
            if not utility.has_collection(collection):
                return [[0, 0, "Collection 不存在", ""]]
            
            col = Collection(collection)
            col.load()
            
            # 获取 query embedding
            emb_src = "vllm" if engine == "本地 vLLM" else "dashscope"
            async with aiohttp.ClientSession() as session:
                semaphore = asyncio.Semaphore(1)
                if emb_src == "vllm":
                    vecs = await embed_vllm_batch(session, [query], semaphore)
                else:
                    vecs = await embed_dashscope_batch(session, [query], semaphore)
            
            vec = vecs[0]
            if vec is None:
                return [[0, 0, "Embedding 失败", ""]]
            
            results = col.search(
                data=[vec.tolist()],
                anns_field="vector",
                param={"metric_type": "IP", "params": {"ef": 256}},
                limit=k,
                output_fields=["metadata"],
            )
            
            rows = []
            for i, hit in enumerate(results[0]):
                meta = hit.entity.get("metadata", {})
                source = meta.get("source", "?")
                text = meta.get("text", "")
                # 取前 120 字预览
                preview = text[:120].replace("\n", " ").strip()
                if len(text) > 120:
                    preview += "..."
                rows.append([i + 1, f"{hit.score:.4f}", source, preview])
            return rows
        
        def get_config_info():
            """获取当前 vLLM 模型实际维度"""
            import requests
            try:
                r = requests.get(f"{VLLM_BASE}/models", timeout=5)
                models = r.json().get("data", [])
                model_info = [{"name": m["id"]} for m in models]
            except Exception:
                model_info = [{"name": "连接失败"}]
            
            try:
                r = requests.post(f"{VLLM_BASE}/embeddings",
                    json={"model": VLLM_MODEL, "input": ["test"]}, timeout=10)
                dim = len(r.json()["data"][0]["embedding"])
            except Exception:
                dim = "?"
            
            return {
                "milvus": f"{MILVUS_HOST}:{MILVUS_PORT}",
                "vector_dim": VECTOR_DIM,
                "vllm_endpoint": VLLM_BASE,
                "vllm_model": VLLM_MODEL,
                "vllm_actual_dim": dim,
                "vllm_dim_match": "✅" if str(dim) == str(VECTOR_DIM) else "❌",
                "dashscope_model": DASHSCOPE_MODEL,
                "dashscope_key": "已配置" if DASHSCOPE_API_KEY else "未配置",
                "chunk_size": CHUNK_SIZE,
                "chunk_overlap": CHUNK_OVERLAP,
                "article_aware": True,
                "max_concurrent": MAX_CONCURRENT,
                "metric": "IP (cosine approx.)",
                "hnsw": "M=32, efConstruction=256",
                "title_prefix": True,
                "metadata_stores_text": True,
                "invert_index_tag": True,
            }
        
        # ===== Bind Events =====
        refresh_btn.click(
            fn=refresh_collections,
            outputs=[coll_table, test_coll_input]
        )
        
        create_btn.click(
            fn=on_create_collection,
            inputs=[coll_name_input, coll_desc_input],
            outputs=[toast]
        )
        
        drop_btn.click(
            fn=on_drop_collection,
            inputs=[coll_name_input],
            outputs=[toast]
        )
        
        def on_table_select(evt: gr.SelectData):
            return evt.value[0]
        
        coll_table.select(
            fn=on_table_select,
            outputs=[coll_name_input]
        )
        
        select_coll_btn.click(
            fn=lambda name: name,
            inputs=[coll_name_input],
            outputs=[toast]
        )
        
        ingest_btn.click(
            fn=start_ingest,
            inputs=[coll_name_input, data_path_input, batch_tag_input,
                    format_group, embedding_radio, reset_checkbox],
            outputs=[toast],
        )
        
        cancel_btn.click(
            fn=cancel_ingest,
            outputs=[toast],
        )
        
        test_btn.click(
            fn=run_test,
            inputs=[test_query_input, test_coll_input, test_k_slider, test_engine_radio],
            outputs=[test_results],
        )
        
        # 定时刷新状态
        timer = gr.Timer(value=1.0, active=True)
        timer.tick(
            fn=update_status,
            outputs=[stat_total, stat_done, stat_chunks, stat_failed,
                     current_file_display, log_display]
        )
        
        app.load(
            fn=refresh_collections,
            outputs=[coll_table, test_coll_input]
        )
        
        config_refresh_btn = gr.Button("刷新配置", size="sm", variant="secondary")
        config_refresh_btn.click(
            fn=get_config_info,
            outputs=[config_display]
        )
    
    return app

# ==================== 主入口 ====================

if __name__ == "__main__":
    # 清理可能导致 httpx 报错的 socks 代理变量
    for k in ["ALL_PROXY", "all_proxy"]:
        os.environ.pop(k, None)
    
    app = build_ui()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        inbrowser=True,
        show_error=True,
        theme=gr.themes.Soft(
            primary_hue="indigo",
            secondary_hue="slate",
            neutral_hue="gray",
        ),
    )
