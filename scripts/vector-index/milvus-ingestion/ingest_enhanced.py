#!/usr/bin/env python3
"""
SIQ RAG Knowledge Base - 增强入库工具
支持：新建/重置Collection、选择模型（vllm本地/阿里云）
"""

import os
for _k in ("all_proxy", "ALL_PROXY", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

import sys, json, re, time, glob, hashlib
from pathlib import Path
from typing import List, Dict, Optional
import requests
import gradio as gr
from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType

# ═══════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════

MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = "19530"
VLLM_EMBED_URL = "http://127.0.0.1:8000/v1/embeddings"
VLLM_EMBED_MODEL = "Qwen3-VL-Embedding-2B"
DASHSCOPE_EMBED_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
DASHSCOPE_EMBED_MODEL = "qwen3-vl-embedding"
EMBED_DIM = 1024

ROLE_REGISTRY = {
    "ic_chairman":             {"desc": "投委会主席",     "icon": "👔"},
    "ic_finance_auditor":      {"desc": "财务审计官",   "icon": "💰"},
    "ic_sector_expert":        {"desc": "行业专家",     "icon": "🔬"},
    "ic_legal_scanner":        {"desc": "法务合规专家", "icon": "⚖️"},
    "ic_strategist":           {"desc": "战略专家",     "icon": "🌐"},
    "ic_risk_controller":      {"desc": "风险管理官",   "icon": "⚠️"},
    "ic_master_coordinator":   {"desc": "投委会秘书",   "icon": "📋"},
    "ic_collaboration_shared": {"desc": "协同共享工作区","icon": "🤝"},
    "ic_archive_sop":          {"desc": "机构历史案例库","icon": "📚"},
}

CHUNK_SIZE = 480
CHUNK_OVERLAP = 80
LAW_CHUNK_RE = re.compile(r'(?:第[一二三四五六七八九十百千零\d]+条|Article\s+\d+)', re.IGNORECASE)
VLLM_BATCH_SIZE = 32
DASHSCOPE_BATCH_SIZE = 4

# ═══════════════════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════════════════

def milvus_connect():
    if not connections.has_connection("default"):
        connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)

def embed_vllm(texts: List[str]) -> List[List[float]]:
    results = []
    for i in range(0, len(texts), VLLM_BATCH_SIZE):
        chunk = texts[i:i + VLLM_BATCH_SIZE]
        resp = requests.post(VLLM_EMBED_URL, json={
            "model": VLLM_EMBED_MODEL,
            "input": chunk,
        }, timeout=120)
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda x: x["index"])
        results.extend([d["embedding"] for d in data])
    return results

def embed_dashscope(texts: List[str], api_key: str) -> List[List[float]]:
    results = []
    headers = {"Authorization": f"Bearer {api_key}"}
    for i in range(0, len(texts), DASHSCOPE_BATCH_SIZE):
        chunk = texts[i:i + DASHSCOPE_BATCH_SIZE]
        payload = {
            "model": DASHSCOPE_EMBED_MODEL,
            "input": {"contents": [{"type": "text", "text": t} for t in chunk]}
        }
        resp = requests.post(DASHSCOPE_EMBED_URL, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()["output"]["embeddings"]
        results.extend([d["embedding"] for d in data])
    return results

def smart_chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    chunks = []
    start = 0
    text_len = len(text)
    
    while start < text_len:
        end = min(start + size, text_len)
        if end < text_len:
            for sep in ['\n\n', '\n', '。', '；', ' ']:
                pos = text.rfind(sep, start, end)
                if pos > start + size // 2:
                    end = pos + len(sep)
                    break
        chunks.append(text[start:end].strip())
        start = end - overlap if end < text_len else end
    
    return [c for c in chunks if len(c) > 20]

def create_collection(name: str, dim: int = EMBED_DIM) -> str:
    try:
        milvus_connect()
        if utility.has_collection(name):
            return f"⚠️ Collection '{name}' 已存在"
        
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="project_tag", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="metadata", dtype=DataType.JSON),
        ]
        schema = CollectionSchema(fields, description=f"SIQ KB: {name}")
        collection = Collection(name, schema)
        
        # 创建索引
        index_params = {
            "metric_type": "IP",
            "index_type": "HNSW",
            "params": {"M": 32, "efConstruction": 256}
        }
        collection.create_index("vector", index_params)
        return f"✅ Collection '{name}' 创建成功"
    except Exception as e:
        return f"❌ 创建失败: {e}"

def reset_collection(name: str) -> str:
    try:
        milvus_connect()
        if utility.has_collection(name):
            utility.drop_collection(name)
        return create_collection(name)
    except Exception as e:
        return f"❌ 重置失败: {e}"

