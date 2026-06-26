#!/usr/bin/env python3
"""
SIQ RAG Knowledge Base - 精简入库工具
仅保留核心的知识入库功能
"""

import os
for _k in ("all_proxy", "ALL_PROXY", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

import sys, json, re, time, glob
from pathlib import Path
from typing import List, Dict
import requests
import gradio as gr
from pymilvus import connections, Collection, utility

# ═══════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════

MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = "19530"
VLLM_EMBED_URL = "http://127.0.0.1:8000/v1/embeddings"
VLLM_EMBED_MODEL = "Qwen3-VL-Embedding-2B"
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

# ═══════════════════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════════════════

def milvus_connect():
    """连接 Milvus"""
    if not connections.has_connection("default"):
        connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)

def embed_vllm(texts: List[str]) -> List[List[float]]:
    """vllm embedding"""
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

def smart_chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """智能分块"""
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

def ingest_documents(collection_name: str, doc_dir: str, exts: List[str], 
                     progress_callback=None) -> str:
    """文档入库核心函数"""
    try:
        milvus_connect()
        
        # 获取文件列表
        files = []
        for ext in exts:
            files.extend(Path(doc_dir).glob(f"**/*.{ext}"))
        
        if not files:
            return f"❌ 未找到 {exts} 文件"
        
        # 获取 collection
        if not utility.has_collection(collection_name):
            return f"❌ Collection '{collection_name}' 不存在"
        
        collection = Collection(collection_name)
        
        total_inserted = 0
        log_lines = []
        
        for file_path in files:
            try:
                # 读取文件
                text = file_path.read_text(encoding='utf-8')
                if not text.strip():
                    continue
                
                # 分块
                chunks = smart_chunk(text, CHUNK_SIZE, CHUNK_OVERLAP)
                if not chunks:
                    continue
                
                # 生成 embedding
                title_prefix = file_path.stem[:80]
                titled_chunks = [f"{title_prefix}\n{c}" for c in chunks]
                vectors = embed_vllm(titled_chunks)
                
                # 准备数据
                entities = [
                    list(range(total_inserted, total_inserted + len(chunks))),
                    vectors,
                    ["default"] * len(chunks),
                    [json.dumps({
                        "source": file_path.name,
                        "text": c,
                        "chunk_index": i + 1,
                        "total_chunks": len(chunks),
                    }) for i, c in enumerate(chunks)]
                ]
                
                # 插入
                collection.insert(entities)
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
        
        with gr.Row():
            with gr.Column():
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
                ingest_btn = gr.Button("🚀 开始入库", variant="primary")
            
            with gr.Column():
                status_output = gr.Textbox(
                    label="入库状态",
                    lines=15,
                    interactive=False
                )
        
        def on_ingest(coll, doc_dir, exts):
            if not doc_dir or not Path(doc_dir).exists():
                return "❌ 目录不存在"
            
            logs = []
            def callback(msg):
                logs.append(msg)
                return "\n".join(logs[-15:])
            
            result = ingest_documents(coll, doc_dir, exts or ["md"])
            return result
        
        ingest_btn.click(
            fn=on_ingest,
            inputs=[coll_dropdown, doc_dir, ext_filter],
            outputs=[status_output]
        )
    
    return demo

if __name__ == "__main__":
    app = build_ui()
    app.launch(server_name="0.0.0.0", server_port=7860)
