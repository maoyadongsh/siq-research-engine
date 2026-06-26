#!/usr/bin/env python3
"""
SIQ RAG Knowledge Base - 全格式异步入库工具 (Gradio版)
参考: async_ingestor.py
特性: 异步并发、全格式支持、断点续传、图片提取、向量归一化
"""

import os
for _k in ("all_proxy", "ALL_PROXY", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

import sys
import json
import asyncio
import base64
import logging
import aiohttp
import numpy as np
from datetime import datetime
from typing import List, Dict, Optional, Set
from pathlib import Path

import fitz  # PyMuPDF
from docx import Document
import gradio as gr
from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType

# ==================== 配置 ====================
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = "19530"
VLLM_EMBED_URL = "http://127.0.0.1:8000/v1/embeddings"
VLLM_EMBED_MODEL = "Qwen3-VL-Embedding-2B"
DASHSCOPE_EMBED_MODEL = "qwen3-vl-embedding"
VECTOR_DIM = 1024
DASHSCOPE_API_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"

# 极速入库参数
CONCURRENT_REQUESTS = 8
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
TIMEOUT_API = 45
VLLM_BATCH_SIZE = 32

logging.basicConfig(level=logging.ERROR)

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


class AsyncKnowledgeIngestor:
    def __init__(self, collection_name: str, reset: bool = False):
        self.collection_name = collection_name
        self.progress_file = f".progress_{collection_name}.json"
        self._init_milvus(reset)
    
    def _init_milvus(self, reset: bool):
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
        
        if reset and utility.has_collection(self.collection_name):
            utility.drop_collection(self.collection_name)
            if os.path.exists(self.progress_file):
                os.remove(self.progress_file)
        
        if not utility.has_collection(self.collection_name):
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
                FieldSchema(name="batch_tag", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="metadata", dtype=DataType.JSON)
            ]
            col = Collection(self.collection_name, CollectionSchema(fields))
            col.create_index("vector", {
                "metric_type": "IP",
                "index_type": "HNSW",
                "params": {"M": 16, "efConstruction": 128}
            })
        
        self.collection = Collection(self.collection_name)
        self.collection.load()
    
    async def _fetch_embedding_vllm(self, texts: List[str]) -> List[Optional[List[float]]]:
        """vllm批量embedding"""
        results = []
        for i in range(0, len(texts), VLLM_BATCH_SIZE):
            chunk = texts[i:i + VLLM_BATCH_SIZE]
            try:
                resp = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: requests.post(VLLM_EMBED_URL, json={
                        "model": VLLM_EMBED_MODEL,
                        "input": chunk,
                    }, timeout=120)
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                data.sort(key=lambda x: x["index"])
                for d in data:
                    vec = np.array(d["embedding"])
                    vec = vec / (np.linalg.norm(vec) + 1e-12)
                    results.append(vec.tolist())
            except Exception as e:
                for _ in chunk:
                    results.append(None)
        return results
    
    async def _fetch_embedding_dashscope(
        self, session: aiohttp.ClientSession, items: List[Dict], semaphore: asyncio.Semaphore
    ) -> List[Optional[List[float]]]:
        """DashScope异步embedding"""
        headers = {
            "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
            "Content-Type": "application/json"
        }
        
        async def fetch_single(item: Dict) -> Optional[List[float]]:
            if item["type"] == "image":
                content = {"text": "visual content", "image": item["image"]}
            else:
                content = {"text": item["content"][:2000]}  # 限制长度
            
            payload = {
                "model": DASHSCOPE_EMBED_MODEL,
                "input": {"contents": [content]},
                "parameters": {"dimension": VECTOR_DIM}
            }
            
            async with semaphore:
                for attempt in range(3):
                    try:
                        async with session.post(
                            DASHSCOPE_API_ENDPOINT,
                            headers=headers,
                            json=payload,
                            timeout=TIMEOUT_API
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                vec = data["output"]["embeddings"][0]["embedding"]
                                arr = np.array(vec)
                                return (arr / (np.linalg.norm(arr) + 1e-12)).tolist()
                            elif resp.status == 429:
                                await asyncio.sleep(2 ** attempt)
                            else:
                                await asyncio.sleep(1)
                    except Exception:
                        await asyncio.sleep(1)
                return None
        
        tasks = [fetch_single(item) for item in items]
        return await asyncio.gather(*tasks)
    
    def _split_text(self, text: str, fname: str, meta_extra: Dict = None) -> List[Dict]:
        """智能文本分块"""
        items = []
        clean_text = " ".join(text.split())
        for i in range(0, len(clean_text), CHUNK_SIZE - CHUNK_OVERLAP):
            chunk = clean_text[i:i + CHUNK_SIZE]
            meta = {"source": fname, "type": "text_chunk"}
            if meta_extra:
                meta.update(meta_extra)
            items.append({"type": "text", "content": chunk, "meta": meta})
        return items
    
    def _parse_pdf(self, path: str) -> List[Dict]:
        """解析PDF（文本+图片）"""
        items = []
        doc = fitz.open(path)
        fname = os.path.basename(path)
        
        for page_idx, page in enumerate(doc):
            # 提取文本
            text = page.get_text("text").strip()
            if len(text) > 10:
                items.extend(self._split_text(text, fname, {"page": page_idx + 1}))
            
            # 提取图片（针对扫描件/图表页）
            if len(text) < 100:
                try:
                    pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2))
                    img_b64 = base64.b64encode(pix.tobytes("jpg")).decode()
                    items.append({
                        "type": "image",
                        "image": f"data:image/jpeg;base64,{img_b64}",
                        "meta": {"page": page_idx + 1, "source": fname, "is_visual": True}
                    })
                except Exception:
                    pass
        
        doc.close()
        return items
    
    def _parse_docx(self, path: str) -> List[Dict]:
        """解析Word文档"""
        doc = Document(path)
        full_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        return self._split_text(full_text, os.path.basename(path), {"format": "docx"})
    
    def _parse_plain_text(self, path: str) -> List[Dict]:
        """解析纯文本"""
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return self._split_text(content, os.path.basename(path), {"format": Path(path).suffix[1:]})
    
    async def process_file(
        self, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore,
        file_path: str, batch_tag: str, embed_backend: str
    ) -> int:
        """处理单个文件"""
        ext = Path(file_path).suffix.lower()
        
        if ext == '.pdf':
            items = self._parse_pdf(file_path)
        elif ext == '.docx':
            items = self._parse_docx(file_path)
        elif ext in ['.md', '.txt']:
            items = self._parse_plain_text(file_path)
        else:
            return 0
        
        if not items:
            return 0
        
        # 获取embedding
        if embed_backend == "vllm (本地)":
            texts = [it.get("content", "") for it in items if it["type"] == "text"]
            results = await self._fetch_embedding_vllm(texts)
        else:
            results = await self._fetch_embedding_dashscope(session, items, semaphore)
        
        # 准备数据
        vectors, metas = [], []
        for i, vec in enumerate(results):
            if vec:
                vectors.append(vec)
                metas.append(items[i]["meta"])
        
        if vectors:
            self.collection.insert([vectors, [batch_tag] * len(vectors), metas])
            return len(vectors)
        return 0
    
    async def run(
        self, data_path: str, batch_tag: str, embed_backend: str,
        progress_callback=None
    ) -> str:
        """主运行函数"""
        # 扫描文件
        extensions = ['*.pdf', '*.docx', '*.md', '*.txt']
        files = []
        for ext in extensions:
            files.extend(list(Path(data_path).glob(f"**/{ext}")))
        
        files = sorted([str(f) for f in files])
        processed = self._load_progress()
        pending = [f for f in files if os.path.basename(f) not in processed]
        
        if not pending:
            return "⚠️ 当前目录无新文档需处理"
        
        total_inserted = 0
        semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
        
        async with aiohttp.ClientSession() as session:
            for idx, f_path in enumerate(pending):
                fname = os.path.basename(f_path)
                try:
                    inserted = await self.process_file(
                        session, semaphore, f_path, batch_tag, embed_backend
                    )
                    if inserted > 0:
                        processed.add(fname)
                        self._save_progress(processed)
                        total_inserted += inserted
                        msg = f"✅ [{idx+1}/{len(pending)}] {fname[:30]}... ({inserted}条)"
                    else:
                        msg = f"⚠️ [{idx+1}/{len(pending)}] {fname[:30]}... (跳过)"
                except Exception as e:
                    msg = f"❌ [{idx+1}/{len(pending)}] {fname[:30]}... ({e})"
                
                if progress_callback:
                    progress_callback(msg)
        
        self.collection.flush()
        return f"🎉 完成！共处理 {len(pending)} 个文件，插入 {total_inserted} 条记录\n当前Collection总实体数: {self.collection.num_entities}"
    
    def _load_progress(self) -> Set[str]:
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r') as f:
                return set(json.load(f))
        return set()
    
    def _save_progress(self, processed: Set[str]):
        with open(self.progress_file, 'w') as f:
            json.dump(list(processed), f)