def ingest_documents(collection_name: str, doc_dir: str, exts: List[str], 
                     embed_backend: str, api_key: str,
                     progress_callback=None) -> str:
    try:
        milvus_connect()
        
        files = []
        for ext in exts:
            files.extend(Path(doc_dir).glob(f"**/*.{ext}"))
        
        if not files:
            return f"❌ 未找到 {exts} 文件"
        
        if not utility.has_collection(collection_name):
            return f"❌ Collection '{collection_name}' 不存在，请先创建"
        
        collection = Collection(collection_name)
        collection.load()
        
        # 获取当前最大ID
        try:
            stats = collection.query(expr="id >= 0", output_fields=["id"], limit=100000)
            start_id = max([s["id"] for s in stats], default=-1) + 1
        except:
            start_id = 0
        
        total_inserted = 0
        log_lines = []
        
        for file_path in files:
            try:
                text = file_path.read_text(encoding='utf-8')
                if not text.strip():
                    continue
                
                chunks = smart_chunk(text, CHUNK_SIZE, CHUNK_OVERLAP)
                if not chunks:
                    continue
                
                # 添加标题前缀
                title_prefix = file_path.stem[:80]
                titled_chunks = [f"{title_prefix}\n{c}" for c in chunks]
                
                # 选择embedding后端
                if embed_backend == "vllm (本地)":
                    vectors = embed_vllm(titled_chunks)
                else:
                    if not api_key:
                        return "❌ 使用DashScope需要提供API Key"
                    vectors = embed_dashscope(titled_chunks, api_key)
                
                entities = [
                    list(range(start_id, start_id + len(chunks))),
                    vectors,
                    ["default"] * len(chunks),
                    [json.dumps({
                        "source": file_path.name,
                        "text": c,
                        "chunk_index": i + 1,
                        "total_chunks": len(chunks),
                    }) for i, c in enumerate(chunks)]
                ]
                
                collection.insert(entities)
                start_id += len(chunks)
                total_inserted += len(chunks)
                
                msg = f"✅ {file_path.name}: {len(chunks)} chunks"
                log_lines.append(msg)
                if progress_callback:
                    progress_callback(msg)
                
            except Exception as e:
                err_msg = f"❌ {file_path.name}: {e}"
                log_lines.append(err_msg)
                if progress_callback:
                    progress_callback(err_msg)
        
        collection.flush()
        return f"✅ 完成！共插入 {total_inserted} 个 chunks\n" + "\n".join(log_lines[-20:])
        
    except Exception as e:
        return f"❌ 错误: {e}"

# ═══════════════════════════════════════════════════════════════════════════
# Gradio UI
# ═══════════════════════════════════════════════════════════════════════════

def build_ui():
    with gr.Blocks(title="SIQ 知识库入库工具") as demo:
        gr.Markdown("# 📦 SIQ 知识库入库工具")
        
        # Collection 管理
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Collection 管理")
                new_coll_name = gr.Textbox(label="新建 Collection 名称", placeholder="输入新名称")
                create_btn = gr.Button("➕ 创建")
                create_result = gr.Textbox(label="创建结果", interactive=False)
                
                reset_coll = gr.Dropdown(choices=list(ROLE_REGISTRY.keys()), label="重置 Collection")
                reset_btn = gr.Button("🔄 重置（清空数据）", variant="stop")
                reset_result = gr.Textbox(label="重置结果", interactive=False)
        
        # 入库配置
        with gr.Row():
            with gr.Column():
                gr.Markdown("### 入库配置")
                coll_dropdown = gr.Dropdown(
                    choices=list(ROLE_REGISTRY.keys()),
                    value="ic_legal_scanner",
                    label="目标 Collection"
                )
                doc_dir = gr.Textbox(
                    label="文档目录",
                    value="/home/maoyd/Desktop/knowledge/legal_scanner",
                    info="包含 .md 文件的目录路径"
                )
                ext_filter = gr.CheckboxGroup(
                    choices=["md", "txt", "pdf", "docx"],
                    value=["md"],
                    label="文件格式"
                )
            
            with gr.Column():
                gr.Markdown("### Embedding 配置")
                embed_backend = gr.Radio(
                    choices=["vllm (本地)", "DashScope (阿里云)"],
                    value="vllm (本地)",
                    label="Embedding 后端"
                )
                api_key = gr.Textbox(
                    label="DashScope API Key",
                    type="password",
                    visible=False,
                    info="选择DashScope时填写"
                )
                
                def toggle_api_key(backend):
                    return gr.Textbox(visible=(backend == "DashScope (阿里云)"))
                
                embed_backend.change(fn=toggle_api_key, inputs=[embed_backend], outputs=[api_key])
                
                ingest_btn = gr.Button("🚀 开始入库", variant="primary")
        
        # 状态输出
        status_output = gr.Textbox(
            label="入库状态",
            lines=20,
            interactive=False
        )
        
        # 事件绑定
        create_btn.click(
            fn=lambda name: create_collection(name) if name else "❌ 请输入名称",
            inputs=[new_coll_name],
            outputs=[create_result]
        )
        
        reset_btn.click(
            fn=reset_collection,
            inputs=[reset_coll],
            outputs=[reset_result]
        )
        
        def on_ingest(coll, doc_dir, exts, backend, key):
            if not doc_dir or not Path(doc_dir).exists():
                return "❌ 目录不存在"
            return ingest_documents(coll, doc_dir, exts or ["md"], backend, key)
        
        ingest_btn.click(
            fn=on_ingest,
            inputs=[coll_dropdown, doc_dir, ext_filter, embed_backend, api_key],
            outputs=[status_output]
        )
    
    return demo

if __name__ == "__main__":
    app = build_ui()
    app.launch(server_name="0.0.0.0", server_port=7860)
