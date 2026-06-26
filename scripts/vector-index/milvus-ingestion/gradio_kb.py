#!/usr/bin/env python3
"""
SIQ Investment Committee - Enterprise RAG Knowledge Base Management System
==========================================================================
Tab 1: AI Knowledge Assistant (Gemini-like chat interface)
Tab 2: Knowledge Ingestion (document import)
Tab 3: Collection Management (CRUD + stats)
Tab 4: Advanced Search (debug tool)
"""

# ── Proxy cleanup (MUST be first) ──────────────────────────────────────────
import os, sys, json, re, time, math, glob, hashlib, traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

for _k in ("all_proxy", "ALL_PROXY", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

import requests
import gradio as gr
from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType

# ═══════════════════════════════════════════════════════════════════════════
# 1. CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = "19530"
VLLM_EMBED_URL = "http://127.0.0.1:8000/v1/embeddings"
VLLM_EMBED_MODEL = "Qwen3-VL-Embedding-2B"
EMBED_DIM = 1024
OLLAMA_BASE = "http://localhost:11434"

DASHSCOPE_EMBED_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
DASHSCOPE_EMBED_MODEL = "qwen3-vl-embedding"
DASHSCOPE_DIM = 1024

# MiniMax Embedding
MINIMAX_GROUP_ID = "2017772342268142382"
MINIMAX_EMBED_URL = f"https://api.minimax.chat/v1/embeddings?GroupId={MINIMAX_GROUP_ID}"
MINIMAX_EMBED_MODEL = "embo-01"

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

OLLAMA_MODELS = [
    "expert-35b", "brain-30b", "gemma4:26b",
    "qwen3.5:35b", "glm-4.7-flash", "nemotron-cascade-2:30b",
]

CLOUD_APIS = {
    "MiniMax": {"url": "https://api.minimax.chat/v1/text/chatcompletion_v2", "model": "MiniMax-Text-01"},
    "智谱GLM":  {"url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "model": "glm-4-plus"},
    "Kimi":     {"url": "https://api.moonshot.cn/v1/chat/completions",          "model": "moonshot-v1-128k"},
}

CHUNK_SIZE = 480
CHUNK_OVERLAP = 80
LAW_CHUNK_RE = re.compile(
    r'(?:第[一二三四五六七八九十百千零\d]+条|Article\s+\d+|Section\s+\d+|[一二三四五六七八九十]+[、.])',
    re.IGNORECASE,
)
MAX_INSERT_BATCH = 256
VLLM_BATCH_SIZE = 32

# ═══════════════════════════════════════════════════════════════════════════
# 2. EMBEDDING CLIENT
# ═══════════════════════════════════════════════════════════════════════════

def embed_vllm(texts: List[str], batch: int = VLLM_BATCH_SIZE) -> List[List[float]]:
    """Batch embedding via local vllm server."""
    results = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        try:
            resp = requests.post(VLLM_EMBED_URL, json={
                "model": VLLM_EMBED_MODEL,
                "input": chunk,
            }, timeout=120)
            resp.raise_for_status()
            data = resp.json()["data"]
            # sort by index in case server reorders
            data.sort(key=lambda x: x["index"])
            results.extend([d["embedding"] for d in data])
        except Exception as e:
            raise RuntimeError(f"vllm embedding failed: {e}")
    return results


def embed_dashscope(texts: List[str], api_key: str) -> List[List[float]]:
    """Per-item embedding via DashScope (cloud)."""
    headers = {"Authorization": f"Bearer {api_key}"}
    results = []
    for t in texts:
        try:
            resp = requests.post(DASHSCOPE_EMBED_URL, json={
                "model": DASHSCOPE_EMBED_MODEL,
                "input": t,
                "dimensions": DASHSCOPE_DIM,
            }, headers=headers, timeout=60)
            resp.raise_for_status()
            results.append(resp.json()["data"][0]["embedding"])
        except Exception as e:
            raise RuntimeError(f"DashScope embedding failed: {e}")
    return results


def embed_minimax(texts: List[str], api_key: str) -> List[List[float]]:
    """Embedding via MiniMax embo-01."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    results = []
    for t in texts:
        try:
            resp = requests.post(MINIMAX_EMBED_URL, json={
                "model": MINIMAX_EMBED_MODEL, "texts": [t], "type": "query",
            }, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "vectors" in data and data["vectors"]:
                results.append(data["vectors"][0])
            else:
                raise RuntimeError(f"MiniMax response: {data}")
        except Exception as e:
            raise RuntimeError(f"MiniMax embedding failed: {e}")
    return results


def embed_query(query: str, backend: str = "vllm", api_key: str = "") -> List[float]:
    """Embed a single query string."""
    if backend == "dashscope" and api_key:
        return embed_dashscope([query], api_key)[0]
    if backend == "minimax" and api_key:
        return embed_minimax([query], api_key)[0]
    return embed_vllm([query])[0]

# ═══════════════════════════════════════════════════════════════════════════
# 3. DOCUMENT PARSER
# ═══════════════════════════════════════════════════════════════════════════

class DocParser:
    """Clause-boundary-aware document chunker for legal docs."""

    def __init__(self, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
        self.chunk_size = chunk_size
        self.overlap = overlap

    # ── Readers ──────────────────────────────────────────────────────────

    def read_pdf(self, path: str) -> str:
        try:
            import pdfplumber
            parts = []
            with pdfplumber.open(path) as pdf:
                for p in pdf.pages:
                    t = p.extract_text() or ""
                    if t.strip():
                        parts.append(t)
            return "\n".join(parts)
        except ImportError:
            raise RuntimeError("pdfplumber not installed; pip install pdfplumber")

    def read_docx(self, path: str) -> str:
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    def read_md(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def read_txt(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def read_file(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        readers = {".pdf": self.read_pdf, ".docx": self.read_docx,
                   ".md": self.read_md, ".txt": self.read_txt}
        r = readers.get(ext)
        if not r:
            raise ValueError(f"Unsupported format: {ext}")
        return r(path)

    # ── Chunker ──────────────────────────────────────────────────────────

    def chunk(self, text: str, source: str = "") -> List[Dict[str, Any]]:
        lines = text.split("\n")
        chunks_out: List[Dict[str, Any]] = []
        buf, buf_lines, buf_start = "", [], 0
        current_heading = ""

        def _heading(line):
            m = re.match(r'^(#{1,4}\s+|第[一二三四五六七八九十]+[章节部编]\s+)', line.strip())
            return line.strip() if m else None

        def _flush():
            nonlocal buf, buf_lines, buf_start
            txt = buf.strip()
            if len(txt) < 30:
                return
            prefix = f"[{current_heading}] " if current_heading else ""
            display = (prefix + txt)[:600]
            chunks_out.append({
                "text": txt,
                "source": source,
                "char_count": len(txt),
                "display": display,
                "heading": current_heading,
            })

        for i, line in enumerate(lines):
            h = _heading(line)
            if h:
                current_heading = h
            buf += line + "\n"
            buf_lines.append(line)
            if len(buf) >= self.chunk_size:
                _flush()
                # overlap: keep last N chars
                overlap_text = buf[-self.overlap:] if len(buf) > self.overlap else buf
                buf = overlap_text
                buf_lines = [overlap_text]
                buf_start = i
        _flush()
        for idx, c in enumerate(chunks_out):
            c["chunk_index"] = idx
            c["total_chunks"] = len(chunks_out)
        return chunks_out

# ═══════════════════════════════════════════════════════════════════════════
# 4. MILVUS OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def milvus_connect():
    """Ensure Milvus connection is alive."""
    try:
        if not connections.has_connection("default"):
            connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
        return True
    except Exception:
        try:
            connections.disconnect("default")
        except Exception:
            pass
        try:
            connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
            return True
        except Exception as e:
            raise RuntimeError(f"Cannot connect to Milvus: {e}")


def list_collections() -> List[str]:
    milvus_connect()
    return sorted(utility.list_collections())


def get_collection_metric(col_name: str) -> str:
    """Auto-detect metric type (L2 or IP) for a collection."""
    milvus_connect()
    col = Collection(col_name)
    col.load()
    for idx in col.indexes:
        if idx.field_name == "vector":
            return idx.params.get("metric_type", "IP")
    return "IP"


def create_collection(name: str, desc: str = "", dim: int = EMBED_DIM) -> str:
    milvus_connect()
    if utility.has_collection(name):
        return f"Collection '{name}' already exists"
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
        FieldSchema(name="project_tag", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="metadata", dtype=DataType.JSON),
    ]
    schema = CollectionSchema(fields=fields, description=desc)
    col = Collection(name, schema=schema)
    col.create_index(field_name="vector",
                     index_params={"index_type": "IVF_FLAT", "metric_type": "IP",
                                   "params": {"nlist": 128}})
    return f"✅ Collection '{name}' created (IP / IVF_FLAT)"


def drop_collection(name: str) -> str:
    milvus_connect()
    if not utility.has_collection(name):
        return f"Collection '{name}' does not exist"
    utility.drop_collection(name)
    return f"🗑️ Collection '{name}' deleted"


def rebuild_index(col_name: str, metric: str = "IP", idx_type: str = "IVF_FLAT") -> str:
    milvus_connect()
    col = Collection(col_name)
    col.release()
    # drop old vector index
    for idx_info in col.indexes:
        if idx_info.field_name == "vector":
            col.drop_index(field_name="vector")
            break
    col.create_index(field_name="vector",
                     index_params={"index_type": idx_type, "metric_type": metric,
                                   "params": {"nlist": 128}})
    col.load()
    return f"✅ Index rebuilt: {metric} / {idx_type}"


def insert_vectors(col_name: str, vectors: List[List[float]],
                   tags: List[str], metas: List[dict]) -> int:
    milvus_connect()
    col = Collection(col_name)
    if not col.indexes:
        col.create_index("vector", {"index_type": "IVF_FLAT", "metric_type": "IP",
                                     "params": {"nlist": 128}})
    col.load()
    total = len(vectors)
    inserted = 0
    for i in range(0, total, MAX_INSERT_BATCH):
        batch_v = vectors[i:i + MAX_INSERT_BATCH]
        batch_t = tags[i:i + MAX_INSERT_BATCH]
        batch_m = metas[i:i + MAX_INSERT_BATCH]
        col.insert([batch_v, batch_t, batch_m])
        inserted += len(batch_v)
        if inserted % 500 == 0:
            col.flush()
    col.flush()
    return inserted


def search_vectors(col_name: str, query_vec: List[float], top_k: int = 10,
                   score_threshold: float = 0.0, filter_expr: str = "") -> List[Dict]:
    """Search with auto metric detection."""
    milvus_connect()
    col = Collection(col_name)
    col.load()
    metric = "IP"
    for idx in col.indexes:
        if idx.field_name == "vector":
            metric = idx.params.get("metric_type", "IP")
            break
    search_params = {"metric_type": metric, "params": {"nprobe": 32}}
    results = col.search(
        data=[query_vec], anns_field="vector", param=search_params,
        limit=top_k, expr=filter_expr or None,
        output_fields=["project_tag", "metadata"],
    )
    hits = results[0]
    out = []
    for h in hits:
        score = h.score
        if metric == "L2":
            score = -score  # lower L2 = better; negate for uniform ranking
        if score >= score_threshold:
            meta = h.entity.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {"text": meta}
            out.append({
                "id": h.id,
                "score": round(float(score), 4),
                "metric": metric,
                "tag": h.entity.get("project_tag", ""),
                "source": meta.get("source", ""),
                "text": meta.get("text", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "total_chunks": meta.get("total_chunks", 0),
                "char_count": meta.get("char_count", 0),
                "timestamp": meta.get("timestamp", ""),
            })
    return out


def get_collection_stats(col_name: str) -> Dict[str, Any]:
    milvus_connect()
    col = Collection(col_name)
    col.load()
    count = col.num_entities
    metric = "IP"
    idx_type = "IVF_FLAT"
    for idx in col.indexes:
        if idx.field_name == "vector":
            metric = idx.params.get("metric_type", "IP")
            idx_type = idx.params.get("index_type", "IVF_FLAT")
            break
    desc = col.description or ""
    return {
        "name": col_name,
        "description": desc,
        "count": count,
        "metric": metric,
        "index_type": idx_type,
    }


def get_tag_stats(col_name: str) -> Dict[str, int]:
    """Count entities per project_tag."""
    milvus_connect()
    col = Collection(col_name)
    col.load()
    total = col.num_entities
    if total == 0:
        return {}
    tags = {}
    batch = min(1000, 16000)
    seen = 0
    while seen < total:
        actual_limit = min(batch, 16000)
        try:
            res = col.query(expr="id >= 0", output_fields=["project_tag"],
                            limit=actual_limit)
        except Exception:
            break
        for r in res:
            t = r.get("project_tag", "untagged")
            tags[t] = tags.get(t, 0) + 1
        if len(res) < actual_limit:
            break
        seen += len(res)
    return tags

# ═══════════════════════════════════════════════════════════════════════════
# 5. GENERATION MODEL CLIENT
# ═══════════════════════════════════════════════════════════════════════════

def call_ollama(model: str, messages: List[Dict], temperature: float = 0.7) -> str:
    """Call local Ollama model."""
    resp = requests.post(f"{OLLAMA_BASE}/api/chat", json={
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }, timeout=300)
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "")


def call_cloud_api(provider: str, api_key: str, messages: List[Dict],
                   temperature: float = 0.7) -> str:
    """Call cloud API (OpenAI-compatible format)."""
    cfg = CLOUD_APIS[provider]
    resp = requests.post(cfg["url"], json={
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
    }, headers={"Authorization": f"Bearer {api_key}"}, timeout=300)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def generate_response(model_choice: str, api_key: str, messages: List[Dict],
                      temperature: float = 0.7) -> str:
    """Unified generation interface."""
    if model_choice in CLOUD_APIS:
        return call_cloud_api(model_choice, api_key, messages, temperature)
    return call_ollama(model_choice, messages, temperature)

# ═══════════════════════════════════════════════════════════════════════════
# 6. RAG PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def hybrid_search(col_name: str, query: str, top_k: int = 10,
                  score_threshold: float = 0.0, mode: str = "semantic",
                  embed_backend: str = "vllm", api_key: str = "") -> Tuple[List[Dict], str]:
    """
    Three search modes:
      - semantic:  pure vector search
      - hybrid:    vector + keyword (RRF fusion, k=60)
      - keyword:   keyword-first with vector boost
    Returns (results, mode_used).
    """
    if mode == "keyword":
        # Keyword first, then vector boost
        keyword_results = _keyword_search(col_name, query, top_k * 3)
        if keyword_results:
            # deduplicate by source+chunk_index
            seen = set()
            deduped = []
            for r in keyword_results:
                key = (r["source"], r["chunk_index"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(r)
            return deduped[:top_k], "keyword"
        # fallback to vector
        mode = "semantic"

    if mode == "hybrid":
        # Vector search
        vec = embed_query(query, embed_backend, api_key)
        vec_results = search_vectors(col_name, vec, top_k * 3, score_threshold)
        # Keyword search
        kw_results = _keyword_search(col_name, query, top_k * 3)
        # RRF fusion
        return _rrf_fusion(vec_results, kw_results, top_k, k=60), "hybrid"

    # Pure semantic
    vec = embed_query(query, embed_backend, api_key)
    results = search_vectors(col_name, vec, top_k, score_threshold)
    return results, "semantic"


def _keyword_search(col_name: str, query: str, limit: int) -> List[Dict]:
    """Scan metadata.source for keyword matches (BM25-like)."""
    milvus_connect()
    col = Collection(col_name)
    col.load()
    total = col.num_entities
    if total == 0:
        return []
    # Extract meaningful keywords from query
    keywords = [w for w in re.split(r'[\s,，。、：:；;]+', query) if len(w) >= 2]
    if not keywords:
        return []
    scored = []
    # Use Milvus query iterator to avoid offset limit
    MAX_OFFSET = 16000
    batch_size = min(1000, total)
    scanned = 0
    while scanned < total and len(scored) < limit * 2:
        actual_limit = min(batch_size, MAX_OFFSET)
        try:
            res = col.query(expr="id >= 0", output_fields=["project_tag", "metadata"],
                            limit=actual_limit)
        except Exception:
            break
        for r in res:
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    continue
            source = meta.get("source", "")
            text = meta.get("text", "")
            combined = f"{source} {text}".lower()
            score = sum(1 for kw in keywords if kw.lower() in combined)
            if score > 0:
                scored.append({
                    "id": r.get("id", 0),
                    "score": float(score),
                    "metric": "keyword",
                    "tag": r.get("project_tag", ""),
                    "source": source,
                    "text": text,
                    "chunk_index": meta.get("chunk_index", 0),
                    "total_chunks": meta.get("total_chunks", 0),
                    "char_count": meta.get("char_count", 0),
                    "timestamp": meta.get("timestamp", ""),
                })
        if len(res) < actual_limit:
            break
        scanned += len(res)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def _rrf_fusion(vec_results: List[Dict], kw_results: List[Dict],
                 top_k: int, k: int = 60) -> List[Dict]:
    """Reciprocal Rank Fusion."""
    scores: Dict[str, float] = {}
    items: Dict[str, Dict] = {}
    for rank, r in enumerate(vec_results):
        key = f"{r['source']}__{r['chunk_index']}"
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        items[key] = r
    for rank, r in enumerate(kw_results):
        key = f"{r['source']}__{r['chunk_index']}"
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        items[key] = r
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    out = []
    for key, rrf_score in ranked:
        item = items[key].copy()
        item["rrf_score"] = round(rrf_score, 4)
        out.append(item)
    return out

# ═══════════════════════════════════════════════════════════════════════════
# 7. INGEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class IngestEngine:
    """Async-concurrent document ingestion pipeline."""

    def __init__(self):
        self.parser = DocParser()
        self.log_lines: List[str] = []
        self.total_chunks = 0
        self.inserted = 0

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_lines.append(line)
        print(line)

    def run(self, col_name: str, doc_dir: str, extensions: List[str],
            embed_backend: str, api_key: str, chunk_size: int, overlap: int,
            tag: str, reset: bool = False):
        self.log_lines = []
        self.total_chunks = 0
        self.inserted = 0
        self.parser = DocParser(chunk_size=chunk_size, overlap=overlap)
        try:
            milvus_connect()
            if not utility.has_collection(col_name):
                create_collection(col_name, ROLE_REGISTRY.get(col_name, {}).get("desc", ""))
                self._log(f"Auto-created collection: {col_name}")
            col = Collection(col_name)
            col.load()
            if reset:
                pre = col.num_entities
                col.drop()
                create_collection(col_name, ROLE_REGISTRY.get(col_name, {}).get("desc", ""))
                col = Collection(col_name)
                col.load()
                self._log(f"Reset: cleared {pre} entities")
            # Find documents
            ext_set = {f".{e.lower().lstrip('.')}" for e in extensions}
            files = []
            for p in sorted(Path(doc_dir).rglob("*")):
                if p.is_file() and p.suffix.lower() in ext_set:
                    files.append(str(p))
            self._log(f"Found {len(files)} documents in {doc_dir}")
            if not files:
                return "No documents found."
            # Parse all
            all_chunks = []
            for f in files:
                try:
                    text = self.parser.read_file(f)
                    chunks = self.parser.chunk(text, source=Path(f).name)
                    all_chunks.extend(chunks)
                    self._log(f"  📄 {Path(f).name}: {len(chunks)} chunks")
                except Exception as e:
                    self._log(f"  ❌ {Path(f).name}: {e}")
            self.total_chunks = len(all_chunks)
            self._log(f"Total chunks: {self.total_chunks}")
            if not all_chunks:
                return "No chunks produced."
            # Embed
            self._log(f"Embedding via {embed_backend}...")
            texts = [c["text"] for c in all_chunks]
            if embed_backend == "dashscope" and api_key:
                vectors = embed_dashscope(texts, api_key)
            elif embed_backend == "minimax" and api_key:
                vectors = embed_minimax(texts, api_key)
            else:
                vectors = embed_vllm(texts)
            # Insert
            self._log("Inserting into Milvus...")
            tags = [tag] * len(vectors)
            metas = []
            ts = datetime.now().isoformat()
            for c in all_chunks:
                metas.append({
                    "source": c["source"],
                    "text": c["display"],
                    "chunk_index": c["chunk_index"],
                    "total_chunks": c["total_chunks"],
                    "char_count": c["char_count"],
                    "timestamp": ts,
                })
            self.inserted = insert_vectors(col_name, vectors, tags, metas)
            self._log(f"✅ Done: {self.inserted} vectors inserted")
            return f"✅ Ingested {self.inserted} chunks from {len(files)} documents"
        except Exception as e:
            self._log(f"❌ Error: {e}")
            return f"❌ Error: {e}"

# ═══════════════════════════════════════════════════════════════════════════
# 8. GRADIO UI
# ═══════════════════════════════════════════════════════════════════════════

CUSTOM_CSS = """
/* ── SIQ RAG · Gemini-style Light Theme (Gradio 6.12 compatible) ── */
.gradio-container {
    max-width: 1440px !important;
}
/* ── Tab navigation ── */
.tab-container button {
    font-weight: 500 !important;
    font-size: 14px !important;
    padding: 10px 20px !important;
}
.tab-container button.selected {
    color: #1a73e8 !important;
    border-bottom: 2px solid #1a73e8 !important;
}
.tab-container button:hover {
    color: #1a73e8 !important;
}
/* ── Chat bubble styles ── */
.message-user {
    background: #e8f0fe !important;
    border-radius: 16px 16px 4px 16px !important;
}
.message-bot {
    background: #ffffff !important;
    border-radius: 16px 16px 16px 4px !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05) !important;
}
/* ── Markdown ── */
.gr-markdown pre {
    border-radius: 10px !important;
}
"""

# ── Build UI ────────────────────────────────────────────────────────────

def build_ui():
    ingest_engine = IngestEngine()

    with gr.Blocks(title="SIQ RAG Knowledge Base") as demo:
        # State variables
        chat_history = gr.State([])
        search_results_state = gr.State([])

        # ── Tab 1: AI Knowledge Assistant ────────────────────────────────
        with gr.Tab("💬 AI 知识助手"):
            with gr.Row(equal_height=True):
                # Left config panel
                with gr.Column(scale=1, min_width=280):
                    gr.Markdown("### ⚙️ 配置面板", elem_classes="config-header")
                    coll_dropdown_chat = gr.Dropdown(
                        choices=list(ROLE_REGISTRY.keys()),
                        value="ic_legal_scanner",
                        label="Collection",
                        info="选择知识库",
                    )
                    embed_backend_chat = gr.Radio(
                        ["vllm (本地)", "MiniMax embo-01", "DashScope (云端)"],
                        value="vllm (本地)",
                        label="Embedding 后端",
                    )
                    model_choice = gr.Radio(
                        ["local"] + list(OLLAMA_MODELS) + list(CLOUD_APIS.keys()),
                        value="qwen3.5:35b",
                        label="生成模型",
                    )
                    cloud_api_key = gr.Textbox(
                        label="API Key (云端模型)",
                        type="password",
                        info="选择云端模型时填写",
                        visible=False,
                    )
                    top_k_slider = gr.Slider(1, 50, value=10, step=1, label="Top-K")
                    score_slider = gr.Slider(0.0, 1.0, value=0.0, step=0.05, label="Score 阈值")
                    rag_mode = gr.Radio(
                        ["纯向量语义", "混合检索(语义+关键词)", "关键词优先"],
                        value="纯向量语义",
                        label="RAG 检索模式",
                    )
                    dashscope_key_chat = gr.Textbox(
                        label="Embedding API Key",
                        type="password",
                        info="MiniMax / DashScope 时填写",
                        visible=False,
                    )

                    def toggle_cloud_key(choice):
                        return gr.Textbox(visible=choice in CLOUD_APIS)

                    def toggle_dashscope_key(backend):
                        return gr.Textbox(visible=("DashScope" in backend or "MiniMax" in backend))

                    model_choice.change(fn=toggle_cloud_key, inputs=[model_choice], outputs=[cloud_api_key])
                    embed_backend_chat.change(fn=toggle_dashscope_key, inputs=[embed_backend_chat], outputs=[dashscope_key_chat])

                # Right chat area
                with gr.Column(scale=3):
                    chatbot = gr.Chatbot(
                        value=[],
                        label="SIQ 知识助手",
                        autoscroll=True,
                        height=600,
                    )
                    with gr.Row():
                        chat_input = gr.Textbox(
                            label="输入问题或报告主题",
                            placeholder="输入法律问题、检索关键词或报告主题...",
                            scale=4,
                            lines=2,
                        )
                    with gr.Row():
                        search_btn = gr.Button("🔍 搜索", variant="primary", scale=1)
                        report_btn = gr.Button("📝 生成报告", variant="secondary", scale=1)
                        clear_btn = gr.Button("🗑️ 清空", scale=1)
                    sources_md = gr.Markdown("", label="参考来源")

        # ── Tab 2: Knowledge Ingestion ───────────────────────────────────
        with gr.Tab("📦 知识入库"):
            with gr.Row():
                with gr.Column(scale=1):
                    coll_dropdown_ingest = gr.Dropdown(
                        choices=list(ROLE_REGISTRY.keys()),
                        value="ic_legal_scanner",
                        label="Collection",
                        info="选择或新建目标知识库",
                        allow_custom_value=True,
                    )
                    doc_dir = gr.Textbox(
                        label="文档目录路径",
                        value="/home/maoyd/data/legal_docs",
                        info="包含 PDF/DOCX/MD/TXT 的目录",
                    )
                    ext_filter = gr.CheckboxGroup(
                        ["pdf", "docx", "md", "txt"],
                        value=["pdf", "docx", "md", "txt"],
                        label="文档格式",
                    )
                    embed_backend_ingest = gr.Radio(
                        ["vllm (本地)", "MiniMax embo-01", "DashScope (云端)"],
                        value="vllm (本地)",
                        label="Embedding 后端",
                    )
                    dashscope_key_ingest = gr.Textbox(
                        label="Embedding API Key",
                        type="password",
                        info="MiniMax / DashScope 时填写",
                        visible=False,
                    )
                    chunk_size_input = gr.Slider(200, 2000, value=CHUNK_SIZE, step=20, label="Chunk Size")
                    overlap_input = gr.Slider(0, 400, value=CHUNK_OVERLAP, step=10, label="Overlap")
                    tag_input = gr.Textbox(label="批次标签", value="default", info="project_tag")
                    reset_check = gr.Checkbox(label="重置 Collection（清空已有数据）", value=False)
                    ingest_btn = gr.Button("🚀 开始入库", variant="primary")
                    embed_backend_ingest.change(
                        fn=lambda b: gr.Textbox(visible=("DashScope" in b or "MiniMax" in b)),
                        inputs=[embed_backend_ingest],
                        outputs=[dashscope_key_ingest],
                    )

                with gr.Column(scale=1):
                    progress_bar = gr.Label(label="状态")
                    ingest_log = gr.Textbox(
                        label="入库日志",
                        lines=20,
                        interactive=False,
                        max_lines=50,
                    )

        # ── Tab 3: Collection Management ─────────────────────────────────
        with gr.Tab("🗄️ Collection 管理"):
            with gr.Row():
                with gr.Column(scale=2):
                    gr.Markdown("### 📊 Collection 状态总览")
                    stats_table = gr.HTML(
                        value="<em>点击刷新加载…</em>",
                        label="状态表",
                    )
                    refresh_btn = gr.Button("🔄 刷新", variant="primary")

                with gr.Column(scale=1):
                    gr.Markdown("### 🔧 操作")
                    new_coll_name = gr.Textbox(label="Collection 名称")
                    new_coll_desc = gr.Textbox(label="描述", info="可选")
                    create_btn = gr.Button("➕ 新建 Collection", variant="primary")
                    del_coll_name = gr.Dropdown(
                        choices=list(ROLE_REGISTRY.keys()),
                        label="要删除的 Collection",
                    )
                    del_btn = gr.Button("🗑️ 删除", variant="stop")

            with gr.Row():
                with gr.Column():
                    gr.Markdown("### 🔨 索引重建")
                    idx_coll = gr.Dropdown(
                        choices=list(ROLE_REGISTRY.keys()),
                        label="Collection",
                    )
                    idx_metric = gr.Radio(["IP", "L2"], value="IP", label="Metric")
                    idx_type = gr.Dropdown(["IVF_FLAT", "HNSW", "IVF_SQ8"], value="IVF_FLAT", label="Index Type")
                    rebuild_btn = gr.Button("🔨 重建索引", variant="primary")
                    rebuild_result = gr.Markdown("")

            with gr.Row():
                with gr.Column():
                    gr.Markdown("### 📈 数据统计")
                    tag_stats_coll = gr.Dropdown(
                        choices=list(ROLE_REGISTRY.keys()),
                        label="Collection",
                    )
                    tag_stats_btn = gr.Button("📊 查看统计")
                    tag_stats_output = gr.HTML(
                        value="<em>选择 Collection 后点击查看…</em>",
                        label="各 Tag 统计",
                    )

        # ── Tab 4: Advanced Search ───────────────────────────────────────
        with gr.Tab("🔍 高级检索"):
            with gr.Row():
                with gr.Column(scale=1):
                    adv_coll = gr.Dropdown(
                        choices=list(ROLE_REGISTRY.keys()),
                        value="ic_legal_scanner",
                        label="Collection",
                    )
                    adv_query = gr.Textbox(label="Query", lines=3, placeholder="输入检索 query...")
                    adv_embed = gr.Radio(
                        ["vllm (本地)", "MiniMax embo-01", "DashScope (云端)"],
                        value="vllm (本地)",
                        label="Embedding",
                    )
                    adv_dk_key = gr.Textbox(label="Embedding API Key", type="password", visible=False)
                    adv_topk = gr.Slider(1, 100, value=20, step=1, label="Top-K")
                    adv_threshold = gr.Slider(0.0, 1.0, value=0.0, step=0.05, label="Score 阈值")
                    adv_mode = gr.Radio(
                        ["纯向量语义", "混合检索(语义+关键词)", "关键词优先"],
                        value="纯向量语义",
                        label="检索模式",
                    )
                    adv_search_btn = gr.Button("🔎 检索", variant="primary")
                    adv_embed.change(
                        fn=lambda b: gr.Textbox(visible=("DashScope" in b or "MiniMax" in b)),
                        inputs=[adv_embed],
                        outputs=[adv_dk_key],
                    )

                with gr.Column(scale=2):
                    adv_results = gr.HTML(
                        value="<em>执行检索后显示结果…</em>",
                        label="检索结果",
                    )
                    score_dist = gr.Markdown("### 向量相似度分布\n_Awaiting search..._")

        # ═══════════════════════════════════════════════════════════════════
        # EVENT HANDLERS
        # ═══════════════════════════════════════════════════════════════════

        # ── Helper: get effective model name ─────────────────────────────
        def get_model_name(choice):
            if choice in CLOUD_APIS:
                return choice
            return choice  # Ollama model name directly

        def get_embed_backend(label):
            if "MiniMax" in label:
                return "minimax"
            return "dashscope" if "DashScope" in label else "vllm"

        def toggle_embed_key(label):
            return gr.Textbox(visible=("DashScope" in label or "MiniMax" in label))

        def get_rag_mode(label):
            mapping = {"纯向量语义": "semantic", "混合检索(语义+关键词)": "hybrid", "关键词优先": "keyword"}
            return mapping.get(label, "semantic")

        # ── Tab 1: Search handler ────────────────────────────────────────
        def on_search(query, coll, embed_label, model, dk_key, cloud_key,
                      top_k, threshold, rag_label, history):
            if not query.strip():
                return history, "⚠️ 请输入查询内容"
            try:
                eb = get_embed_backend(embed_label)
                dk = dk_key if eb == "dashscope" else ""
                results, mode_used = hybrid_search(
                    coll, query, top_k=int(top_k),
                    score_threshold=float(threshold),
                    mode=get_rag_mode(rag_label),
                    embed_backend=eb, api_key=dk,
                )
                if not results:
                    history = history + [
                        {"role": "user", "content": query},
                        {"role": "assistant", "content": "未找到相关结果。请尝试调整检索参数或更换 Collection。"},
                    ]
                    return history, ""
                # Build sources markdown
                sources_lines = ["### 📚 参考来源\n"]
                seen = set()
                for r in results[:10]:
                    src = r.get("source", "")
                    if src not in seen:
                        seen.add(src)
                        tag_info = ROLE_REGISTRY.get(coll, {}).get("desc", coll)
                        sources_lines.append(f"- **{src}** (Collection: {tag_info}, Chunk {r['chunk_index']}/{r['total_chunks']})")
                # Build response
                context_parts = []
                for i, r in enumerate(results[:5]):
                    text = r.get("text", "")[:300]
                    context_parts.append(f"[{i+1}] {r.get('source','')} (score={r['score']}):\n{text}")
                context = "\n\n".join(context_parts)
                answer = f"**检索模式**: {mode_used} | **结果数**: {len(results)}\n\n"
                answer += "---\n\n### 相关片段\n\n" + context
                answer += "\n\n---\n\n*以上为检索到的原始片段，如需生成完整分析请点击「生成报告」。*"
                history = history + [
                    {"role": "user", "content": query},
                    {"role": "assistant", "content": answer},
                ]
                return history, "\n".join(sources_lines)
            except Exception as e:
                tb = traceback.format_exc()
                history = history + [
                    {"role": "user", "content": query},
                    {"role": "assistant", "content": f"❌ 搜索出错:\n```\n{tb}\n```"},
                ]
                return history, ""

        # ── Tab 1: Report generation handler ─────────────────────────────
        def on_report(query, coll, embed_label, model, dk_key, cloud_key,
                      top_k, threshold, rag_label, history):
            if not query.strip():
                return history, "⚠️ 请输入报告主题"
            try:
                eb = get_embed_backend(embed_label)
                dk = dk_key if eb == "dashscope" else ""
                effective_model = get_model_name(model)
                api_k = cloud_key if model in CLOUD_APIS else ""

                # Step 1: Generate sub-queries via LLM
                sub_query_prompt = (
                    "你是一个专业的法律知识助手。用户需要生成一份关于以下主题的详细报告。\n"
                    "请生成 3-5 个用于检索相关法律依据的子查询，每行一个，不要编号。\n\n"
                    f"报告主题: {query}\n\n"
                    "子查询:"
                )
                sub_queries_text = generate_response(
                    effective_model, api_k,
                    [{"role": "system", "content": "你是法律知识检索助手。只输出子查询，每行一个。"},
                     {"role": "user", "content": sub_query_prompt}],
                    temperature=0.5,
                )
                sub_queries = [q.strip() for q in sub_queries_text.strip().split("\n") if q.strip()]
                if not sub_queries:
                    sub_queries = [query]

                # Step 2: Multi-query hybrid search
                all_results = []
                seen_keys = set()
                for sq in sub_queries:
                    results, _ = hybrid_search(
                        coll, sq, top_k=int(top_k),
                        score_threshold=float(threshold) * 0.8,
                        mode=get_rag_mode(rag_label),
                        embed_backend=eb, api_key=dk,
                    )
                    for r in results:
                        key = (r.get("source", ""), r.get("chunk_index", 0))
                        if key not in seen_keys:
                            seen_keys.add(key)
                            all_results.append(r)
                # Sort by score descending
                all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
                all_results = all_results[:20]

                if not all_results:
                    history = history + [
                        {"role": "user", "content": query},
                        {"role": "assistant", "content": "⚠️ 未检索到足够的参考资料，无法生成报告。"},
                    ]
                    return history, ""

                # Step 3: Build context for generation
                context_parts = []
                source_list = []
                for i, r in enumerate(all_results):
                    text = r.get("text", "")
                    src = r.get("source", "")
                    context_parts.append(f"[来源{i+1}] {src}\n{text}")
                    source_list.append(f"[{i+1}] {src}")

                context_block = "\n\n---\n\n".join(context_parts)

                # Step 4: Generate report
                report_prompt = (
                    f"请基于以下检索到的法律参考资料，撰写一份关于「{query}」的专业分析报告。\n\n"
                    "要求：\n"
                    "1. 结构清晰，包含概述、核心分析、风险提示、建议\n"
                    "2. 引用具体法规条款\n"
                    "3. 标注引用来源编号\n\n"
                    f"参考资料：\n\n{context_block}"
                )
                report = generate_response(
                    effective_model, api_k,
                    [{"role": "system", "content": "你是SIQ投委会法务专家助手，擅长撰写专业法律分析报告。"},
                     {"role": "user", "content": report_prompt}],
                    temperature=0.7,
                )

                # Step 5: Format output
                sources_md = "### 📚 报告引用来源\n" + "\n".join(f"- {s}" for s in source_list[:15])
                full_report = f"**📋 生成报告：{query}**\n\n_{datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n---\n\n{report}\n\n---\n\n{sources_md}"

                history = history + [
                    {"role": "user", "content": f"📝 生成报告: {query}"},
                    {"role": "assistant", "content": full_report},
                ]
                return history, sources_md
            except Exception as e:
                tb = traceback.format_exc()
                history = history + [
                    {"role": "user", "content": f"📝 生成报告: {query}"},
                    {"role": "assistant", "content": f"❌ 报告生成出错:\n```\n{tb}\n```"},
                ]
                return history, ""

        # ── Tab 1: Clear chat ────────────────────────────────────────────
        def clear_chat():
            return [], ""

        # ── Tab 2: Ingest handler ────────────────────────────────────────
        def on_ingest(coll, doc_dir, exts, embed_label, dk_key, ck_size, ovlp, tag, reset):
            if not doc_dir.strip() or not Path(doc_dir).exists():
                return "❌ 文档目录不存在", ""
            eb = get_embed_backend(embed_label)
            dk = dk_key if eb == "dashscope" else ""
            exts = exts if exts else ["pdf", "docx", "md", "txt"]
            result = ingest_engine.run(
                coll, doc_dir.strip(), exts, eb, dk,
                int(ck_size), int(ovlp), tag, reset,
            )
            log_text = "\n".join(ingest_engine.log_lines[-50:])
            progress = f"📦 {ingest_engine.inserted}/{ingest_engine.total_chunks} chunks inserted"
            return progress, log_text

        # ── Tab 3: Stats handler ─────────────────────────────────────────
        def _rows_to_html_table(headers, rows):
            """Convert list-of-lists to styled HTML table."""
            hdr = "".join(f"<th>{h}</th>" for h in headers)
            body = ""
            for row in rows:
                body += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
            return (
                '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;'
                'font-size:13px">'
                f"<thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table></div>"
            )

        def refresh_stats():
            try:
                milvus_connect()
                cols = list_collections()
                headers = ["名称", "描述", "实体数", "Metric", "索引类型"]
                rows = []
                for c in cols:
                    info = get_collection_stats(c)
                    reg = ROLE_REGISTRY.get(c, {})
                    desc = reg.get("desc", info.get("description", ""))
                    rows.append([
                        f"{reg.get('icon','📦')} {c}",
                        desc,
                        info["count"],
                        info["metric"],
                        info["index_type"],
                    ])
                return _rows_to_html_table(headers, rows)
            except Exception as e:
                return f"<p style='color:red'>❌ Error: {e}</p>"

        def on_create(name, desc):
            if not name.strip():
                return "❌ 名称不能为空"
            return create_collection(name.strip(), desc)

        def on_delete(name):
            if not name:
                return "❌ 请选择 Collection"
            return drop_collection(name)

        def on_rebuild(coll, metric, idx_type):
            if not coll:
                return "❌ 请选择 Collection"
            return rebuild_index(coll, metric, idx_type)

        def on_tag_stats(coll):
            if not coll:
                return "<em>请先选择 Collection</em>"
            try:
                stats = get_tag_stats(coll)
                rows = sorted(stats.items(), key=lambda x: -x[1])
                return _rows_to_html_table(["Tag", "文档/切片数"], rows)
            except Exception as e:
                return f"<p style='color:red'>Error: {e}</p>"

        # ── Tab 4: Advanced search handler ───────────────────────────────
        def on_adv_search(query, coll, embed_label, dk_key, top_k, threshold, rag_label):
            if not query.strip():
                return "<em>请输入查询内容</em>", "### ⚠️ 请输入查询内容"
            try:
                eb = get_embed_backend(embed_label)
                dk = dk_key if eb == "dashscope" else ""
                results, mode_used = hybrid_search(
                    coll, query, top_k=int(top_k),
                    score_threshold=float(threshold),
                    mode=get_rag_mode(rag_label),
                    embed_backend=eb, api_key=dk,
                )
                rows = []
                scores = []
                for r in results:
                    rows.append([
                        f"{r['score']:.4f}",
                        r.get("source", ""),
                        r.get("chunk_index", 0),
                        r.get("total_chunks", 0),
                        r.get("text", "")[:120] + "...",
                    ])
                    scores.append(r["score"])
                html_table = _rows_to_html_table(
                    ["Score", "Source", "Chunk#", "Total", "Text Preview"], rows
                ) if rows else "<em>无结果</em>"
                # Score distribution
                if scores:
                    min_s, max_s = min(scores), max(scores)
                    avg_s = sum(scores) / len(scores)
                    bins = 10
                    hist = [0] * bins
                    for s in scores:
                        idx = min(int((s - min_s) / (max_s - min_s + 1e-9) * bins), bins - 1)
                        hist[idx] += 1
                    max_h = max(hist) if hist else 1
                    bar_lines = [f"### 📊 相似度分布 (mode={mode_used}, metric=auto)\n"]
                    bar_lines.append(f"**Range**: {min_s:.4f} ~ {max_s:.4f} | **Avg**: {avg_s:.4f} | **Count**: {len(scores)}\n\n```\n")
                    for i, h in enumerate(hist):
                        low = min_s + (max_s - min_s) * i / bins
                        high = min_s + (max_s - min_s) * (i + 1) / bins
                        bar = "█" * int(h / max_h * 30) if max_h > 0 else ""
                        bar_lines.append(f"{low:7.3f}-{high:7.3f} | {bar} ({h})")
                    bar_lines.append("```")
                    dist_md = "\n".join(bar_lines)
                else:
                    dist_md = "### 无结果"
                return html_table, dist_md
            except Exception as e:
                return f"<p style='color:red'>❌ Error: {e}</p>", f"### ❌ {e}"

        # ═══════════════════════════════════════════════════════════════════
        # WIRE EVENTS
        # ═══════════════════════════════════════════════════════════════════

        # Tab 1
        search_btn.click(
            fn=on_search,
            inputs=[chat_input, coll_dropdown_chat, embed_backend_chat,
                    model_choice, dashscope_key_chat, cloud_api_key,
                    top_k_slider, score_slider, rag_mode, chat_history],
            outputs=[chatbot, sources_md],
        )
        chat_input.submit(
            fn=on_search,
            inputs=[chat_input, coll_dropdown_chat, embed_backend_chat,
                    model_choice, dashscope_key_chat, cloud_api_key,
                    top_k_slider, score_slider, rag_mode, chat_history],
            outputs=[chatbot, sources_md],
        )
        report_btn.click(
            fn=on_report,
            inputs=[chat_input, coll_dropdown_chat, embed_backend_chat,
                    model_choice, dashscope_key_chat, cloud_api_key,
                    top_k_slider, score_slider, rag_mode, chat_history],
            outputs=[chatbot, sources_md],
        )
        clear_btn.click(fn=clear_chat, outputs=[chatbot, sources_md])

        # Tab 2
        ingest_btn.click(
            fn=on_ingest,
            inputs=[coll_dropdown_ingest, doc_dir, ext_filter,
                    embed_backend_ingest, dashscope_key_ingest,
                    chunk_size_input, overlap_input, tag_input, reset_check],
            outputs=[progress_bar, ingest_log],
        )

        # Tab 3
        refresh_btn.click(fn=refresh_stats, outputs=[stats_table])
        create_btn.click(fn=on_create, inputs=[new_coll_name, new_coll_desc],
                         outputs=[stats_table])
        del_btn.click(fn=on_delete, inputs=[del_coll_name], outputs=[stats_table])
        rebuild_btn.click(fn=on_rebuild, inputs=[idx_coll, idx_metric, idx_type],
                          outputs=[rebuild_result])
        tag_stats_btn.click(fn=on_tag_stats, inputs=[tag_stats_coll],
                            outputs=[tag_stats_output])

        # Tab 4
        adv_search_btn.click(
            fn=on_adv_search,
            inputs=[adv_query, adv_coll, adv_embed, adv_dk_key,
                    adv_topk, adv_threshold, adv_mode],
            outputs=[adv_results, score_dist],
        )

        # ── Load initial data ────────────────────────────────────────────
        demo.load(fn=refresh_stats, outputs=[stats_table])

    return demo


# ═══════════════════════════════════════════════════════════════════════════
# 9. MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = build_ui()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
        css=CUSTOM_CSS,
        theme=gr.themes.Soft(primary_hue="blue", secondary_hue="purple"),
    )