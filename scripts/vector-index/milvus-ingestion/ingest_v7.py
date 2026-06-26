#!/usr/bin/env python3
"""
SIQ RAG Knowledge Base - 全格式异步入库工具 V7.1
优化: 并发入库、统一 HTTP 客户端、Schema 复用、环境变量读取、Minimax 支持
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

import fitz
from docx import Document
import gradio as gr
from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType

# ==================== 配置 ====================
MILVUS_HOST = os.getenv("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")

INDEX_TYPE = "HNSW"
INDEX_PARAMS = {
    "metric_type": "L2",
    "index_type": INDEX_TYPE,
    "params": {"M": 32, "efConstruction": 256}
}

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

VLLM_EMBED_URL = os.getenv("VLLM_EMBED_URL", "http://127.0.0.1:8000/v1/embeddings")
VLLM_EMBED_MODEL = os.getenv("VLLM_EMBED_MODEL", "Qwen3-VL-Embedding-2B")
DASHSCOPE_EMBED_MODEL = os.getenv("DASHSCOPE_EMBED_MODEL", "qwen3-vl-embedding")
MINIMAX_EMBED_MODEL = os.getenv("MINIMAX_EMBED_MODEL", "embo-01")

VECTOR_DIM = int(os.getenv("VECTOR_DIM", "1024"))
DASHSCOPE_API_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
MINIMAX_API_ENDPOINT = "https://api.minimax.chat/v1/embeddings"

CONCURRENT_REQUESTS = 8
DEFAULT_CHUNK_SIZE = 700
DEFAULT_CHUNK_OVERLAP = 120
TIMEOUT_API = 45
VLLM_BATCH_SIZE = 32

logging.basicConfig(level=logging.ERROR)

ROLE_REGISTRY = {
    "ic_chairman": {"desc": "投委会主席", "icon": "👔"},
    "ic_finance_auditor": {"desc": "财务审计官", "icon": "💰"},
    "ic_sector_expert": {"desc": "行业专家", "icon": "🔬"},
    "ic_legal_scanner": {"desc": "法务合规专家", "icon": "⚖️"},
    "ic_strategist": {"desc": "战略专家", "icon": "🌐"},
    "ic_risk_controller": {"desc": "风险管理官", "icon": "⚠️"},
    "ic_master_coordinator": {"desc": "投委会秘书", "icon": "📋"},
    "ic_collaboration_shared": {"desc": "协同共享工作区", "icon": "🤝"},
    "ic_archive_sop": {"desc": "机构历史案例库", "icon": "📚"},
}

_DEFAULT_COLLECTIONS = list(ROLE_REGISTRY.keys())


def get_kb_schema(description: str) -> CollectionSchema:
    """返回统一的知识库 Schema"""
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
        FieldSchema(name="project_tag", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="metadata", dtype=DataType.JSON)
    ]
    return CollectionSchema(fields, description=description)


class AsyncKnowledgeIngestor:
    _pause_event = asyncio.Event()
    _pause_event.set()

    def __init__(self, collection_name: str, reset: bool = False):
        self.collection_name = collection_name
        self.role_desc = ROLE_REGISTRY.get(collection_name, {}).get("desc", collection_name)
        self.progress_file = f".progress_{self.collection_name}.json"
        self._processed_cache: Optional[Set[str]] = None
        self.chunk_size = DEFAULT_CHUNK_SIZE
        self.chunk_overlap = DEFAULT_CHUNK_OVERLAP
        self._init_milvus(reset)

    @classmethod
    def pause(cls):
        cls._pause_event.clear()

    @classmethod
    def resume(cls):
        cls._pause_event.set()

    def _init_milvus(self, reset: bool):
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)

        if reset and utility.has_collection(self.collection_name):
            utility.drop_collection(self.collection_name)
            if os.path.exists(self.progress_file):
                os.remove(self.progress_file)

        if not utility.has_collection(self.collection_name):
            schema = get_kb_schema(self.role_desc)
            collection = Collection(self.collection_name, schema)
            collection.create_index(field_name="vector", index_params=INDEX_PARAMS)
            collection.create_index(field_name="project_tag", index_params={"index_type": "INVERTED"})

        self.collection = Collection(self.collection_name)
        self.collection.load()

    async def _fetch_embedding_vllm(self, session: aiohttp.ClientSession, texts: List[str]) -> List[Optional[List[float]]]:
        results = []
        for i in range(0, len(texts), VLLM_BATCH_SIZE):
            chunk = texts[i:i + VLLM_BATCH_SIZE]
            try:
                async with session.post(
                    VLLM_EMBED_URL,
                    json={"model": VLLM_EMBED_MODEL, "input": chunk},
                    timeout=120
                ) as resp:
                    resp.raise_for_status()
                    data = (await resp.json())["data"]
                    data.sort(key=lambda x: x["index"])
                    for d in data:
                        vec = np.array(d["embedding"])
                        vec = vec / (np.linalg.norm(vec) + 1e-12)
                        results.append(vec.tolist())
            except Exception as e:
                logging.error(f"vLLM embedding error: {e}")
                for _ in chunk:
                    results.append(None)
        return results

    async def _fetch_embedding_dashscope(
        self, session: aiohttp.ClientSession, items: List[Dict], semaphore: asyncio.Semaphore, api_key: str
    ) -> List[Optional[List[float]]]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        async def fetch_single(item: Dict) -> Optional[List[float]]:
            if item["type"] == "image":
                content = {"text": "visual content", "image": item["image"]}
            else:
                content = {"text": item["content"][:2000]}
            payload = {
                "model": DASHSCOPE_EMBED_MODEL,
                "input": {"contents": [content]},
                "parameters": {"dimension": VECTOR_DIM}
            }

            async with semaphore:
                for attempt in range(3):
                    try:
                        async with session.post(
                            DASHSCOPE_API_ENDPOINT, headers=headers, json=payload, timeout=TIMEOUT_API
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
                    except Exception as e:
                        logging.warning(f"DashScope embedding attempt {attempt + 1} failed: {e}")
                        await asyncio.sleep(1)
                return None

        tasks = [fetch_single(item) for item in items]
        return await asyncio.gather(*tasks)

    async def _fetch_embedding_minimax(
        self, session: aiohttp.ClientSession, items: List[Dict], semaphore: asyncio.Semaphore, api_key: str
    ) -> List[Optional[List[float]]]:
        """Minimax 文本 Embedding（仅支持文本；图片项会自动降级为描述文本）"""
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        texts = []
        for item in items:
            if item["type"] == "image":
                texts.append("visual content")
            else:
                texts.append(item["content"][:2000])

        async def fetch_batch(batch_texts: List[str]) -> List[Optional[List[float]]]:
            payload = {
                "model": MINIMAX_EMBED_MODEL,
                "input": batch_texts,
                "type": "db"
            }
            async with semaphore:
                for attempt in range(3):
                    try:
                        async with session.post(
                            MINIMAX_API_ENDPOINT, headers=headers, json=payload, timeout=TIMEOUT_API
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                embeddings = data["data"]["embeddings"]
                                embeddings.sort(key=lambda x: x["index"])
                                results = []
                                for d in embeddings:
                                    vec = np.array(d["embedding"])
                                    results.append((vec / (np.linalg.norm(vec) + 1e-12)).tolist())
                                return results
                            elif resp.status == 429:
                                await asyncio.sleep(2 ** attempt)
                            else:
                                body = await resp.text()
                                logging.warning(f"Minimax error {resp.status}: {body}")
                                await asyncio.sleep(1)
                    except Exception as e:
                        logging.warning(f"Minimax embedding attempt {attempt + 1} failed: {e}")
                        await asyncio.sleep(1)
                return [None] * len(batch_texts)

        MAX_MINIMAX_BATCH = 100
        tasks = []
        for i in range(0, len(texts), MAX_MINIMAX_BATCH):
            tasks.append(fetch_batch(texts[i:i + MAX_MINIMAX_BATCH]))

        batches = await asyncio.gather(*tasks)
        results = []
        for b in batches:
            results.extend(b)
        return results

    def _split_text(self, text: str, fname: str, meta_extra: Dict = None) -> List[Dict]:
        items = []
        clean_text = " ".join(text.split())
        step = max(1, self.chunk_size - self.chunk_overlap)
        for i in range(0, len(clean_text), step):
            chunk = clean_text[i:i + self.chunk_size]
            meta = {"source": fname, "type": "text_chunk"}
            if meta_extra:
                meta.update(meta_extra)
            items.append({"type": "text", "content": chunk, "meta": meta})
        return items

    def _parse_pdf(self, path: str) -> List[Dict]:
        items = []
        doc = fitz.open(path)
        fname = os.path.basename(path)
        for page_idx, page in enumerate(doc):
            text = page.get_text("text").strip()
            if len(text) > 10:
                items.extend(self._split_text(text, fname, {"page": page_idx + 1}))
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
        doc = Document(path)
        full_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        return self._split_text(full_text, os.path.basename(path), {"format": "docx"})

    def _parse_plain_text(self, path: str) -> List[Dict]:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return self._split_text(content, os.path.basename(path), {"format": Path(path).suffix[1:]})

    async def process_file(
        self, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore,
        file_path: str, batch_tag: str, embed_backend: str, api_key: str
    ) -> int:
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

        if embed_backend == "vllm (本地)":
            texts = [it.get("content", "") for it in items if it["type"] == "text"]
            if not texts:
                return 0
            text_items = [it for it in items if it["type"] == "text"]
            results = await self._fetch_embedding_vllm(session, texts)
            items = text_items
        elif embed_backend == "DashScope (阿里云)":
            results = await self._fetch_embedding_dashscope(session, items, semaphore, api_key)
        elif embed_backend == "Minimax":
            results = await self._fetch_embedding_minimax(session, items, semaphore, api_key)
        else:
            return 0

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
        api_key: str = "", chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP, progress_callback=None
    ) -> str:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

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
        logs: List[str] = []

        async def _process_one(f_path: str, idx: int):
            await self._pause_event.wait()
            fname = os.path.basename(f_path)
            try:
                inserted = await self.process_file(
                    session, semaphore, f_path, batch_tag, embed_backend, api_key
                )
                if inserted > 0:
                    return (fname, inserted, f"✅ [{idx+1}/{len(pending)}] {fname[:30]}... ({inserted}条)")
                else:
                    return (fname, 0, f"⚠️ [{idx+1}/{len(pending)}] {fname[:30]}... (跳过)")
            except Exception as e:
                logging.exception(f"Failed to process {fname}")
                return (fname, 0, f"❌ [{idx+1}/{len(pending)}] {fname[:30]}... ({type(e).__name__}: {str(e)[:50]})")

        async with aiohttp.ClientSession() as session:
            tasks = [_process_one(f_path, idx) for idx, f_path in enumerate(pending)]
            for coro in asyncio.as_completed(tasks):
                await self._pause_event.wait()
                fname, inserted, msg = await coro
                if inserted > 0:
                    processed.add(fname)
                    total_inserted += inserted
                logs.append(msg)
                if progress_callback:
                    progress_callback("\n".join(logs))

            self._save_progress(processed)

        self.collection.flush()
        return (
            f"🎉 完成！共处理 {len(pending)} 个文件，插入 {total_inserted} 条记录\n"
            f"Collection: {self.collection_name}\n"
            f"总实体数: {self.collection.num_entities}"
        )

    def _load_progress(self) -> Set[str]:
        if self._processed_cache is not None:
            return self._processed_cache
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r') as f:
                self._processed_cache = set(json.load(f))
                return self._processed_cache
        return set()

    def _save_progress(self, processed: Set[str]):
        self._processed_cache = processed
        with open(self.progress_file, 'w') as f:
            json.dump(list(processed), f)


# ==================== Collection 管理函数 ====================

def milvus_connect():
    if not connections.has_connection("default"):
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)

def get_all_collections() -> List[str]:
    try:
        milvus_connect()
        return sorted(utility.list_collections())
    except Exception:
        return []

def list_collections():
    """列出所有Collection状态"""
    try:
        milvus_connect()
        cols = utility.list_collections()
        result = []
        for c in sorted(cols):
            try:
                col = Collection(c)
                col.load()
                stats = {
                    "name": c,
                    "entities": col.num_entities,
                    "loaded": True
                }
            except Exception as e:
                stats = {"name": c, "entities": "-", "loaded": False, "error": str(e)}
            result.append(stats)
        return result
    except Exception as e:
        return [{"error": str(e)}]

def create_collection(name: str) -> str:
    """创建新Collection"""
    try:
        milvus_connect()
        if utility.has_collection(name):
            return f"⚠️ Collection '{name}' 已存在"

        schema = get_kb_schema(f"SIQ KB: {name}")
        collection = Collection(name, schema)
        collection.create_index(field_name="vector", index_params=INDEX_PARAMS)
        collection.create_index(field_name="project_tag", index_params={"index_type": "INVERTED"})
        return f"✅ Collection '{name}' 创建成功"
    except Exception as e:
        return f"❌ 创建失败: {e}"

def drop_collection(name: str) -> str:
    """删除Collection"""
    try:
        milvus_connect()
        if not utility.has_collection(name):
            return f"⚠️ Collection '{name}' 不存在"
        utility.drop_collection(name)
        progress_file = f".progress_{name}.json"
        if os.path.exists(progress_file):
            os.remove(progress_file)
        return f"✅ Collection '{name}' 已删除"
    except Exception as e:
        return f"❌ 删除失败: {e}"

def rebuild_index(name: str) -> str:
    """重建索引"""
    try:
        milvus_connect()
        if not utility.has_collection(name):
            return f"❌ Collection '{name}' 不存在"
        collection = Collection(name)
        collection.release()
        collection.drop_index()
        collection.create_index(field_name="vector", index_params=INDEX_PARAMS)
        collection.load()
        return f"✅ Collection '{name}' 索引重建完成"
    except Exception as e:
        return f"❌ 重建失败: {e}"

def get_tag_stats(name: str) -> str:
    """获取Tag统计"""
    try:
        milvus_connect()
        if not utility.has_collection(name):
            return f"❌ Collection '{name}' 不存在"
        collection = Collection(name)
        collection.load()

        try:
            results = collection.query(expr="id >= 0", output_fields=["project_tag"], limit="unlimited")
        except Exception:
            results = collection.query(expr="id >= 0", output_fields=["project_tag"], limit=100000)

        tag_counts = {}
        for r in results:
            tag = r.get("project_tag", "unknown")
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        if not tag_counts:
            return "暂无数据"

        lines = [f"📊 {name} Tag统计:", "-" * 40]
        for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {tag}: {count} 条")
        lines.append(f"\n总计: {sum(tag_counts.values())} 条")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 统计失败: {e}"


# ==================== Gradio UI ====================

CSS = """
    body {
      background: radial-gradient(1200px 700px at 15% -10%, #f1f7ff 0%, #f8fbff 50%, #f6f8fc 100%) !important;
    }
    .gradio-container {
      max-width: 1400px !important;
      margin: 0 auto !important;
      padding-top: 14px !important;
    }
    .siq-hero {
      border: 1px solid #d9e4f3;
      border-radius: 16px;
      padding: 18px 20px;
      background: linear-gradient(145deg, #ffffff 0%, #f4f8ff 100%);
      box-shadow: 0 8px 22px rgba(24, 55, 90, 0.06);
      margin-bottom: 12px;
    }
    .siq-hero h1 {
      margin: 0 0 6px 0;
      font-size: 28px;
      letter-spacing: 0.2px;
      color: #0f3b66;
    }
    .siq-hero p {
      margin: 0;
      color: #4a5f78;
      font-size: 14px;
    }
    .siq-tabs > .tab-nav {
      background: #edf4ff;
      border: 1px solid #d8e4f3;
      border-radius: 12px;
      padding: 4px;
      margin-bottom: 14px;
    }
    .siq-tabs button {
      font-weight: 600 !important;
      letter-spacing: 0.2px;
    }
    .siq-panel {
      border: 1px solid #d9e3f0;
      border-radius: 16px;
      background: #ffffff;
      padding: 14px;
      box-shadow: 0 4px 16px rgba(20, 55, 95, 0.05);
      min-height: 100%;
    }
    .siq-panel h3 {
      margin-top: 2px !important;
      color: #173a5f;
      font-size: 18px;
    }
    .siq-panel .gr-button {
      border-radius: 10px !important;
      font-weight: 600 !important;
    }
    .siq-panel .gr-button.primary {
      background: linear-gradient(180deg, #2b7cd9 0%, #216bc2 100%) !important;
      border-color: #216bc2 !important;
    }
    .siq-panel .gr-button.stop {
      background: linear-gradient(180deg, #ef6b64 0%, #dc554d 100%) !important;
      border-color: #dc554d !important;
    }
    .siq-panel input, .siq-panel textarea, .siq-panel select {
      border-radius: 10px !important;
      border-color: #d6e1ef !important;
      background: #fbfdff !important;
    }
    .siq-log textarea {
      font-family: "SFMono-Regular", Menlo, Consolas, monospace !important;
      font-size: 12px !important;
      line-height: 1.5 !important;
      background: #f7fbff !important;
    }
"""


def build_ui():
    with gr.Blocks(title="SIQ 知识库管理系统 V7.0", css=CSS) as demo:
        gr.HTML("""
        <div class="siq-hero">
          <h1>🗂️ SIQ 知识库管理系统 <span style="font-size:18px;color:#355d87;">V7.0</span></h1>
          <p>面向投研团队的文件入库与 Collection 运营控制台 · 浅色专业界面 · 实时状态同步</p>
        </div>
        """)

        with gr.Tabs(elem_classes=["siq-tabs"]):
            # Tab 1: 知识入库
            with gr.Tab("📥 知识入库"):
                with gr.Row():
                    with gr.Column(scale=1, min_width=360, elem_classes=["siq-panel"]):
                        gr.Markdown("### ⚙️ 入库配置")

                        coll_dropdown = gr.Dropdown(
                            choices=_DEFAULT_COLLECTIONS,
                            value="ic_legal_scanner",
                            label="目标 Collection"
                        )

                        refresh_list_btn = gr.Button("🧭 刷新 Collection 列表", variant="secondary")

                        doc_dir = gr.Textbox(
                            label="文档目录",
                            value="/home/maoyd/Desktop/knowledge/legal_scanner",
                            placeholder="输入绝对路径"
                        )

                        batch_tag = gr.Textbox(
                            label="批次标签 (project_tag)",
                            value=lambda: f"ingest_{datetime.now().strftime('%m%d_%H%M')}",
                            info="用于区分不同批次的数据"
                        )

                        chunk_size_slider = gr.Slider(
                            label="切片字节数",
                            minimum=200,
                            maximum=2000,
                            step=50,
                            value=700,
                            info="每个切片的最大字符/字节尺度（越大上下文越完整，越小召回更细）"
                        )

                        chunk_overlap_slider = gr.Slider(
                            label="重叠字节数",
                            minimum=0,
                            maximum=500,
                            step=10,
                            value=120,
                            info="相邻切片重叠长度（适当重叠可减少边界信息丢失）"
                        )

                        with gr.Accordion("🔬 高级选项", open=False):
                            embed_backend = gr.Radio(
                                choices=["vllm (本地)", "DashScope (阿里云)", "Minimax"],
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
                                return gr.Textbox(visible=(backend in ("DashScope (阿里云)", "Minimax")))

                            embed_backend.change(fn=toggle_api_key, inputs=[embed_backend], outputs=[api_key])

                            reset_check = gr.Checkbox(
                                label="🧨 重置 Collection（清空已有数据）",
                                value=False,
                                info="勾选后将删除并重建 Collection"
                            )

                        gr.Markdown(
                            "**索引配置**: HNSW (M=32, efConstruction=256, metric=L2)  \n"
                            "**向量维度**: 1024  \n"
                            "**分块策略**: 按文件类型动态切分（pdf/docx/md/txt）  \n"
                            "**视觉策略**: vLLM优先（可用则图片直接向量化）→ OCR增强 → 文本代理兜底"
                        )

                        with gr.Row():
                            start_btn = gr.Button("🚀 开始入库", variant="primary")
                            pause_btn = gr.Button("⏸️ 暂停", variant="secondary")
                            resume_btn = gr.Button("▶️ 继续", variant="secondary")

                        run_control = gr.Textbox(label="运行控制", interactive=False)

                    with gr.Column(scale=2, elem_classes=["siq-panel"]):
                        gr.Markdown("### 📑 运行日志")

                        log_output = gr.Textbox(
                            label="",
                            lines=25,
                            interactive=False,
                            elem_classes=["siq-log"]
                        )

                        stats_output = gr.Textbox(label="📌 统计信息", interactive=False)

                def on_pause():
                    AsyncKnowledgeIngestor.pause()
                    return "⏸️ 已暂停（当前文件处理完成后停止）"

                def on_resume():
                    AsyncKnowledgeIngestor.resume()
                    return "▶️ 已继续"

                pause_btn.click(fn=on_pause, outputs=[run_control])
                resume_btn.click(fn=on_resume, outputs=[run_control])

                def update_overlap(chunk_size, overlap):
                    max_ov = int(chunk_size) - 50
                    if overlap > max_ov:
                        return max_ov
                    return overlap

                chunk_size_slider.change(
                    fn=update_overlap,
                    inputs=[chunk_size_slider, chunk_overlap_slider],
                    outputs=[chunk_overlap_slider]
                )

                async def on_start(coll, doc_dir, tag, backend, key, reset, csize, coverlap):
                    if not doc_dir or not Path(doc_dir).exists():
                        return "❌ 目录不存在，请检查路径", ""
                    ingestor = AsyncKnowledgeIngestor(coll, reset)
                    logs = []
                    def callback(msg):
                        logs.clear()
                        logs.append(msg)
                    result = await ingestor.run(
                        doc_dir, tag, backend, key,
                        chunk_size=int(csize),
                        chunk_overlap=int(coverlap),
                        progress_callback=callback
                    )
                    final_log = logs[-1] if logs else ""
                    return final_log, result

                start_btn.click(
                    fn=on_start,
                    inputs=[
                        coll_dropdown, doc_dir, batch_tag,
                        embed_backend, api_key, reset_check,
                        chunk_size_slider, chunk_overlap_slider
                    ],
                    outputs=[log_output, stats_output]
                )

            # Tab 2: Collection 管理
            with gr.Tab("🗄️ Collection 管理"):
                with gr.Row():
                    with gr.Column(elem_classes=["siq-panel"]):
                        gr.Markdown("### 📊 状态总览")

                        refresh_btn = gr.Button("🔄 刷新状态", variant="secondary")
                        status_output = gr.JSON(label="Collection 列表")
                        status_box = gr.Textbox(label="📣 状态", interactive=False)

                    with gr.Column(elem_classes=["siq-panel"]):
                        gr.Markdown("### 🧰 维护操作")

                        new_coll_name = gr.Textbox(
                            label="Collection 名称",
                            placeholder="例如: ic_custom_workspace"
                        )
                        create_btn = gr.Button("➕ 创建 Collection", variant="primary")
                        create_result = gr.Textbox(label="创建结果", interactive=False)

                        del_coll = gr.Dropdown(
                            choices=_DEFAULT_COLLECTIONS,
                            value="ic_legal_scanner",
                            label="选择要删除的 Collection"
                        )
                        del_btn = gr.Button("🗑️ 删除 Collection", variant="stop")
                        del_result = gr.Textbox(label="删除结果", interactive=False)

                        rebuild_coll = gr.Dropdown(
                            choices=_DEFAULT_COLLECTIONS,
                            value="ic_legal_scanner",
                            label="选择要重建索引的 Collection"
                        )
                        rebuild_btn = gr.Button("🔧 重建索引", variant="secondary")
                        rebuild_result = gr.Textbox(label="重建结果", interactive=False)

                        tag_coll = gr.Dropdown(
                            choices=_DEFAULT_COLLECTIONS,
                            value="ic_legal_scanner",
                            label="选择 Collection（查看Tag统计）"
                        )
                        tag_btn = gr.Button("📈 查看 Tag 统计", variant="secondary")
                        tag_output = gr.Textbox(label="统计结果", interactive=False)

                def on_refresh():
                    return list_collections(), "状态已刷新"

                refresh_btn.click(fn=on_refresh, outputs=[status_output, status_box])
                demo.load(fn=on_refresh, outputs=[status_output, status_box])

                def on_refresh_dropdowns():
                    cols = get_all_collections()
                    if not cols:
                        cols = _DEFAULT_COLLECTIONS
                    return (
                        gr.Dropdown(choices=cols),
                        gr.Dropdown(choices=cols),
                        gr.Dropdown(choices=cols),
                        gr.Dropdown(choices=cols),
                        "Collection 列表已刷新"
                    )

                refresh_list_btn.click(
                    fn=on_refresh_dropdowns,
                    outputs=[coll_dropdown, del_coll, rebuild_coll, tag_coll, status_box]
                )

                create_btn.click(fn=create_collection, inputs=[new_coll_name], outputs=[create_result])
                del_btn.click(fn=drop_collection, inputs=[del_coll], outputs=[del_result])
                rebuild_btn.click(fn=rebuild_index, inputs=[rebuild_coll], outputs=[rebuild_result])
                tag_btn.click(fn=get_tag_stats, inputs=[tag_coll], outputs=[tag_output])

    return demo


if __name__ == "__main__":
    app = build_ui()
    app.launch(server_name="0.0.0.0", server_port=7860)