# ==================== Gradio UI ====================

def build_ui():
    with gr.Blocks(title="SIQ 全格式知识库入库系统") as demo:
        gr.Markdown("# 📦 SIQ 全格式知识库入库系统 V5.0")
        gr.Markdown("支持 PDF/Word/Markdown/TXT，异步并发，断点续传")
        
        # 状态存储
        ingestor_state = gr.State(None)
        
        with gr.Row():
            # 左列：配置
            with gr.Column(scale=1):
                gr.Markdown("### ⚙️ 配置")
                
                coll_dropdown = gr.Dropdown(
                    choices=list(ROLE_REGISTRY.keys()),
                    value="ic_legal_scanner",
                    label="目标 Collection"
                )
                
                doc_dir = gr.Textbox(
                    label="文档目录",
                    value="/home/maoyd/Desktop/knowledge/legal_scanner",
                    placeholder="输入绝对路径"
                )
                
                batch_tag = gr.Textbox(
                    label="批次标签",
                    value=lambda: f"ingest_{datetime.now().strftime('%m%d_%H%M')}",
                    info="用于区分不同批次的数据"
                )
                
                embed_backend = gr.Radio(
                    choices=["vllm (本地)", "DashScope (阿里云)"],
                    value="vllm (本地)",
                    label="Embedding 后端"
                )
                
                reset_check = gr.Checkbox(
                    label="⚠️ 重置 Collection（清空已有数据）",
                    value=False
                )
                
                with gr.Accordion("高级选项", open=False):
                    ext_filter = gr.CheckboxGroup(
                        choices=["pdf", "docx", "md", "txt"],
                        value=["pdf", "docx", "md", "txt"],
                        label="文件格式筛选"
                    )
                
                start_btn = gr.Button("🚀 开始入库", variant="primary", size="lg")
        
            # 右列：日志
            with gr.Column(scale=2):
                gr.Markdown("### 📋 运行日志")
                log_output = gr.Textbox(
                    label="",
                    lines=25,
                    interactive=False,
                    max_lines=100
                )
                
                stats_output = gr.Textbox(
                    label="统计信息",
                    interactive=False
                )
        
        # 事件处理
        async def on_start(coll, doc_dir, tag, backend, reset, exts):
            if not doc_dir or not Path(doc_dir).exists():
                return "❌ 目录不存在，请检查路径", ""
            
            if not exts:
                return "❌ 请至少选择一种文件格式", ""
            
            # 初始化 ingestor
            ingestor = AsyncKnowledgeIngestor(coll, reset)
            
            logs = []
            def callback(msg):
                logs.append(msg)
                return "\n".join(logs)
            
            # 运行入库
            result = await ingestor.run(doc_dir, tag, backend, callback)
            
            final_log = "\n".join(logs) + "\n" + "="*50 + "\n" + result
            return final_log, result
        
        start_btn.click(
            fn=on_start,
            inputs=[coll_dropdown, doc_dir, batch_tag, embed_backend, reset_check, ext_filter],
            outputs=[log_output, stats_output]
        )
    
    return demo


if __name__ == "__main__":
    import requests  # 确保导入
    app = build_ui()
    app.launch(server_name="0.0.0.0", server_port=7860)
