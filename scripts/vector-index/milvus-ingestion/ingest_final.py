#!/usr/bin/env python3
"""
SIQ RAG Knowledge Base - 全格式异步入库工具 V8.1
统一规范：
- Collection命名: {role} (无后缀)
- 索引: HNSW (M=32, efConstruction=256, metric=L2)
- 字段: id(auto), vector(1024d), project_tag, metadata
- metadata: SIQChunkMetadata v1，用于证据引用、检索评测和 Graph sidecar
"""

import os
for _k in ("all_proxy", "ALL_PROXY", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

import sys
import json
import asyncio
import base64
import html
import logging
import re
import io
import hashlib
import mimetypes
import socket
import time
import requests
import numpy as np
import threading
from datetime import datetime
from collections import Counter
from typing import Any, List, Dict, Optional, Set, Tuple
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ModuleNotFoundError:
    fitz = None

try:
    from docx import Document
except ModuleNotFoundError:
    Document = None
try:
    import gradio as gr
except ModuleNotFoundError:
    gr = None

project_dir = Path(__file__).resolve().parent
project_venv_python = project_dir / ".venv" / "bin" / "python"
conda_python = Path("/home/maoyd/miniconda3/bin/python")
current_python = Path(sys.executable).resolve()
project_venv_broken_for_milvus = (
    project_venv_python.exists()
    and current_python == project_venv_python.resolve()
)

if (gr is None or fitz is None or project_venv_broken_for_milvus) and os.getenv("SIQ_INGEST_REEXECED") != "1":
    # The project .venv currently times out during pymilvus/gRPC handshake on this aarch64 host.
    # Prefer the conda Python, whose pymilvus stack has been verified against local Milvus.
    python_candidates = [conda_python, project_venv_python]
    for candidate in python_candidates:
        if candidate.exists() and current_python != candidate.resolve():
            os.environ["SIQ_INGEST_REEXECED"] = "1"
            os.execv(str(candidate), [str(candidate), __file__, *sys.argv[1:]])

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None

try:
    import pytesseract
except ModuleNotFoundError:
    pytesseract = None

try:
    from pymilvus import (
        connections,
        Collection,
        utility,
        FieldSchema,
        CollectionSchema,
        DataType,
        MilvusClient,
    )
except ModuleNotFoundError:
    connections = Collection = utility = FieldSchema = CollectionSchema = DataType = MilvusClient = None

# ==================== 配置 (参考 env_setup.py) ====================
MILVUS_HOST = os.getenv("SIQ_MILVUS_HOST", os.getenv("MILVUS_HOST", "localhost")).strip() or "localhost"
MILVUS_PORT = os.getenv("SIQ_MILVUS_PORT", os.getenv("MILVUS_PORT", "19530")).strip() or "19530"
MILVUS_DEFAULT_DB = (
    os.getenv("SIQ_MILVUS_DB_NAME")
    or os.getenv("MILVUS_DB_NAME")
    or "default"
).strip() or "default"
PHYSICAL_SHARED_COLLECTION = "ic_collaboration_shared"
COLLECTION_ALIASES = {
    "siq_deal_shared": PHYSICAL_SHARED_COLLECTION,
    "siq_ic_chairman": "ic_chairman",
    "siq_ic_finance_auditor": "ic_finance_auditor",
    "siq_ic_legal_scanner": "ic_legal_scanner",
    "siq_ic_risk_controller": "ic_risk_controller",
    "siq_ic_sector_expert": "ic_sector_expert",
    "siq_ic_strategist": "ic_strategist",
    "siq_ic_master_coordinator": "ic_master_coordinator",
}

_DEFAULT_COLLECTION_VALUE = (
    os.getenv("SIQ_MILVUS_COLLECTION")
    or os.getenv("MILVUS_COLLECTION")
    or PHYSICAL_SHARED_COLLECTION
).strip() or PHYSICAL_SHARED_COLLECTION
MILVUS_DEFAULT_COLLECTION = COLLECTION_ALIASES.get(_DEFAULT_COLLECTION_VALUE, _DEFAULT_COLLECTION_VALUE)

# HNSW 索引配置 (CPU高性能)
INDEX_TYPE = "HNSW"
INDEX_PARAMS = {
    "metric_type": "L2",
    "index_type": INDEX_TYPE,
    "params": {"M": 32, "efConstruction": 256}
}

# Embedding配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "").strip()
VLLM_EMBED_URL = "http://127.0.0.1:8000/v1/embeddings"
VLLM_EMBED_MODEL = os.getenv("VLLM_EMBED_MODEL", "qwen3-vl-embedding-2b")
VLLM_EMBED_MODEL_FALLBACK = os.getenv("VLLM_EMBED_MODEL_FALLBACK", "Qwen3-VL-Embedding-2B")
MODEL_NAME = "qwen3-vl-embedding"
VECTOR_DIM = 1024
API_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
MINIMAX_EMBED_URL = os.getenv("MINIMAX_EMBED_URL", "https://api.minimax.io/v1/embeddings").strip()
MINIMAX_EMBED_MODEL = os.getenv("MINIMAX_EMBED_MODEL", "embo-01").strip()

# 极速入库参数
CONCURRENT_REQUESTS = 8
DEFAULT_CHUNK_SIZE = 700
DEFAULT_CHUNK_OVERLAP = 120
DEFAULT_MIN_CHUNK_LEN = 90
TITLE_PREFIX_LEN = 80
TIMEOUT_API = 45
VLLM_BATCH_SIZE = 32
MAX_DASHSCOPE_TEXT_CHARS = 2000
OCR_TRIGGER_TEXT_LEN = 40
OCR_MIN_TEXT_LEN = 80
OCR_LANG = os.getenv("OCR_LANG", "chi_sim+eng")
OCR_RENDER_SCALE = 2.0
EMBED_PIPELINE_VERSION = "v8.1"
METADATA_SCHEMA_VERSION = "siq_chunk_v1"
PROGRESS_SAVE_EVERY = 10
QUALITY_REPORT_DIR = project_dir / "ingest_quality_reports"
NOISE_LINE_MIN_REPEATS = 3
NOISE_LINE_MAX_CHARS = 90
VISUAL_EMBED_CONCURRENCY = 4
PDF_PARSE_MODE_AUTO = "自动（共享底稿 MinerU）"
PDF_PARSE_MODE_FAST = "PyMuPDF 快速解析"
PDF_PARSE_MODE_MINERU = "MinerU 高精度解析"
PDF_PARSE_MODE_MINERU_FALLBACK = "MinerU 优先，失败回退 PyMuPDF"
PDF_PARSE_MODES = [
    PDF_PARSE_MODE_AUTO,
    PDF_PARSE_MODE_FAST,
    PDF_PARSE_MODE_MINERU,
    PDF_PARSE_MODE_MINERU_FALLBACK,
]
CHUNK_MODE_AUTO = "默认结构化切片（推荐）"
CHUNK_MODE_MANUAL = "手动固定参数切片"
CHUNK_MODES = [CHUNK_MODE_AUTO, CHUNK_MODE_MANUAL]
MINERU_API_BASE = os.getenv("MINERU_API_URL", "http://127.0.0.1:8003").rstrip("/")
MINERU_VLM_API_BASE = os.getenv("VLM_API_URL", "http://127.0.0.1:8002").rstrip("/")
MINERU_CACHE_DIR = project_dir / ".mineru_ingest_cache"
MINERU_SUBMIT_TIMEOUT_SECONDS = int(os.getenv("MINERU_SUBMIT_TIMEOUT_SECONDS", "900"))
MINERU_STATUS_TIMEOUT_SECONDS = int(os.getenv("MINERU_STATUS_TIMEOUT_SECONDS", "30"))
MINERU_RESULT_TIMEOUT_SECONDS = int(os.getenv("MINERU_RESULT_TIMEOUT_SECONDS", "120"))
MINERU_MAX_WAIT_SECONDS = int(os.getenv("SIQ_INGEST_MINERU_MAX_WAIT_SECONDS", "3600"))
MINERU_POLL_INTERVAL_SECONDS = float(os.getenv("SIQ_INGEST_MINERU_POLL_INTERVAL_SECONDS", "3"))
MINERU_SUCCESS_STATUSES = {"completed", "complete", "success", "succeeded", "finished", "done"}
MINERU_FAILURE_STATUSES = {"failed", "failure", "error", "cancelled", "canceled"}
MAX_TABLE_CHUNK_CHARS = 6000
MAX_VISUAL_CONTEXT_CHARS = 900
GRAPH_SIDECAR_DEFAULT_PREFIX = os.getenv("SIQ_GRAPH_SIDECAR_PREFIX", "siq_project").strip()
GRAPH_ENTITY_COLLECTION = "vgrag_entities"
GRAPH_RELATION_COLLECTION = "vgrag_relations"
GRAPH_PASSAGE_COLLECTION = "vgrag_passages"
GRAPH_INDEX_PARAMS = {"M": 32, "efConstruction": 256}

CHUNK_POLICY_BY_EXT = {
    "pdf": {"size": 760, "overlap": 120, "min_len": 80},
    "pdf_ocr": {"size": 620, "overlap": 100, "min_len": 70},
    "docx": {"size": 900, "overlap": 140, "min_len": 100},
    "md": {"size": 1100, "overlap": 140, "min_len": 120},
    "txt": {"size": 900, "overlap": 120, "min_len": 100},
    "legal": {"size": 900, "overlap": 80, "min_len": 70},
    "finance": {"size": 900, "overlap": 100, "min_len": 80},
    "discussion": {"size": 900, "overlap": 80, "min_len": 80},
    "mineru_md": {"size": 1150, "overlap": 140, "min_len": 80},
    "table": {"size": 1400, "overlap": 80, "min_len": 40},
    "visual": {"size": 700, "overlap": 60, "min_len": 20},
    "default": {"size": DEFAULT_CHUNK_SIZE, "overlap": DEFAULT_CHUNK_OVERLAP, "min_len": DEFAULT_MIN_CHUNK_LEN},
}

DOC_TYPE_PATTERNS = [
    ("legal", ["法律", "法规", "条例", "办法", "规定", "监管", "合规", "协议", "合同", "章程", "term", "legal"]),
    ("financials", ["财务", "审计", "报表", "利润", "收入", "现金流", "估值", "valuation", "fdd", "finance"]),
    ("teaser", ["teaser", "bp", "pitch", "deck", "商业计划", "融资材料", "路演"]),
    ("industry_research", ["研报", "行业", "研究", "market", "research", "产业"]),
    ("meeting_note", ["会议", "纪要", "妙记", "minutes", "访谈", "interview"]),
    ("committee_opinion", ["r1", "r2", "r3", "观点", "裁决", "投委会", "审计链", "discussion"]),
    ("sop", ["sop", "手册", "方法论", "模板", "framework", "playbook"]),
]

EVIDENCE_LEVEL_BY_DOC_TYPE = {
    "legal": "regulation",
    "financials": "source_doc",
    "teaser": "source_doc",
    "industry_research": "research",
    "meeting_note": "expert_opinion",
    "committee_opinion": "expert_opinion",
    "sop": "methodology",
    "default": "source_doc",
}

# 运行时控制（单机场景下用于暂停/继续）
INGEST_CONTROL = {
    "paused": False
}

# 页面刷新后仍可恢复运行日志与统计信息。
RUNTIME_STATE_FILE = project_dir / ".ingest_runtime_state.json"
MAX_RUNTIME_LOG_LINES = 800
INGEST_RUNTIME_LOCK = threading.RLock()
INGEST_RUNTIME = {
    "active": False,
    "started_at": "",
    "updated_at": "",
    "finished_at": "",
    "logs": [],
    "meta": {},
    "config": {},
    "result": "",
    "task": None,
}

logging.basicConfig(level=logging.ERROR)

# Collection命名 (无后缀)
ROLE_REGISTRY = {
    "ic_chairman":             {"desc": "SIQ投委会主席",                    "icon": "👔"},
    "ic_finance_auditor":      {"desc": "SIQ财务审计专家",                  "icon": "💰"},
    "ic_sector_expert":        {"desc": "SIQ行业专家",                      "icon": "🔬"},
    "ic_legal_scanner":        {"desc": "SIQ法务合规专家",                  "icon": "⚖️"},
    "ic_strategist":           {"desc": "SIQ战略专家",                      "icon": "🌐"},
    "ic_risk_controller":      {"desc": "SIQ风险管理专家",                  "icon": "⚠️"},
    "ic_master_coordinator":   {"desc": "SIQ投委会秘书",                    "icon": "📋"},
    "ic_collaboration_shared": {"desc": "协同共享工作区 (项目实时讨论)",      "icon": "🤝"},
    "ic_archive_sop":          {"desc": "机构历史案例库 (SOP资产)",         "icon": "📚"},
}


class AsyncKnowledgeIngestor:
    """异步知识入库器 (参考 async_ingestor.py + env_setup.py)"""
    
    def __init__(
        self,
        collection_name: str,
        reset: bool = False,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        pdf_parse_mode: str = PDF_PARSE_MODE_AUTO,
        enable_table_chunks: bool = True,
        enable_visual_chunks: bool = True,
        graph_sidecar_prefix: Optional[str] = None,
        graph_drop_existing: bool = False,
        db_name: str = MILVUS_DEFAULT_DB,
    ):
        self.db_name = (db_name or MILVUS_DEFAULT_DB).strip() or MILVUS_DEFAULT_DB
        self.collection_name = _normalize_collection_name(collection_name)
        self.role_desc = ROLE_REGISTRY.get(self.collection_name, {}).get("desc", self.collection_name)
        self.progress_file = str(project_dir / f".progress_{self.db_name}_{self.collection_name}.json")
        self.chunk_size = max(256, int(chunk_size))
        # 重叠必须小于切片长度
        self.chunk_overlap = max(0, min(int(chunk_overlap), self.chunk_size - 1))
        self.min_chunk_len = max(40, int(self.chunk_size * 0.12))
        self.http = requests.Session()
        self.vllm_model = self._resolve_vllm_model()
        self.vllm_visual_supported: Optional[bool] = None
        self.pdf_parse_mode = pdf_parse_mode if pdf_parse_mode in PDF_PARSE_MODES else PDF_PARSE_MODE_AUTO
        self.enable_table_chunks = bool(enable_table_chunks)
        self.enable_visual_chunks = bool(enable_visual_chunks)
        self.graph_sidecar_prefix = _normalize_graph_prefix(graph_sidecar_prefix) if graph_sidecar_prefix else ""
        self.graph_collection_names: Dict[str, str] = {}
        self.graph_client = None
        self._init_milvus(reset)
        self._init_graph_sidecar(graph_drop_existing)

    def _collection_role(self) -> str:
        if self.collection_name == "ic_collaboration_shared":
            return "shared"
        if self.collection_name == "ic_archive_sop":
            return "archive"
        if self.collection_name in ROLE_REGISTRY:
            return "private"
        return "custom"

    def _agent_id(self) -> Optional[str]:
        return self.collection_name if self._collection_role() == "private" else None

    @staticmethod
    def _clean_text(text: str) -> str:
        text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\u00a0", " ").replace("\u3000", " ")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        text = re.sub(r"[ \t]+", " ", text)

        raw_lines = [line.rstrip() for line in text.split("\n")]
        normalized_counts = Counter()
        for line in raw_lines:
            normalized = re.sub(r"\s+", " ", line).strip()
            normalized = re.sub(r"\d+", "#", normalized)
            if 0 < len(normalized) <= NOISE_LINE_MAX_CHARS:
                normalized_counts[normalized] += 1

        cleaned_lines = []
        for line in raw_lines:
            stripped = line.strip()
            normalized = re.sub(r"\s+", " ", stripped)
            normalized = re.sub(r"\d+", "#", normalized)
            is_repeated_noise = (
                normalized_counts.get(normalized, 0) >= NOISE_LINE_MIN_REPEATS
                and not re.match(r"^(#{1,6}\s+|第[一二三四五六七八九十百千万0-9]+[章节条款部分])", stripped)
                and not re.search(r"(收入|利润|客户|供应商|股东|诉讼|风险|订单|金额|合计|资产|负债)", stripped)
            )
            is_page_noise = bool(re.match(r"^(第\s*)?\d+\s*(页|/|-|of)\s*\d*$", stripped, re.IGNORECASE))
            is_disclaimer_noise = len(stripped) <= 100 and bool(re.search(
                r"(免责声明|仅供参考|confidential|disclaimer|本文件.*保密|内部资料.*请勿外传|未经.*许可.*不得)",
                stripped,
                re.IGNORECASE,
            ))
            if is_repeated_noise or is_page_noise or is_disclaimer_noise:
                continue
            cleaned_lines.append(line)

        text = "\n".join(cleaned_lines)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text.strip()

    @staticmethod
    def _strip_html(text: str) -> str:
        text = html.unescape(str(text or ""))
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</t[dh]>\s*<t[dh][^>]*>", " | ", text, flags=re.IGNORECASE)
        text = re.sub(r"</tr>\s*<tr[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"[ \t]+", " ", text).strip()

    @staticmethod
    def _extract_table_summary(table_text: str, captions: List[str], footnotes: List[str]) -> Dict[str, Any]:
        cleaned = re.sub(r"\s+", " ", str(table_text or "")).strip()
        rows = [row.strip(" |") for row in str(table_text or "").splitlines() if row.strip()]
        numeric_values = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?%?", cleaned)
        metric_candidates = []
        for row in rows[:12]:
            first_cell = row.split("|", 1)[0].strip()
            if 1 < len(first_cell) <= 40 and not re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?%?", first_cell):
                metric_candidates.append(first_cell)
        units = sorted(set(re.findall(r"(?:单位[:：]\s*)?([万亿]?元|万元|亿元|人民币|美元|%|百分比|股|吨|MW|GW|平方米|㎡)", cleaned)))
        years = sorted(set(re.findall(r"(?:19|20)\d{2}(?:年|年度)?", cleaned)))
        summary_parts = []
        if captions:
            summary_parts.append("标题: " + "；".join(captions[:3]))
        if metric_candidates:
            summary_parts.append("指标: " + "、".join(dict.fromkeys(metric_candidates[:8])))
        if years:
            summary_parts.append("期间: " + "、".join(years[:8]))
        if units:
            summary_parts.append("单位: " + "、".join(units[:6]))
        if numeric_values:
            summary_parts.append("数值样例: " + "、".join(numeric_values[:12]))
        if footnotes:
            summary_parts.append("注释: " + "；".join(footnotes[:3]))
        return {
            "table_summary": " | ".join(summary_parts)[:1600],
            "table_row_count": len(rows),
            "table_metric_candidates": list(dict.fromkeys(metric_candidates[:16])),
            "table_units": units[:10],
            "table_years": years[:12],
            "table_numeric_samples": numeric_values[:24],
        }

    @staticmethod
    def _visual_caption_text(
        block_type: str,
        source: str,
        page: Optional[int],
        captions: List[str],
        footnotes: List[str],
        context: str,
    ) -> str:
        parts = [f"[视觉证据] {source} 第{page or '?'}页", f"类型: {block_type}"]
        if captions:
            parts.append("caption: " + "；".join(captions[:4]))
        if context:
            parts.append("邻近文本: " + context)
        if footnotes:
            parts.append("注释: " + "；".join(footnotes[:3]))
        return "\n".join(parts)

    @staticmethod
    def _coerce_text_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            output = []
            for item in value:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("caption")
                else:
                    text = item
                text = str(text or "").strip()
                if text:
                    output.append(text)
            return output
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _read_json_file(path: Path) -> Any:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return payload
        return payload

    @staticmethod
    def _infer_doc_type(path: str, explicit_format: str = "") -> str:
        file_obj = Path(path)
        haystack = " ".join([
            file_obj.name,
            str(file_obj.parent),
            explicit_format,
        ]).lower()
        for doc_type, patterns in DOC_TYPE_PATTERNS:
            if any(p.lower() in haystack for p in patterns):
                return doc_type
        return "default"

    @staticmethod
    def _policy_key_for_doc_type(file_ext: str, doc_type: str) -> str:
        if file_ext in {"mineru_md", "table", "visual"}:
            return file_ext
        if doc_type in {"legal", "financials", "meeting_note", "committee_opinion"}:
            return {
                "legal": "legal",
                "financials": "finance",
                "meeting_note": "discussion",
                "committee_opinion": "discussion",
            }[doc_type]
        return file_ext or "default"

    @staticmethod
    def _line_offsets(text: str) -> List[Tuple[int, str]]:
        offsets: List[Tuple[int, str]] = []
        cursor = 0
        for line in text.splitlines(True):
            offsets.append((cursor, line.rstrip("\n")))
            cursor += len(line)
        return offsets

    @staticmethod
    def _page_for_offset(line_offsets: List[Tuple[int, str]], char_start: int) -> Optional[int]:
        page = None
        for offset, line in line_offsets:
            if offset > char_start:
                break
            match = re.match(
                r"^\s*(?:\[PDF_PAGE:\s*(\d+)\]|<!--\s*PDF_PAGE:\s*(\d+)\s*-->)\s*$",
                line,
                flags=re.IGNORECASE,
            )
            if match:
                page = int(match.group(1) or match.group(2))
        return page

    @staticmethod
    def _section_path_for_offset(line_offsets: List[Tuple[int, str]], char_start: int) -> str:
        headings: List[Tuple[int, str]] = []
        for offset, line in line_offsets:
            if offset > char_start:
                break
            stripped = line.strip()
            if not stripped:
                continue

            md = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if md:
                level = len(md.group(1))
                title = md.group(2).strip()
            else:
                cn = re.match(r"^第[一二三四五六七八九十百千万0-9]+[章节条款部分][、：:\s]*(.+)?$", stripped)
                if cn:
                    marker = stripped[: min(len(stripped), 40)]
                    level = 2 if "章" in stripped or "节" in stripped else 3
                    title = marker
                else:
                    continue

            title = re.sub(r"\s+", " ", title).strip(" #")
            if not title:
                continue
            headings = [(lvl, val) for lvl, val in headings if lvl < level]
            headings.append((level, title[:80]))

        return " / ".join(title for _, title in headings[-4:])

    def _structured_units(self, text: str, policy_key: str) -> List[Tuple[str, int, int]]:
        """
        结构优先切分：Markdown/法规/会议材料优先按标题、法条、发言块切。
        返回的 unit 可能仍大于 chunk_size，后续再做长度兜底。
        """
        if policy_key not in {"md", "legal", "discussion", "mineru_md"}:
            return []

        text = self._clean_text(text)
        if not text:
            return []

        boundaries = [0]
        patterns = [
            r"(?m)^\[PDF_PAGE:\s*\d+\]\s*$",
            r"(?m)^<!--\s*PDF_PAGE:\s*\d+\s*-->\s*$",
            r"(?m)^#{1,6}\s+.+$",
            r"(?m)^第[一二三四五六七八九十百千万0-9]+[章节条款部分][^\n]*$",
        ]
        if policy_key == "discussion":
            patterns.extend([
                r"(?m)^【[^】]{1,40}】",
                r"(?m)^[A-Za-z0-9_\-\u4e00-\u9fff]{2,30}[：:]\s*",
            ])

        for pat in patterns:
            for m in re.finditer(pat, text):
                if m.start() > 0:
                    boundaries.append(m.start())
        boundaries = sorted(set(boundaries + [len(text)]))

        units: List[Tuple[str, int, int]] = []
        for start, end in zip(boundaries, boundaries[1:]):
            unit = text[start:end].strip()
            if unit:
                leading_ws = len(text[start:end]) - len(text[start:end].lstrip())
                trailing_ws = len(text[start:end]) - len(text[start:end].rstrip())
                units.append((unit, start + leading_ws, end - trailing_ws))
        return units if len(units) > 1 else []

    def _split_unit_by_length(
        self, unit: str, base_start: int, policy_key: str
    ) -> List[Tuple[str, int, int]]:
        policy = self._chunk_policy_for_ext(policy_key)
        chunk_size = policy["size"]
        chunk_overlap = policy["overlap"]
        # 结构化块可能很短，但常包含标题、条款或关键事实，不能按普通长文本阈值过滤。
        min_chunk_len = min(policy["min_len"], 30) if policy_key in {"md", "legal", "discussion", "mineru_md"} else policy["min_len"]

        if len(unit) <= chunk_size:
            return [(unit, base_start, base_start + len(unit))] if len(unit) >= min_chunk_len else []

        chunks: List[Tuple[str, int, int]] = []
        start = 0
        split_seps = ("\n\n", "\n", "。", "！", "？", "；", ". ", "; ", ", ", " ")
        while start < len(unit):
            end = min(start + chunk_size, len(unit))
            if end < len(unit):
                window = unit[start:end]
                for sep in split_seps:
                    pos = window.rfind(sep)
                    if pos >= int(chunk_size * 0.55):
                        end = start + pos + len(sep)
                        break
            chunk = unit[start:end].strip()
            if len(chunk) >= min_chunk_len:
                lead = len(unit[start:end]) - len(unit[start:end].lstrip())
                trail = len(unit[start:end]) - len(unit[start:end].rstrip())
                chunks.append((chunk, base_start + start + lead, base_start + end - trail))
            start = end - chunk_overlap if end < len(unit) else end
        return chunks

    def _build_embed_text(self, meta: Dict[str, Any], chunk: str) -> str:
        parts = [
            str(meta.get("source", "")),
            str(meta.get("section_path", "")),
            str(meta.get("doc_type", "")),
            str(meta.get("project_tag", "")),
            chunk,
        ]
        return "\n".join(p for p in parts if p)

    def _finalize_metadata(
        self,
        meta: Dict[str, Any],
        batch_tag: str,
        embed_backend: str,
    ) -> Dict[str, Any]:
        finalized = dict(meta)
        text = str(finalized.get("text", "")).strip()
        source_path = str(finalized.get("source_path", ""))
        source = str(finalized.get("source", Path(source_path).name if source_path else "unknown"))
        doc_type = str(finalized.get("doc_type") or self._infer_doc_type(source_path or source, finalized.get("format", "")))

        finalized.update({
            "schema_version": METADATA_SCHEMA_VERSION,
            "project_tag": batch_tag,
            "collection": self.collection_name,
            "collection_role": self._collection_role(),
            "agent_id": self._agent_id(),
            "doc_type": doc_type,
            "evidence_level": finalized.get("evidence_level") or EVIDENCE_LEVEL_BY_DOC_TYPE.get(doc_type, "source_doc"),
            "language": finalized.get("language") or self._infer_language(text),
            "ingest_version": EMBED_PIPELINE_VERSION,
            "embedding_backend": embed_backend,
            "embedding_model": self.vllm_model if embed_backend == "vllm (本地)" else (
                MINIMAX_EMBED_MODEL if embed_backend == "MiniMax (云端)" else MODEL_NAME
            ),
            "vector_dim": VECTOR_DIM,
            "source": source,
            "source_path": source_path,
            "text": text,
            "text_len": len(text),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })

        chunk_identity = {
            "collection": self.collection_name,
            "project_tag": batch_tag,
            "source_path": source_path,
            "chunk_index": finalized.get("chunk_index"),
            "text_sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),
        }
        finalized["chunk_uid"] = hashlib.sha1(
            json.dumps(chunk_identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        finalized["text_sha1"] = chunk_identity["text_sha1"]
        finalized["citation"] = self._build_citation(finalized)
        return finalized

    @staticmethod
    def _infer_language(text: str) -> str:
        if not text:
            return "unknown"
        zh = len(re.findall(r"[\u4e00-\u9fff]", text))
        ascii_letters = len(re.findall(r"[A-Za-z]", text))
        if zh >= max(10, ascii_letters * 0.4):
            return "zh"
        if ascii_letters > 0:
            return "en"
        return "unknown"

    @staticmethod
    def _build_citation(meta: Dict[str, Any]) -> str:
        source = meta.get("source", "unknown")
        page = meta.get("page")
        section = meta.get("section_path")
        chunk_index = meta.get("chunk_index")
        bits = [str(source)]
        if page:
            bits.append(f"p.{page}")
        if section:
            bits.append(str(section))
        if chunk_index:
            bits.append(f"chunk {chunk_index}")
        return " | ".join(bits)

    @staticmethod
    def _validate_metadata(meta: Dict[str, Any]) -> Tuple[bool, str]:
        required = ["schema_version", "text", "source", "project_tag", "collection", "chunk_uid"]
        missing = [key for key in required if not meta.get(key)]
        if missing:
            return False, f"metadata missing: {', '.join(missing)}"
        if len(str(meta.get("text", "")).strip()) < 5:
            return False, "metadata text too short"
        return True, ""

    def _insert_graph_passages(
        self,
        vectors: List[List[float]],
        metas: List[Dict[str, Any]],
    ) -> int:
        """Mirror shared project chunks into the Vector Graph RAG passage collection."""
        if not self.graph_client or not self.graph_collection_names:
            return 0

        passage_collection = self.graph_collection_names["passages"]
        rows = []
        for vector, meta in zip(vectors, metas):
            text = str(meta.get("text", "")).strip()
            if not text:
                continue
            row = {
                "id": str(meta.get("chunk_uid")),
                "text": text[:65535],
                "vector": vector,
                "project_tag": meta.get("project_tag"),
                "source": meta.get("source"),
                "source_path": meta.get("source_path"),
                "page": meta.get("page"),
                "section_path": meta.get("section_path"),
                "chunk_uid": meta.get("chunk_uid"),
                "text_sha1": meta.get("text_sha1"),
                "citation": meta.get("citation"),
                "doc_type": meta.get("doc_type"),
                "evidence_level": meta.get("evidence_level"),
                "type": meta.get("type"),
                "modality": meta.get("modality"),
                "parser": meta.get("parser"),
                "image_path": meta.get("image_path"),
                "collection": meta.get("collection"),
                "entity_ids": [],
                "relation_ids": [],
            }
            rows.append({k: v for k, v in row.items() if v is not None})

        if not rows:
            return 0

        if hasattr(self.graph_client, "upsert"):
            self.graph_client.upsert(collection_name=passage_collection, data=rows)
        else:
            self.graph_client.insert(collection_name=passage_collection, data=rows)
        return len(rows)

    def _write_quality_report(
        self,
        file_path: str,
        batch_tag: str,
        metas: List[Dict[str, Any]],
        skipped_count: int = 0,
    ) -> None:
        if not metas:
            return
        QUALITY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        type_counts = Counter(str(meta.get("type", "unknown")) for meta in metas)
        modality_counts = Counter(str(meta.get("modality", "unknown")) for meta in metas)
        missing_page = sum(1 for meta in metas if not meta.get("page"))
        missing_section = sum(1 for meta in metas if not meta.get("section_path") and meta.get("type") == "text_chunk")
        short_text = sum(1 for meta in metas if int(meta.get("text_len") or len(str(meta.get("text", "")))) < 80)
        report = {
            "source_path": str(file_path),
            "source": Path(file_path).name,
            "collection": self.collection_name,
            "project_tag": batch_tag,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "inserted_chunks": len(metas),
            "skipped_chunks": skipped_count,
            "type_counts": dict(type_counts),
            "modality_counts": dict(modality_counts),
            "missing_page_count": missing_page,
            "missing_section_count": missing_section,
            "short_text_count": short_text,
            "has_table_chunks": type_counts.get("table_chunk", 0) > 0,
            "has_visual_chunks": type_counts.get("visual_chunk", 0) > 0,
            "sample_citations": [meta.get("citation") for meta in metas[:8]],
            "sample_chunks": [
                {
                    "type": meta.get("type"),
                    "modality": meta.get("modality"),
                    "page": meta.get("page"),
                    "section_path": meta.get("section_path"),
                    "citation": meta.get("citation"),
                    "text_preview": str(meta.get("text", ""))[:240],
                }
                for meta in metas[:8]
            ],
        }
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(file_path).stem)[:80] or "document"
        digest = hashlib.sha1(str(file_path).encode("utf-8")).hexdigest()[:10]
        out_path = QUALITY_REPORT_DIR / f"{datetime.now().strftime('%Y%m%d')}_{self.collection_name}_{safe_name}_{digest}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    
    def _init_milvus(self, reset: bool):
        """初始化Milvus (参考 env_setup.py)"""
        if connections is None:
            raise RuntimeError("未安装 pymilvus，请先安装 `pip install pymilvus`")

        _ensure_milvus_connection(self.db_name)
        
        if reset:
            if utility.has_collection(self.collection_name):
                self._record_reset_manifest(self.collection_name)
                try:
                    Collection(self.collection_name).release()
                except Exception:
                    pass
                utility.drop_collection(self.collection_name)
            if os.path.exists(self.progress_file):
                os.remove(self.progress_file)
        
        if not utility.has_collection(self.collection_name):
            # 字段定义 (参考 env_setup.py)
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
                FieldSchema(name="project_tag", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="metadata", dtype=DataType.JSON)
            ]
            
            schema = CollectionSchema(fields, description=self.role_desc)
            collection = Collection(self.collection_name, schema)
            
            # 创建索引 (参考 env_setup.py)
            collection.create_index(field_name="vector", index_params=INDEX_PARAMS)
            collection.create_index(
                field_name="project_tag", 
                index_params={"index_type": "INVERTED"}
            )
        
        self.collection = Collection(self.collection_name)
        self.collection.load()

    def _init_graph_sidecar(self, drop_existing: bool = False):
        if not self.graph_sidecar_prefix:
            return
        if self.collection_name != "ic_collaboration_shared":
            raise ValueError("Graph sidecar 当前仅支持项目共享底稿库 ic_collaboration_shared")

        result = _ensure_graph_sidecar_collections(
            self.graph_sidecar_prefix,
            dimension=VECTOR_DIM,
            drop_existing=drop_existing,
            db_name=self.db_name,
        )
        self.graph_collection_names = result["collections"]
        self.graph_client = _milvus_client(self.db_name)

    def _record_reset_manifest(self, target: str):
        """重建前记录轻量 manifest，便于追溯本次清库动作。"""
        manifest_dir = project_dir / "reset_manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "action": "drop_collection_for_rebuild",
            "collection": target,
            "database": self.db_name,
            "requested_collection": self.collection_name,
            "schema_version_next": METADATA_SCHEMA_VERSION,
            "pipeline_version_next": EMBED_PIPELINE_VERSION,
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            col = Collection(target)
            payload["entities_before_drop"] = int(col.num_entities)
            payload["indexes_before_drop"] = [
                {
                    "field_name": idx.field_name,
                    "index_type": idx.params.get("index_type"),
                    "metric_type": idx.params.get("metric_type"),
                }
                for idx in col.indexes
            ]
        except Exception as e:
            payload["inspect_error"] = str(e)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = manifest_dir / f"{stamp}_{target}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _resolve_vllm_model(self) -> str:
        """优先使用用户配置模型名，若大小写不一致则自动匹配本地已加载模型"""
        preferred = (VLLM_EMBED_MODEL or "").strip()
        fallback = (VLLM_EMBED_MODEL_FALLBACK or "").strip()

        candidates = []
        for name in [preferred, fallback]:
            if name and name not in candidates:
                candidates.append(name)

        try:
            resp = self.http.get(VLLM_EMBED_URL.replace("/embeddings", "/models"), timeout=8)
            if resp.status_code == 200:
                model_ids = [str(m.get("id", "")).strip() for m in resp.json().get("data", [])]
                if model_ids:
                    model_map = {m.lower(): m for m in model_ids if m}
                    for candidate in candidates:
                        matched = model_map.get(candidate.lower())
                        if matched:
                            return matched
                    # 兜底使用第一个已加载模型，避免模型名配置错误导致全量失败
                    return model_ids[0]
        except Exception:
            pass

        return fallback or preferred or "Qwen3-VL-Embedding-2B"

    def _chunk_policy_for_ext(self, file_ext: str) -> Dict[str, int]:
        # 以用户滑块为全局基准，再按文档类型做比例调整。
        default_policy = CHUNK_POLICY_BY_EXT["default"]
        type_policy = CHUNK_POLICY_BY_EXT.get(file_ext, default_policy)
        size_ratio = type_policy["size"] / default_policy["size"]
        overlap_ratio = type_policy["overlap"] / max(default_policy["overlap"], 1)
        min_ratio = type_policy["min_len"] / max(default_policy["min_len"], 1)

        size = int(self.chunk_size * size_ratio)
        overlap = int(self.chunk_overlap * overlap_ratio)
        min_len = int(self.min_chunk_len * min_ratio)

        size = max(256, size)
        overlap = max(0, min(overlap, size - 1))
        min_len = max(30, min(min_len, size))
        return {"size": size, "overlap": overlap, "min_len": min_len}

    def _pipeline_signature(self, embed_backend: str, batch_tag: str = "") -> str:
        payload = {
            "version": EMBED_PIPELINE_VERSION,
            "metadata_schema": METADATA_SCHEMA_VERSION,
            "collection": self.collection_name,
            "batch_tag": batch_tag,
            "graph_sidecar_prefix": self.graph_sidecar_prefix,
            "embed_backend": embed_backend,
            "vllm_model": self.vllm_model,
            "dashscope_model": MODEL_NAME,
            "minimax_model": MINIMAX_EMBED_MODEL,
            "minimax_url": MINIMAX_EMBED_URL,
            "vector_dim": VECTOR_DIM,
            "chunk_runtime": {
                "size": self.chunk_size,
                "overlap": self.chunk_overlap,
                "min_len": self.min_chunk_len,
            },
        "ocr": {
            "trigger_len": OCR_TRIGGER_TEXT_LEN,
            "min_len": OCR_MIN_TEXT_LEN,
            "lang": OCR_LANG,
            "render_scale": OCR_RENDER_SCALE,
            "pdf_parse_mode": self.pdf_parse_mode,
            "mineru_api_base": MINERU_API_BASE,
            "mineru_vlm_api_base": MINERU_VLM_API_BASE,
            "table_chunks": self.enable_table_chunks,
            "visual_chunks": self.enable_visual_chunks,
        },
            "title_prefix_len": TITLE_PREFIX_LEN,
            "chunk_policy_by_ext": CHUNK_POLICY_BY_EXT,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_vector(raw_vec: List[float], backend_name: str) -> List[float]:
        arr = np.asarray(raw_vec, dtype=np.float32)
        if arr.ndim != 1:
            raise ValueError(f"{backend_name} 返回了非一维 embedding")
        if arr.shape[0] != VECTOR_DIM:
            raise ValueError(f"{backend_name} 向量维度为 {arr.shape[0]}，当前 Collection 需要 {VECTOR_DIM}")
        arr = arr / (np.linalg.norm(arr) + 1e-12)
        return arr.tolist()

    @staticmethod
    def _emit_progress(progress_callback, msg: str, meta: Optional[Dict] = None):
        if not progress_callback:
            return
        try:
            progress_callback(msg, meta or {})
        except TypeError:
            progress_callback(msg)
    
    async def _fetch_embedding_vllm(self, texts: List[str]) -> List[Optional[List[float]]]:
        """vllm批量embedding + 向量归一化"""
        results = []
        
        for i in range(0, len(texts), VLLM_BATCH_SIZE):
            chunk = texts[i:i + VLLM_BATCH_SIZE]
            try:
                resp = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.http.post(VLLM_EMBED_URL, json={
                        "model": self.vllm_model,
                        "input": chunk,
                    }, timeout=120)
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                data.sort(key=lambda x: x["index"])
                
                for d in data:
                    results.append(self._normalize_vector(d["embedding"], "vLLM"))
                    
            except Exception:
                for _ in chunk:
                    results.append(None)
                    
        return results

    async def _fetch_embedding_vllm_visual(self, image_data_url: str) -> Optional[List[float]]:
        """
        尝试通过 vLLM 多模态 embedding 接口对图片做向量化。
        若当前服务仅支持文本 embedding，会自动降级返回 None。
        """
        if self.vllm_visual_supported is False:
            return None

        payload_candidates = [
            {
                "model": self.vllm_model,
                "input": [{
                    "content": [
                        {"type": "text", "text": "visual content"},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ]
                }],
            },
            {
                "model": self.vllm_model,
                "input": [[
                    {"type": "text", "text": "visual content"},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ]],
            },
            {
                "model": self.vllm_model,
                "input": [{
                    "type": "text",
                    "text": "visual content",
                    "image": image_data_url,
                }],
            },
        ]

        for payload in payload_candidates:
            try:
                resp = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.http.post(VLLM_EMBED_URL, json=payload, timeout=TIMEOUT_API)
                )
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data", [])
                if not data:
                    continue
                self.vllm_visual_supported = True
                return self._normalize_vector(data[0]["embedding"], "vLLM visual")
            except Exception:
                continue

        self.vllm_visual_supported = False
        return None
    
    async def _fetch_embedding_dashscope(
        self, items: List[Dict], semaphore: asyncio.Semaphore, api_key: str
    ) -> List[Optional[List[float]]]:
        """DashScope异步embedding + 向量归一化 (requests + executor)"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        async def fetch_single(item: Dict) -> Optional[List[float]]:
            if item["type"] == "image":
                content = {"text": "visual content", "image": item["image"]}
            else:
                content = {"text": item["content"][:MAX_DASHSCOPE_TEXT_CHARS]}
            
            payload = {
                "model": MODEL_NAME,
                "input": {"contents": [content]},
                "parameters": {"dimension": VECTOR_DIM}
            }
            
            async with semaphore:
                for attempt in range(3):
                    try:
                        resp = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.http.post(
                                API_ENDPOINT,
                                headers=headers,
                                json=payload,
                                timeout=TIMEOUT_API
                            )
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            vec = data["output"]["embeddings"][0]["embedding"]
                            return self._normalize_vector(vec, "DashScope")
                        if resp.status_code == 429:
                            await asyncio.sleep(2 ** attempt)
                        else:
                            await asyncio.sleep(1)
                    except Exception:
                        await asyncio.sleep(1)
                return None
        
        tasks = [fetch_single(item) for item in items]
        return await asyncio.gather(*tasks)

    async def _fetch_embedding_minimax(
        self, texts: List[str], semaphore: asyncio.Semaphore, api_key: str, model_name: str
    ) -> List[Optional[List[float]]]:
        """MiniMax OpenAI-compatible embeddings 接口"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async def fetch_single(text: str) -> Optional[List[float]]:
            payload = {
                "model": model_name,
                "input": text[:MAX_DASHSCOPE_TEXT_CHARS],
            }
            async with semaphore:
                for attempt in range(3):
                    try:
                        resp = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.http.post(
                                MINIMAX_EMBED_URL,
                                headers=headers,
                                json=payload,
                                timeout=TIMEOUT_API,
                            )
                        )
                        if resp.status_code == 200:
                            data = resp.json().get("data", [])
                            if not data:
                                return None
                            return self._normalize_vector(data[0]["embedding"], "MiniMax")
                        if resp.status_code in (429, 503):
                            await asyncio.sleep(2 ** attempt)
                        else:
                            await asyncio.sleep(1)
                    except Exception:
                        await asyncio.sleep(1)
                return None

        return await asyncio.gather(*[fetch_single(text) for text in texts])
    
    def _smart_chunk(self, text: str, file_ext: str = "default") -> List[Tuple[str, int, int]]:
        """结构化分块：按文件类型动态切分，优先段落/句子边界"""
        policy = self._chunk_policy_for_ext(file_ext)
        chunk_size = policy["size"]
        chunk_overlap = policy["overlap"]
        min_chunk_len = policy["min_len"]

        text = self._clean_text(text)
        if not text:
            return []

        structured_units = self._structured_units(text, file_ext)
        if structured_units:
            chunks: List[Tuple[str, int, int]] = []
            for unit, unit_start, _ in structured_units:
                chunks.extend(self._split_unit_by_length(unit, unit_start, file_ext))
            if chunks:
                return chunks

        chunks: List[Tuple[str, int, int]] = []
        text_len = len(text)
        start = 0
        split_seps = ("\n\n", "\n", "。", "！", "？", "；", ". ", "; ", ", ", " ")

        while start < text_len:
            end = min(start + chunk_size, text_len)
            if end < text_len:
                window = text[start:end]
                for sep in split_seps:
                    pos = window.rfind(sep)
                    if pos >= int(chunk_size * 0.55):
                        end = start + pos + len(sep)
                        break

            chunk = text[start:end].strip()
            if len(chunk) >= min_chunk_len:
                chunks.append((chunk, start, end))

            start = end - chunk_overlap if end < text_len else end

        return chunks

    def _split_text(self, text: str, file_path: str, meta_extra: Dict = None) -> List[Dict]:
        """文本切块并补全可检索 metadata"""
        items = []
        file_obj = Path(file_path)
        fname = file_obj.name
        inferred_ext = file_obj.suffix.lower().lstrip(".") or "default"
        format_ext = inferred_ext
        if meta_extra and str(meta_extra.get("format", "")).strip():
            format_ext = str(meta_extra["format"]).strip()

        doc_type = self._infer_doc_type(str(file_obj), format_ext)
        policy_key = self._policy_key_for_doc_type(format_ext, doc_type)
        policy = self._chunk_policy_for_ext(policy_key)
        clean_text = self._clean_text(text)
        chunks = self._smart_chunk(clean_text, policy_key)
        if not chunks:
            return items

        total_chunks = len(chunks)
        line_offsets = self._line_offsets(clean_text)
        default_page = meta_extra.get("page") if meta_extra else None
        parent_groups: Dict[str, List[Tuple[int, str]]] = {}
        chunk_sections: List[str] = []
        for idx, (chunk, cstart, _) in enumerate(chunks, start=1):
            section = self._section_path_for_offset(line_offsets, cstart) or "(root)"
            chunk_sections.append(section)
            parent_groups.setdefault(section, []).append((idx, chunk))

        for idx, (chunk, cstart, cend) in enumerate(chunks, start=1):
            section_path = chunk_sections[idx - 1] if idx - 1 < len(chunk_sections) else ""
            page = default_page or self._page_for_offset(line_offsets, cstart)
            parent_key = section_path or "(root)"
            parent_items = parent_groups.get(parent_key, [])
            parent_text = self._clean_text("\n".join(text for _, text in parent_items))[:2200]
            parent_identity = {
                "source_path": str(file_obj),
                "section_path": parent_key,
                "projected_format": format_ext,
            }
            meta = {
                "type": "text_chunk",
                "modality": "text",
                "schema_version": METADATA_SCHEMA_VERSION,
                "source": fname,
                "source_path": str(file_obj),
                "file_stem": file_obj.stem,
                "file_ext": file_obj.suffix.lower().lstrip("."),
                "format": format_ext,
                "doc_type": doc_type,
                "section_path": section_path,
                "page": page,
                "chunk_index": idx,
                "total_chunks": total_chunks,
                "parent_id": hashlib.sha1(json.dumps(parent_identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                "parent_type": "section",
                "parent_chunk_count": len(parent_items),
                "parent_text_preview": parent_text,
                "neighbor_prev_index": idx - 1 if idx > 1 else None,
                "neighbor_next_index": idx + 1 if idx < total_chunks else None,
                "char_start": cstart,
                "char_end": cend,
                "chunk_size": policy["size"],
                "chunk_overlap": policy["overlap"],
                "text": chunk,
            }
            if meta_extra:
                meta.update(meta_extra)

            # 通过来源、章节、项目标签和正文组合增强实体命中和跨文档区分能力。
            items.append({
                "type": "text",
                "content": chunk,
                "embed_text": self._build_embed_text(meta, chunk),
                "meta": meta
            })

        return items

    def _ocr_text_from_page(self, page) -> str:
        """扫描页OCR文本抽取（本地可选，优先服务vLLM文本入库）"""
        if fitz is None or Image is None or pytesseract is None:
            return ""

        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(OCR_RENDER_SCALE, OCR_RENDER_SCALE))
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(image, lang=OCR_LANG)
            text = re.sub(r"\s+", " ", text).strip()
            return text
        except Exception:
            return ""
    
    def _mineru_cache_dir(self, pdf_path: str) -> Path:
        file_obj = Path(pdf_path).resolve()
        try:
            stat = file_obj.stat()
            identity = f"{file_obj}:{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            identity = str(file_obj)
        digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
        return MINERU_CACHE_DIR / f"{file_obj.stem}_{digest}"

    def _save_mineru_images(self, images: Any, images_dir: Path) -> int:
        if not isinstance(images, dict):
            return 0
        images_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for name, payload in images.items():
            data = None
            if isinstance(payload, bytes):
                data = payload
            elif payload is not None:
                raw = str(payload)
                if raw.lower().startswith("data:") and "," in raw:
                    raw = raw.split(",", 1)[1]
                try:
                    data = base64.b64decode(raw, validate=False)
                except Exception:
                    data = None
            if not data:
                continue
            safe_name = os.path.basename(str(name)) or f"image_{saved + 1}.jpg"
            if not os.path.splitext(safe_name)[1]:
                safe_name += ".jpg"
            with open(images_dir / safe_name, "wb") as f:
                f.write(data)
            saved += 1
        return saved

    def _mineru_submit_and_fetch(self, pdf_path: str) -> Dict[str, Any]:
        fields = {
            "backend": "hybrid-http-client",
            "parse_method": "auto",
            "formula_enable": "true",
            "table_enable": "true",
            "server_url": MINERU_VLM_API_BASE,
            "return_md": "true",
            "return_middle_json": "true",
            "return_model_output": "true",
            "return_content_list": "true",
            "return_images": "true",
            "response_format_zip": "false",
            "return_original_file": "false",
            "lang_list": "ch",
        }
        with open(pdf_path, "rb") as f:
            resp = self.http.post(
                f"{MINERU_API_BASE}/tasks",
                data=fields,
                files={"files": (Path(pdf_path).name, f, "application/pdf")},
                timeout=MINERU_SUBMIT_TIMEOUT_SECONDS,
            )
        resp.raise_for_status()
        task_id = str(resp.json().get("task_id") or "")
        if not task_id:
            raise RuntimeError("MinerU 未返回 task_id")
        deadline = time.time() + MINERU_MAX_WAIT_SECONDS
        last_status = "submitted"
        while time.time() < deadline:
            status_resp = self.http.get(f"{MINERU_API_BASE}/tasks/{task_id}", timeout=MINERU_STATUS_TIMEOUT_SECONDS)
            status_resp.raise_for_status()
            status_payload = status_resp.json()
            last_status = str(status_payload.get("status") or status_payload.get("state") or last_status).lower()
            if last_status in MINERU_SUCCESS_STATUSES:
                result_resp = self.http.get(f"{MINERU_API_BASE}/tasks/{task_id}/result", timeout=MINERU_RESULT_TIMEOUT_SECONDS)
                result_resp.raise_for_status()
                payload = result_resp.json()
                payload["_task_id"] = task_id
                return payload
            if last_status in MINERU_FAILURE_STATUSES:
                raise RuntimeError(f"MinerU 解析失败: {status_payload}")
            time.sleep(MINERU_POLL_INTERVAL_SECONDS)
        raise TimeoutError(f"等待 MinerU 解析超时，最后状态: {last_status}")

    def _load_or_run_mineru(self, pdf_path: str) -> Dict[str, Any]:
        cache_dir = self._mineru_cache_dir(pdf_path)
        md_path = cache_dir / "result.md"
        content_path = cache_dir / "content_list.json"
        if md_path.exists():
            content_list = self._read_json_file(content_path) if content_path.exists() else []
            return {"markdown": md_path.read_text(encoding="utf-8", errors="ignore"), "content_list": content_list if isinstance(content_list, list) else [], "cache_dir": cache_dir, "from_cache": True}
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = self._mineru_submit_and_fetch(pdf_path)
        selected = None
        selected_name = ""
        for name, data in (payload.get("results") or {}).items():
            if isinstance(data, dict) and data.get("md_content") is not None:
                selected = data
                selected_name = str(name)
                break
        if not selected:
            raise RuntimeError("MinerU result 中没有 md_content")
        markdown = str(selected.get("md_content") or "")
        content_list = selected.get("content_list") or []
        if isinstance(content_list, str):
            try:
                content_list = json.loads(content_list)
            except json.JSONDecodeError:
                content_list = []
        md_path.write_text(markdown, encoding="utf-8")
        with open(content_path, "w", encoding="utf-8") as f:
            json.dump(content_list, f, ensure_ascii=False, indent=2)
        if selected.get("middle_json") is not None:
            with open(cache_dir / "middle.json", "w", encoding="utf-8") as f:
                json.dump(selected.get("middle_json"), f, ensure_ascii=False, indent=2)
        self._save_mineru_images(selected.get("images"), cache_dir / "images")
        with open(cache_dir / "result_payload_summary.json", "w", encoding="utf-8") as f:
            json.dump({"task_id": payload.get("_task_id"), "result_file": selected_name, "source_pdf": str(Path(pdf_path).resolve()), "cached_at": datetime.now().isoformat(timespec="seconds")}, f, ensure_ascii=False, indent=2)
        return {"markdown": markdown, "content_list": content_list if isinstance(content_list, list) else [], "cache_dir": cache_dir, "from_cache": False}

    def _content_list_context(self, content_list: List[Dict[str, Any]], index: int, window: int = 2) -> str:
        if not isinstance(content_list, list):
            return ""
        parts = []
        for item in content_list[max(0, index - window):min(len(content_list), index + window + 1)]:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")) in {"header", "footer", "page_number"}:
                continue
            text = item.get("text")
            if not text and item.get("list_items"):
                text = "；".join(self._coerce_text_list(item.get("list_items")))
            text = str(text or "").strip()
            if text:
                parts.append(text)
        return self._clean_text("\n".join(parts))[:MAX_VISUAL_CONTEXT_CHARS]

    def _image_data_url_from_path(self, image_path: Path) -> Optional[str]:
        if not image_path.exists():
            return None
        mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _mineru_table_visual_items(self, pdf_path: str, content_list: List[Dict[str, Any]], cache_dir: Path) -> List[Dict]:
        items = []
        file_obj = Path(pdf_path)
        if not isinstance(content_list, list):
            return items
        table_index = 0
        visual_index = 0
        seen_visual_paths: Set[str] = set()
        for idx, block in enumerate(content_list):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "")).lower()
            page = int(block.get("page_idx", -1)) + 1 if block.get("page_idx") is not None else None
            img_rel = str(block.get("img_path") or block.get("image_path") or "").strip()
            image_path = cache_dir / img_rel if img_rel else None
            image_path_str = str(image_path) if image_path else ""
            if block_type == "table" and self.enable_table_chunks:
                captions = self._coerce_text_list(block.get("table_caption"))
                footnotes = self._coerce_text_list(block.get("table_footnote"))
                body = str(block.get("table_body") or "")
                body_text = self._strip_html(body)
                table_summary = self._extract_table_summary(body_text, captions, footnotes)
                text_value = self._clean_text("\n".join([
                    "[表格证据]",
                    table_summary.get("table_summary", ""),
                    *captions,
                    body_text,
                    *footnotes,
                ]))[:MAX_TABLE_CHUNK_CHARS]
                if len(text_value) >= 5:
                    table_index += 1
                    meta = {"type": "table_chunk", "modality": "table", "source": file_obj.name, "source_path": str(file_obj), "file_stem": file_obj.stem, "file_ext": "pdf", "format": "table", "parser": "mineru", "page": page, "chunk_index": table_index, "table_index": table_index, "table_caption": captions, "table_footnote": footnotes, "table_html": body[:20000], "image_path": image_path_str, "bbox": block.get("bbox"), "text": text_value, **table_summary}
                    items.append({"type": "text", "content": text_value, "embed_text": self._build_embed_text(meta, text_value), "meta": meta})
            if block_type in {"image", "figure"} and self.enable_visual_chunks:
                captions = self._coerce_text_list(block.get("image_caption") or block.get("caption"))
                footnotes = self._coerce_text_list(block.get("image_footnote") or block.get("footnote"))
                context = self._content_list_context(content_list, idx)
                visual_caption = self._visual_caption_text(block_type, file_obj.name, page, captions, footnotes, context)
                text_value = self._clean_text(visual_caption) or f"[视觉证据] {file_obj.name} 第{page or '?'}页图片/图表"
                visual_index += 1
                meta = {"type": "visual_chunk", "modality": "image", "source": file_obj.name, "source_path": str(file_obj), "file_stem": file_obj.stem, "file_ext": "pdf", "format": "visual", "parser": "mineru", "page": page, "chunk_index": visual_index, "visual_index": visual_index, "image_caption": captions, "image_footnote": footnotes, "visual_caption": visual_caption, "visual_context": context, "image_path": image_path_str, "bbox": block.get("bbox"), "text": text_value, "is_visual": True}
                data_url = self._image_data_url_from_path(image_path) if image_path else None
                if data_url:
                    seen_visual_paths.add(image_path_str)
                    items.append({"type": "image", "image": data_url, "meta": meta})
                else:
                    items.append({"type": "text", "content": text_value, "embed_text": self._build_embed_text(meta, text_value), "meta": meta})
            if block_type == "table" and self.enable_visual_chunks and img_rel and image_path_str not in seen_visual_paths:
                data_url = self._image_data_url_from_path(image_path) if image_path else None
                if data_url:
                    visual_index += 1
                    captions = self._coerce_text_list(block.get("table_caption"))
                    body_text = self._strip_html(str(block.get("table_body") or ""))
                    table_summary = self._extract_table_summary(body_text, captions, [])
                    visual_caption = self._clean_text("\n".join(["[表格截图证据]", table_summary.get("table_summary", ""), *captions, body_text[:600]]))
                    text_value = visual_caption or f"[表格截图证据] {file_obj.name} 第{page or '?'}页表格"
                    meta = {"type": "visual_chunk", "modality": "table_image", "source": file_obj.name, "source_path": str(file_obj), "file_stem": file_obj.stem, "file_ext": "pdf", "format": "visual", "parser": "mineru", "page": page, "chunk_index": visual_index, "visual_index": visual_index, "image_path": image_path_str, "bbox": block.get("bbox"), "text": text_value, "visual_caption": visual_caption, "is_visual": True, **table_summary}
                    seen_visual_paths.add(image_path_str)
                    items.append({"type": "image", "image": data_url, "meta": meta})
        return items

    def _parse_pdf_fast(self, path: str) -> List[Dict]:
        """PyMuPDF 快速解析 PDF，作为 MinerU 失败后的兜底。"""
        if fitz is None:
            raise RuntimeError("未安装 PyMuPDF（fitz），请先安装 `pip install pymupdf`")

        items = []
        file_obj = Path(path)

        with fitz.open(path) as doc:
            for page_idx, page in enumerate(doc):
                # 提取文本
                text = page.get_text("text").strip()
                if len(text) > 10:
                    items.extend(self._split_text(
                        text,
                        path,
                        {"page": page_idx + 1, "format": "pdf"}
                    ))

                page_no = page_idx + 1
                ocr_text = ""

                # 扫描页优先做OCR，提升vLLM路径下的文本召回
                if len(text) < OCR_TRIGGER_TEXT_LEN:
                    ocr_text = self._ocr_text_from_page(page)
                    if len(ocr_text) >= OCR_MIN_TEXT_LEN:
                        items.extend(self._split_text(
                            ocr_text,
                            path,
                            {"page": page_no, "format": "pdf_ocr", "ocr": True}
                        ))

                # OCR不可用或结果过短时，保留图片embedding项给DashScope兜底
                if len(text) < OCR_TRIGGER_TEXT_LEN and len(ocr_text) < OCR_MIN_TEXT_LEN:
                    try:
                        pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2))
                        img_b64 = base64.b64encode(pix.tobytes("jpg")).decode()
                        items.append({
                            "type": "image",
                            "image": f"data:image/jpeg;base64,{img_b64}",
                            "meta": {
                                "type": "image_page",
                                "source": file_obj.name,
                                "source_path": str(file_obj),
                                "file_stem": file_obj.stem,
                                "file_ext": "pdf",
                                "page": page_no,
                                "is_visual": True,
                                "ocr_used": bool(ocr_text),
                                "text": f"[image_page_{page_no}]"
                            }
                        })
                    except Exception:
                        pass

        return items
    
    def _parse_pdf_mineru(self, path: str) -> List[Dict]:
        mineru = self._load_or_run_mineru(path)
        markdown = mineru.get("markdown") or ""
        content_list = mineru.get("content_list") or []
        cache_dir = mineru.get("cache_dir")
        file_obj = Path(path)
        items = self._split_text(markdown, str(cache_dir / "result.md"), {
            "source": file_obj.name,
            "source_path": str(file_obj),
            "file_stem": file_obj.stem,
            "file_ext": "pdf",
            "format": "mineru_md",
            "parser": "mineru",
            "mineru_cache_dir": str(cache_dir),
            "mineru_from_cache": bool(mineru.get("from_cache")),
            "doc_type": self._infer_doc_type(path, "pdf"),
        })
        items.extend(self._mineru_table_visual_items(path, content_list, cache_dir))
        return items

    def _parse_pdf(self, path: str) -> List[Dict]:
        if self.pdf_parse_mode == PDF_PARSE_MODE_FAST:
            return self._parse_pdf_fast(path)
        should_use_mineru = self.pdf_parse_mode in {PDF_PARSE_MODE_MINERU, PDF_PARSE_MODE_MINERU_FALLBACK} or (
            self.pdf_parse_mode == PDF_PARSE_MODE_AUTO and self.collection_name == "ic_collaboration_shared"
        )
        if not should_use_mineru:
            return self._parse_pdf_fast(path)
        try:
            return self._parse_pdf_mineru(path)
        except Exception:
            if self.pdf_parse_mode in {PDF_PARSE_MODE_MINERU, PDF_PARSE_MODE_AUTO}:
                raise
            logging.exception("MinerU 解析失败，回退 PyMuPDF: %s", path)
            return self._parse_pdf_fast(path)

    def _parse_docx(self, path: str) -> List[Dict]:
        """解析Word文档"""
        if Document is None:
            raise RuntimeError("未安装 python-docx，请先安装 `pip install python-docx`")

        doc = Document(path)
        full_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        return self._split_text(full_text, path, {"format": "docx"})
    
    def _parse_plain_text(self, path: str) -> List[Dict]:
        """解析纯文本"""
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return self._split_text(content, path, {"format": Path(path).suffix.lower().lstrip(".")})

    async def _embed_image_items_vllm(self, image_items: List[Dict]) -> List[Optional[List[float]]]:
        """图片 embedding 先探测能力，再在受控并发下处理图片页。"""
        if not image_items:
            return []

        results: List[Optional[List[float]]] = [None] * len(image_items)

        if self.vllm_visual_supported is not False:
            results[0] = await self._fetch_embedding_vllm_visual(image_items[0]["image"])

        if self.vllm_visual_supported:
            semaphore = asyncio.Semaphore(VISUAL_EMBED_CONCURRENCY)

            async def fetch_one(index: int, image_data_url: str):
                async with semaphore:
                    results[index] = await self._fetch_embedding_vllm_visual(image_data_url)

            await asyncio.gather(*[
                fetch_one(i, item["image"])
                for i, item in enumerate(image_items[1:], start=1)
            ])

        fallback_indices = [i for i, vec in enumerate(results) if vec is None]
        if fallback_indices:
            proxy_texts = []
            for idx in fallback_indices:
                meta = image_items[idx].get("meta", {})
                stem = str(meta.get("file_stem", "document"))
                page = str(meta.get("page", ""))
                proxy_texts.append(str(meta.get("text") or f"{stem} page {page} visual content"))
            proxy_vecs = await self._fetch_embedding_vllm(proxy_texts)
            for j, idx in enumerate(fallback_indices):
                results[idx] = proxy_vecs[j]
                if results[idx]:
                    image_items[idx].setdefault("meta", {})["visual_fallback"] = "vllm_text_proxy"

        for i, vec in enumerate(results):
            if vec:
                image_items[i].setdefault("meta", {})["visual_embedding"] = (
                    "vllm_multimodal" if image_items[i]["meta"].get("visual_fallback") is None
                    else image_items[i]["meta"]["visual_fallback"]
                )
        return results

    async def _embed_image_items_proxy_text(
        self,
        image_items: List[Dict],
        semaphore: asyncio.Semaphore,
        embed_backend: str,
        api_key: str,
    ) -> List[Optional[List[float]]]:
        """不支持视觉 embedding 的云端后端，统一退化为页级代理文本。"""
        if not image_items:
            return []

        proxy_texts = []
        for item in image_items:
            meta = item.get("meta", {})
            stem = str(meta.get("file_stem", "document"))
            page = str(meta.get("page", ""))
            proxy_texts.append(str(meta.get("text") or f"{stem} page {page} visual content"))

        if embed_backend == "DashScope (阿里云)":
            proxy_items = [{"type": "text", "content": text} for text in proxy_texts]
            results = await self._fetch_embedding_dashscope(proxy_items, semaphore, api_key)
        else:
            results = await self._fetch_embedding_minimax(proxy_texts, semaphore, api_key, MINIMAX_EMBED_MODEL)

        for item, vec in zip(image_items, results):
            if vec:
                meta = item.setdefault("meta", {})
                meta["visual_embedding"] = "text_proxy"
        return results
    
    async def process_file(
        self, semaphore: asyncio.Semaphore, file_path: str,
        batch_tag: str, embed_backend: str, api_key: str
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

        prepared_items = []
        skipped_invalid = 0
        for item in items:
            meta = self._finalize_metadata(item.get("meta", {}), batch_tag, embed_backend)
            ok, reason = self._validate_metadata(meta)
            if not ok:
                logging.warning("跳过无效 metadata: %s (%s)", reason, file_path)
                skipped_invalid += 1
                continue
            item["meta"] = meta
            if item.get("type") == "text":
                item["content"] = meta["text"]
                item["embed_text"] = self._build_embed_text(meta, meta["text"])
            prepared_items.append(item)

        items = prepared_items
        if not items:
            return 0
        
        # 获取embedding
        if embed_backend == "vllm (本地)":
            text_items = [it for it in items if it["type"] == "text"]
            image_items = [it for it in items if it["type"] == "image"]

            paired_items: List[Tuple[Dict, Optional[List[float]]]] = []

            if text_items:
                texts = [it.get("embed_text", it.get("content", "")) for it in text_items]
                text_results = await self._fetch_embedding_vllm(texts)
                paired_items.extend(list(zip(text_items, text_results)))

            if image_items:
                image_results = await self._embed_image_items_vllm(image_items)
                paired_items.extend(list(zip(image_items, image_results)))

            if not paired_items:
                return 0
        elif embed_backend == "DashScope (阿里云)":
            if not api_key:
                raise ValueError("未提供 DashScope API Key")
            text_items = [it for it in items if it["type"] == "text"]
            image_items = [it for it in items if it["type"] == "image"]
            paired_items = []
            if text_items:
                results = await self._fetch_embedding_dashscope(text_items, semaphore, api_key)
                paired_items.extend(list(zip(text_items, results)))
            if image_items:
                image_results = await self._embed_image_items_proxy_text(
                    image_items, semaphore, embed_backend, api_key
                )
                paired_items.extend(list(zip(image_items, image_results)))
        elif embed_backend == "MiniMax (云端)":
            if not api_key:
                raise ValueError("未提供 MiniMax API Key")
            if not MINIMAX_EMBED_MODEL:
                raise ValueError("未配置 MiniMax embedding 模型名（MINIMAX_EMBED_MODEL）")
            text_items = [it for it in items if it["type"] == "text"]
            image_items = [it for it in items if it["type"] == "image"]
            paired_items = []
            if text_items:
                texts = [it.get("embed_text", it.get("content", "")) for it in text_items]
                results = await self._fetch_embedding_minimax(texts, semaphore, api_key, MINIMAX_EMBED_MODEL)
                paired_items.extend(list(zip(text_items, results)))
            if image_items:
                image_results = await self._embed_image_items_proxy_text(
                    image_items, semaphore, embed_backend, api_key
                )
                paired_items.extend(list(zip(image_items, image_results)))
        else:
            raise ValueError(f"不支持的 embedding 后端: {embed_backend}")
        
        # 准备数据
        vectors, metas = [], []
        for item, vec in paired_items:
            if vec:
                meta = dict(item["meta"])
                ok, reason = self._validate_metadata(meta)
                if not ok:
                    logging.warning("跳过无效 metadata: %s (%s)", reason, file_path)
                    skipped_invalid += 1
                    continue
                vectors.append(vec)
                metas.append(meta)
        
        if vectors:
            self.collection.insert([vectors, [batch_tag] * len(vectors), metas])
            graph_inserted = self._insert_graph_passages(vectors, metas)
            self._write_quality_report(file_path, batch_tag, metas, skipped_invalid)
            if graph_inserted:
                logging.info("Graph sidecar passages inserted: %s", graph_inserted)
            return len(vectors)
        return 0
    
    @staticmethod
    def _file_key(file_path: str) -> str:
        """断点续传主键：绝对路径，避免同名文件误判"""
        return str(Path(file_path).resolve())

    @staticmethod
    def _compute_file_hash(file_path: str) -> str:
        """文件内容哈希，用于检测文件内容更新"""
        hasher = hashlib.sha1()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _build_progress_entry(
        file_hash: str,
        pipeline_sig: str,
        file_size: int,
        file_mtime_ns: int,
    ) -> Dict[str, str]:
        return {
            "hash": file_hash,
            "sig": pipeline_sig,
            "size": int(file_size),
            "mtime_ns": int(file_mtime_ns),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    async def run(
        self, data_path: str, batch_tag: str, embed_backend: str,
        api_key: str = "",
        progress_callback=None,
        file_paths: Optional[List[str]] = None,
    ) -> str:
        """主运行函数 (参考 async_ingestor.py)"""
        # 扫描文件
        supported_exts = {".pdf", ".docx", ".md", ".txt"}
        if file_paths:
            files = sorted({
                str(Path(f))
                for f in file_paths
                if f and Path(f).is_file() and Path(f).suffix.lower() in supported_exts
            })
        else:
            files = sorted(
                str(f) for f in Path(data_path).glob("**/*")
                if f.is_file() and f.suffix.lower() in supported_exts
            )
        processed_state, legacy_names = self._load_progress()
        pipeline_sig = self._pipeline_signature(embed_backend, batch_tag=batch_tag)
        pending = []
        progress_upgraded = False
        for f in files:
            file_key = self._file_key(f)
            fname = os.path.basename(f)
            stat = os.stat(f)

            state_entry = processed_state.get(file_key, {})
            if isinstance(state_entry, dict):
                state_hash = str(state_entry.get("hash", ""))
                state_sig = str(state_entry.get("sig", ""))
                state_size = int(state_entry.get("size", -1))
                state_mtime_ns = int(state_entry.get("mtime_ns", -1))
            else:
                state_hash = str(state_entry)
                state_sig = ""
                state_size = -1
                state_mtime_ns = -1

            # 快速路径：mtime/size/参数未变化时，跳过完整 hash 计算。
            if (
                state_sig == pipeline_sig
                and state_size == int(stat.st_size)
                and state_mtime_ns == int(stat.st_mtime_ns)
            ):
                continue

            # 兼容旧版：basename记录（旧格式）
            if file_key not in processed_state and fname in legacy_names:
                continue

            file_hash = self._compute_file_hash(f)
            if state_hash == file_hash and state_sig == pipeline_sig:
                processed_state[file_key] = self._build_progress_entry(
                    file_hash, pipeline_sig, stat.st_size, stat.st_mtime_ns
                )
                progress_upgraded = True
                continue

            pending.append((f, file_key, file_hash, int(stat.st_size), int(stat.st_mtime_ns)))
        
        if not pending:
            if progress_upgraded:
                self._save_progress(processed_state, legacy_names)
            return "⚠️ 当前目录无新文档需处理"

        self._emit_progress(progress_callback, "🔎 已完成扫描，开始入库...", {
            "phase": "scan",
            "total_files": len(files),
            "pending_files": len(pending),
            "processed_files": 0,
            "inserted_total": 0,
            "paused": False,
        })

        if embed_backend == "DashScope (阿里云)":
            effective_key = (api_key or DASHSCOPE_API_KEY).strip()
        elif embed_backend == "MiniMax (云端)":
            effective_key = (api_key or MINIMAX_API_KEY).strip()
        else:
            effective_key = ""

        if embed_backend == "DashScope (阿里云)" and not effective_key:
            return "❌ 请选择 DashScope 后提供 API Key（或设置 DASHSCOPE_API_KEY 环境变量）"
        if embed_backend == "MiniMax (云端)" and not effective_key:
            return "❌ 请选择 MiniMax 后提供 API Key（或设置 MINIMAX_API_KEY 环境变量）"
        
        total_inserted = 0
        semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
        pause_notice_sent = False
        dirty_progress = 0

        for idx, (f_path, file_key, file_hash, file_size, file_mtime_ns) in enumerate(pending):
            fname = os.path.basename(f_path)

            while INGEST_CONTROL.get("paused", False):
                if not pause_notice_sent:
                    self._emit_progress(progress_callback, "⏸️ 入库已暂停，等待继续...", {
                        "phase": "paused",
                        "total_files": len(pending),
                        "processed_files": idx,
                        "inserted_total": total_inserted,
                        "current_file": fname,
                        "paused": True,
                    })
                    pause_notice_sent = True
                await asyncio.sleep(0.4)
            if pause_notice_sent:
                self._emit_progress(progress_callback, "▶️ 已恢复入库，继续处理...", {
                    "phase": "running",
                    "total_files": len(pending),
                    "processed_files": idx,
                    "inserted_total": total_inserted,
                    "current_file": fname,
                    "paused": False,
                })
                pause_notice_sent = False

            try:
                inserted = await self.process_file(
                    semaphore, f_path, batch_tag, embed_backend, effective_key
                )
                # 无论是否插入条目，只要处理成功就记录状态，避免反复重试空文件
                processed_state[file_key] = self._build_progress_entry(
                    file_hash, pipeline_sig, file_size, file_mtime_ns
                )
                if fname in legacy_names:
                    legacy_names.discard(fname)
                dirty_progress += 1
                if dirty_progress >= PROGRESS_SAVE_EVERY or idx == len(pending) - 1:
                    self._save_progress(processed_state, legacy_names)
                    dirty_progress = 0

                if inserted > 0:
                    total_inserted += inserted
                    msg = f"✅ [{idx+1}/{len(pending)}] {fname[:30]}... ({inserted}条)"
                    state = "inserted"
                else:
                    msg = f"⚠️ [{idx+1}/{len(pending)}] {fname[:30]}... (跳过)"
                    state = "skipped"
            except Exception as e:
                msg = f"❌ [{idx+1}/{len(pending)}] {fname[:30]}... ({str(e)[:80]})"
                inserted = 0
                state = "error"

            self._emit_progress(progress_callback, msg, {
                "phase": state,
                "index": idx + 1,
                "total_files": len(pending),
                "processed_files": idx + 1,
                "inserted_current": inserted,
                "inserted_total": total_inserted,
                "current_file": fname,
                "paused": False,
            })

        self.collection.flush()
        if dirty_progress:
            self._save_progress(processed_state, legacy_names)
        model_info = self.vllm_model if embed_backend == "vllm (本地)" else MODEL_NAME
        if embed_backend == "MiniMax (云端)":
            model_info = MINIMAX_EMBED_MODEL
        elif embed_backend == "DashScope (阿里云)":
            model_info = MODEL_NAME
        return (
            f"🎉 完成！共处理 {len(pending)} 个文件，插入 {total_inserted} 条记录\n"
            f"Collection: {self.collection_name}\n"
            f"Graph sidecar: {self.graph_sidecar_prefix or '未启用'}\n"
            f"Metadata schema: {METADATA_SCHEMA_VERSION}\n"
            f"Pipeline version: {EMBED_PIPELINE_VERSION}\n"
            f"Embedding模型: {model_info}\n"
            f"总实体数: {self.collection.num_entities}"
        )
    
    def _load_progress(self) -> Tuple[Dict[str, Dict[str, str]], Set[str]]:
        """加载断点进度（path->{hash,sig,updated_at}, legacy_basename）"""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    files = data.get("files", {})
                    legacy = data.get("legacy_names", [])
                    file_map: Dict[str, Dict[str, str]] = {}
                    if isinstance(files, dict):
                        for k, v in files.items():
                            if isinstance(v, dict):
                                file_map[str(k)] = {
                                    "hash": str(v.get("hash", "")),
                                    "sig": str(v.get("sig", "")),
                                    "size": int(v.get("size", -1)),
                                    "mtime_ns": int(v.get("mtime_ns", -1)),
                                    "updated_at": str(v.get("updated_at", "")),
                                }
                            else:
                                file_map[str(k)] = {
                                    "hash": str(v),
                                    "sig": "",
                                    "size": -1,
                                    "mtime_ns": -1,
                                    "updated_at": "",
                                }
                    legacy_names = set(str(x) for x in legacy) if isinstance(legacy, list) else set()
                    return file_map, legacy_names

                # 兼容旧格式：list[str]
                legacy_entries = set(str(x) for x in data) if isinstance(data, list) else set()
                file_map: Dict[str, Dict[str, str]] = {}
                legacy_names: Set[str] = set()
                for entry in legacy_entries:
                    if os.path.sep in entry or entry.startswith("."):
                        file_map[str(Path(entry).resolve())] = {
                            "hash": "",
                            "sig": "",
                            "size": -1,
                            "mtime_ns": -1,
                            "updated_at": "",
                        }
                    else:
                        legacy_names.add(entry)
                return file_map, legacy_names
            except Exception:
                return {}, set()
        return {}, set()
    
    def _save_progress(self, processed_state: Dict[str, Dict[str, str]], legacy_names: Set[str]):
        """保存断点进度（v2结构）"""
        payload = {
            "version": 4,
            "files": processed_state,
            "legacy_names": sorted(legacy_names),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp_path = f"{self.progress_file}.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.progress_file)


# ==================== Gradio UI ====================

def _normalize_db_name(db_name: Optional[str]) -> str:
    db = (db_name or MILVUS_DEFAULT_DB).strip()
    return db or MILVUS_DEFAULT_DB


def _ensure_milvus_connection(db_name: Optional[str] = None):
    if connections is None:
        raise RuntimeError("未安装 pymilvus，请先安装 `pip install pymilvus`")
    db = _normalize_db_name(db_name)
    if connections.has_connection("default"):
        try:
            connections.disconnect("default")
        except Exception:
            pass
    connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT, db_name=db)


def _list_databases() -> List[str]:
    try:
        client = _milvus_client()
        if hasattr(client, "list_databases"):
            dbs = sorted(str(db) for db in client.list_databases())
            if dbs:
                return dbs
    except Exception:
        pass
    return [MILVUS_DEFAULT_DB]


def _canonical_collection_name(name: str) -> str:
    return (name or "").strip()


def _normalize_collection_name(name: str) -> str:
    raw = _canonical_collection_name(name)
    return COLLECTION_ALIASES.get(raw, raw)


def _resolve_existing_collection_name(name: str) -> str:
    return _normalize_collection_name(name)


def _choose_valid_value(preferred: Optional[str], choices: List[str]) -> Optional[str]:
    if not choices:
        return None
    if preferred in choices:
        return preferred
    return choices[0]


def _normalize_graph_prefix(prefix: str) -> str:
    prefix = (prefix or GRAPH_SIDECAR_DEFAULT_PREFIX or "siq_project").strip()
    prefix = re.sub(r"[^A-Za-z0-9_]+", "_", prefix).strip("_")
    return prefix or "siq_project"


def _graph_collection_names(prefix: str) -> Dict[str, str]:
    normalized = _normalize_graph_prefix(prefix)
    return {
        "entities": f"{normalized}_{GRAPH_ENTITY_COLLECTION}",
        "relations": f"{normalized}_{GRAPH_RELATION_COLLECTION}",
        "passages": f"{normalized}_{GRAPH_PASSAGE_COLLECTION}",
    }


def _is_graph_sidecar_collection(name: str) -> bool:
    return name.endswith((
        f"_{GRAPH_ENTITY_COLLECTION}",
        f"_{GRAPH_RELATION_COLLECTION}",
        f"_{GRAPH_PASSAGE_COLLECTION}",
    ))


def _milvus_client(db_name: Optional[str] = None) -> "MilvusClient":
    if MilvusClient is None:
        raise RuntimeError("未安装 pymilvus，请先安装 `pip install pymilvus`")
    return MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}", db_name=_normalize_db_name(db_name))


def _ensure_graph_sidecar_collections(
    prefix: str = GRAPH_SIDECAR_DEFAULT_PREFIX,
    dimension: int = VECTOR_DIM,
    drop_existing: bool = False,
    db_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create Vector Graph RAG compatible sidecar collections.

    The schema intentionally follows vector-graph-rag's MilvusStore:
    id(VARCHAR primary), vector(FLOAT_VECTOR), text(VARCHAR), dynamic fields enabled.
    Dynamic fields carry project_tag/source/page/entity_ids/relation_ids.
    """
    db = _normalize_db_name(db_name)
    client = _milvus_client(db)
    names = _graph_collection_names(prefix)
    created = []
    reused = []
    dropped = []

    for name in names.values():
        if client.has_collection(name):
            if drop_existing:
                client.drop_collection(name)
                dropped.append(name)
            else:
                reused.append(name)
                continue

        schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, max_length=64, is_primary=True)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type=INDEX_TYPE,
            metric_type="IP",
            params=GRAPH_INDEX_PARAMS,
        )

        client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
            consistency_level="Bounded",
        )
        created.append(name)

    return {
        "prefix": _normalize_graph_prefix(prefix),
        "database": db,
        "dimension": dimension,
        "collections": names,
        "created": created,
        "reused": reused,
        "dropped": dropped,
    }


def _create_graph_sidecar(db_name: str, prefix: str, reset: bool = False) -> str:
    try:
        result = _ensure_graph_sidecar_collections(prefix, dimension=VECTOR_DIM, drop_existing=bool(reset), db_name=db_name)
        lines = [
            "✅ Vector Graph RAG sidecar collections ready",
            f"Database: {result['database']}",
            f"Prefix: {result['prefix']}",
            f"Dimension: {result['dimension']}",
            f"Entities: {result['collections']['entities']}",
            f"Relations: {result['collections']['relations']}",
            f"Passages: {result['collections']['passages']}",
        ]
        if result["created"]:
            lines.append(f"Created: {', '.join(result['created'])}")
        if result["reused"]:
            lines.append(f"Reused: {', '.join(result['reused'])}")
        if result["dropped"]:
            lines.append(f"Dropped first: {', '.join(result['dropped'])}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Graph sidecar 初始化失败: {e}"


def _current_collection_choices(db_name: Optional[str] = None) -> List[str]:
    try:
        _ensure_milvus_connection(db_name)
        existing = sorted(utility.list_collections())
        return sorted({
            _canonical_collection_name(n)
            for n in existing
            if n and not _is_graph_sidecar_collection(n)
        })
    except Exception:
        pass
    # Milvus 不可用时，回退到默认角色清单；空 database 保持空列表
    return list(ROLE_REGISTRY.keys())


def _collection_status_snapshot(db_name: Optional[str] = None) -> Dict:
    db = _normalize_db_name(db_name)
    try:
        _ensure_milvus_connection(db)
        collections = sorted(utility.list_collections())
        rows = []
        for physical_name in collections:
            name = _canonical_collection_name(physical_name)
            try:
                c = Collection(physical_name)
                rows.append({
                    "name": name,
                    "physical_name": physical_name,
                    "entities": int(c.num_entities),
                    "desc": ROLE_REGISTRY.get(name, {}).get("desc", ""),
                    "managed_role": name in ROLE_REGISTRY,
                    "graph_sidecar": _is_graph_sidecar_collection(physical_name),
                })
            except Exception as e:
                rows.append({
                    "name": name,
                    "physical_name": physical_name,
                    "entities": None,
                    "desc": ROLE_REGISTRY.get(name, {}).get("desc", ""),
                    "managed_role": name in ROLE_REGISTRY,
                    "graph_sidecar": _is_graph_sidecar_collection(physical_name),
                    "error": str(e),
                })
        return {
            "database": db,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "total_collections": len(collections),
            "collections": rows,
        }
    except Exception as e:
        return {
            "database": db,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "error": str(e),
            "total_collections": 0,
            "collections": [],
        }


def _create_collection(db_name: str, name: str) -> str:
    coll = _normalize_collection_name(name)
    if not coll:
        return "❌ Collection 名称不能为空"
    if not re.fullmatch(r"[A-Za-z0-9_]+", coll):
        return "❌ Collection 名称仅允许字母/数字/下划线"

    try:
        db = _normalize_db_name(db_name)
        _ensure_milvus_connection(db)
        if utility.has_collection(coll):
            return f"⚠️ Collection 已存在: {db}.{coll}"

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
            FieldSchema(name="project_tag", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="metadata", dtype=DataType.JSON),
        ]
        schema = CollectionSchema(fields, description=ROLE_REGISTRY.get(coll, {}).get("desc", coll))
        collection = Collection(coll, schema)
        collection.create_index(field_name="vector", index_params=INDEX_PARAMS)
        collection.create_index(field_name="project_tag", index_params={"index_type": "INVERTED"})
        collection.load()
        return f"✅ Collection 创建成功: {db}.{coll}"
    except Exception as e:
        return f"❌ 创建失败: {e}"


def _drop_collection(db_name: str, name: str) -> str:
    coll = _normalize_collection_name(name)
    if not coll:
        return "❌ 请选择 Collection"
    try:
        db = _normalize_db_name(db_name)
        _ensure_milvus_connection(db)
        target = _resolve_existing_collection_name(coll)
        if not utility.has_collection(target):
            return f"⚠️ Collection 不存在: {coll}"
        utility.drop_collection(target)
        return f"✅ Collection 已删除: {db}.{coll}"
    except Exception as e:
        return f"❌ 删除失败: {e}"


def _rebuild_index(db_name: str, name: str) -> str:
    coll = _normalize_collection_name(name)
    if not coll:
        return "❌ 请选择 Collection"
    try:
        db = _normalize_db_name(db_name)
        _ensure_milvus_connection(db)
        target = _resolve_existing_collection_name(coll)
        if not utility.has_collection(target):
            return f"⚠️ Collection 不存在: {coll}"

        collection = Collection(target)
        try:
            collection.release()
        except Exception:
            pass

        # 先尝试删除旧索引，再重建标准索引
        try:
            collection.drop_index()
        except Exception:
            pass

        collection.create_index(field_name="vector", index_params=INDEX_PARAMS)
        collection.create_index(field_name="project_tag", index_params={"index_type": "INVERTED"})
        collection.load()
        if target != coll:
            return f"✅ 索引重建完成: {db}.{target}（对应显示名: {coll}）"
        return f"✅ 索引重建完成: {db}.{coll}"
    except Exception as e:
        return f"❌ 索引重建失败: {e}"


def _get_tag_stats(db_name: str, name: str) -> str:
    coll = _normalize_collection_name(name)
    if not coll:
        return "❌ 请选择 Collection"
    try:
        db = _normalize_db_name(db_name)
        _ensure_milvus_connection(db)
        target = _resolve_existing_collection_name(coll)
        if not utility.has_collection(target):
            return f"⚠️ Collection 不存在: {coll}"

        collection = Collection(target)
        total = int(collection.num_entities)
        if total == 0:
            if target != coll:
                return f"ℹ️ Collection `{target}`（显示名 `{coll}`）为空"
            return f"ℹ️ Collection `{coll}` 为空"

        query_limit = min(total, 20000)
        rows = collection.query(
            expr='project_tag != ""',
            output_fields=["project_tag"],
            limit=query_limit
        )
        counter = Counter()
        for row in rows:
            tag = str(row.get("project_tag", "")).strip()
            if tag:
                counter[tag] += 1

        lines = [
            f"Database: {db}",
            f"Collection: {target}" if target == coll else f"Collection: {target} (显示名: {coll})",
            f"总实体数: {total}",
            f"统计样本: {query_limit}",
        ]
        if not counter:
            lines.append("未发现 project_tag 数据")
            return "\n".join(lines)

        lines.append(f"不同 tag 数: {len(counter)}")
        lines.append("-" * 40)
        for tag, count in counter.most_common(20):
            lines.append(f"{tag}: {count}")
        if total > query_limit:
            lines.append("-" * 40)
            lines.append("注: 为控制开销，仅统计前 20000 条样本")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 统计失败: {e}"


def _refresh_ui_state(
    current_db: Optional[str],
    current_ingest: Optional[str],
    current_drop: Optional[str],
    current_rebuild: Optional[str],
    current_tag: Optional[str],
):
    db_choices = _list_databases()
    db_value = _choose_valid_value(_normalize_db_name(current_db), db_choices)
    choices = _current_collection_choices(db_value)
    snapshot = _collection_status_snapshot(db_value)
    ingest_value = _choose_valid_value(current_ingest, choices)
    drop_value = _choose_valid_value(current_drop, choices)
    rebuild_value = _choose_valid_value(current_rebuild, choices)
    tag_value = _choose_valid_value(current_tag, choices)
    total_actual = int(snapshot.get("total_collections", 0)) if isinstance(snapshot, dict) else 0
    if total_actual > 0:
        status_msg = f"✅ 已刷新 Database `{db_value}`，当前 {len(choices)} 个 Collection（{datetime.now().strftime('%H:%M:%S')}）"
    else:
        status_msg = f"ℹ️ Database `{db_value}` 当前无 Collection（{datetime.now().strftime('%H:%M:%S')}）"
    return (
        snapshot,
        status_msg,
        gr.update(choices=db_choices, value=db_value),
        gr.update(choices=db_choices, value=db_value),
        gr.update(choices=choices, value=ingest_value),
        gr.update(choices=choices, value=drop_value),
        gr.update(choices=choices, value=rebuild_value),
        gr.update(choices=choices, value=tag_value),
    )


def _format_runtime_stats(meta: Dict, config: Dict) -> str:
    if not config:
        return "暂无运行任务"

    phase = str(meta.get("phase", "running"))
    processed = int(meta.get("processed_files", 0))
    total = int(meta.get("total_files", 0))
    inserted_total = int(meta.get("inserted_total", 0))
    current_file = str(meta.get("current_file", "-"))
    paused = bool(meta.get("paused", False))
    paused_text = "是" if paused else "否"
    chunk_size = int(config.get("chunk_size", DEFAULT_CHUNK_SIZE))
    chunk_overlap = int(config.get("chunk_overlap", DEFAULT_CHUNK_OVERLAP))
    return "\n".join([
        f"Database: {config.get('database', '-')}",
        f"Collection: {config.get('collection', '-')}",
        f"后端: {config.get('backend', '-')}",
        f"阶段: {phase}",
        f"文件进度: {processed}/{total}",
        f"当前文件: {current_file}",
        f"累计入库: {inserted_total}",
        f"切片/重叠: {chunk_size}/{chunk_overlap}",
        f"质量报告: {QUALITY_REPORT_DIR}",
        f"暂停状态: {paused_text}",
        f"更新时间: {datetime.now().strftime('%H:%M:%S')}",
    ])


def _runtime_public_snapshot() -> Tuple[str, str]:
    with INGEST_RUNTIME_LOCK:
        logs = list(INGEST_RUNTIME.get("logs", []))
        meta = dict(INGEST_RUNTIME.get("meta", {}))
        config = dict(INGEST_RUNTIME.get("config", {}))
        active = bool(INGEST_RUNTIME.get("active", False))
        result = str(INGEST_RUNTIME.get("result", ""))

    log_text = "\n".join(logs[-300:])
    stats_text = _format_runtime_stats(meta, config)
    if result and not active and result not in stats_text:
        stats_text = f"{stats_text}\n{result}"
    return log_text, stats_text


def _save_runtime_state_unlocked():
    payload = {
        "active": bool(INGEST_RUNTIME.get("active", False)),
        "started_at": str(INGEST_RUNTIME.get("started_at", "")),
        "updated_at": str(INGEST_RUNTIME.get("updated_at", "")),
        "finished_at": str(INGEST_RUNTIME.get("finished_at", "")),
        "logs": list(INGEST_RUNTIME.get("logs", []))[-MAX_RUNTIME_LOG_LINES:],
        "meta": dict(INGEST_RUNTIME.get("meta", {})),
        "config": dict(INGEST_RUNTIME.get("config", {})),
        "result": str(INGEST_RUNTIME.get("result", "")),
    }
    tmp_path = f"{RUNTIME_STATE_FILE}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, RUNTIME_STATE_FILE)


def _load_runtime_state():
    if not RUNTIME_STATE_FILE.exists():
        return
    try:
        with open(RUNTIME_STATE_FILE, "r") as f:
            payload = json.load(f)
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    with INGEST_RUNTIME_LOCK:
        INGEST_RUNTIME.update({
            "active": bool(payload.get("active", False)),
            "started_at": str(payload.get("started_at", "")),
            "updated_at": str(payload.get("updated_at", "")),
            "finished_at": str(payload.get("finished_at", "")),
            "logs": list(payload.get("logs", []))[-MAX_RUNTIME_LOG_LINES:],
            "meta": dict(payload.get("meta", {})),
            "config": dict(payload.get("config", {})),
            "result": str(payload.get("result", "")),
            "task": None,
        })
        if INGEST_RUNTIME["active"]:
            INGEST_RUNTIME["active"] = False
            INGEST_RUNTIME["meta"]["phase"] = "interrupted"
            INGEST_RUNTIME["logs"].append("⚠️ 服务曾重启，上一轮入库运行态已停止。请重新开始任务。")
            _save_runtime_state_unlocked()


def _append_runtime_log(msg: str, meta: Optional[Dict] = None):
    with INGEST_RUNTIME_LOCK:
        logs = INGEST_RUNTIME.setdefault("logs", [])
        logs.append(msg)
        del logs[:-MAX_RUNTIME_LOG_LINES]
        if meta:
            INGEST_RUNTIME.setdefault("meta", {}).update(meta)
        INGEST_RUNTIME["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_runtime_state_unlocked()


async def _run_ingest_background(
    db_name: str,
    coll: str,
    doc_dir: str,
    uploaded_file_paths: Optional[List[str]],
    tag: str,
    backend: str,
    key: str,
    reset: bool,
    chunk_size: int,
    chunk_overlap: int,
    pdf_parse_mode: str = PDF_PARSE_MODE_AUTO,
    enable_table_chunks: bool = True,
    enable_visual_chunks: bool = True,
    graph_sidecar_prefix: str = "",
    graph_drop_existing: bool = False,
):
    try:
        ingestor = AsyncKnowledgeIngestor(
            coll,
            reset,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            pdf_parse_mode=pdf_parse_mode,
            enable_table_chunks=enable_table_chunks,
            enable_visual_chunks=enable_visual_chunks,
            graph_sidecar_prefix=graph_sidecar_prefix,
            graph_drop_existing=graph_drop_existing,
            db_name=db_name,
        )
    except Exception as e:
        err = f"❌ 初始化失败: {e}"
        _append_runtime_log(err, {"phase": "init_error", "paused": False})
        with INGEST_RUNTIME_LOCK:
            INGEST_RUNTIME["active"] = False
            INGEST_RUNTIME["finished_at"] = datetime.now().isoformat(timespec="seconds")
            INGEST_RUNTIME["result"] = err
            _save_runtime_state_unlocked()
        return

    def callback(msg: str, meta: Optional[Dict] = None):
        _append_runtime_log(msg, meta or {})

    try:
        result = await ingestor.run(doc_dir, tag, backend, key, callback, file_paths=uploaded_file_paths)
    except Exception as e:
        result = f"❌ 入库任务异常: {e}"

    _append_runtime_log("=" * 50, {})
    _append_runtime_log(result, {"phase": "done", "paused": False})
    with INGEST_RUNTIME_LOCK:
        INGEST_RUNTIME["active"] = False
        INGEST_RUNTIME["finished_at"] = datetime.now().isoformat(timespec="seconds")
        INGEST_RUNTIME["result"] = result
        _save_runtime_state_unlocked()


_load_runtime_state()


def _upload_path(item: Any) -> str:
    """Return the server-side path from Gradio File values across versions."""
    if item is None:
        return ""
    if isinstance(item, (str, Path)):
        return str(item)
    if isinstance(item, dict):
        return str(item.get("path") or item.get("name") or "")
    for attr in ("path", "name"):
        value = getattr(item, attr, None)
        if value:
            return str(value)
    return ""


def _uploaded_file_paths(uploaded_docs: Any) -> List[str]:
    raw_items = uploaded_docs if isinstance(uploaded_docs, (list, tuple)) else ([uploaded_docs] if uploaded_docs else [])
    paths = []
    for item in raw_items:
        raw_path = _upload_path(item)
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.is_file():
            paths.append(str(path))
    return paths


def _resolve_document_root(uploaded_docs: Any, fallback_doc_dir: str = "") -> Tuple[str, str, int, List[str]]:
    """Prefer a directory uploaded through Gradio, with a manual path fallback."""
    paths = [Path(path) for path in _uploaded_file_paths(uploaded_docs)]

    if paths:
        roots = [path if path.is_dir() else path.parent for path in paths]
        try:
            root = Path(os.path.commonpath([str(root.resolve()) for root in roots]))
        except ValueError:
            root = roots[0]
        return str(root), "uploaded_directory", len(paths), [str(path) for path in paths]

    fallback = str(fallback_doc_dir or "").strip()
    if fallback:
        return fallback, "server_path", 0, []
    return "", "", 0, []


INGEST_UI_CSS = r"""
/* ===== Apple Design System ===== */
:root {
  --a-bg: #f5f5f7;
  --a-surface: #ffffff;
  --a-text: #1d1d1f;
  --a-text-secondary: #86868b;
  --a-text-tertiary: #a1a1a6;
  --a-border: #e2e8ed;
  --a-border-light: #f0f0f2;
  --a-primary: #2563eb;
  --a-primary-hover: #1d4ed8;
  --a-success: #22c55e;
  --a-warning: #f59e0b;
  --a-error: #ef4444;
  --a-radius: 14px;
  --a-radius-sm: 10px;
  --a-radius-pill: 980px;
  --a-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  --a-shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
  --a-transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

:host {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", Arial, sans-serif !important;
}

.gradio-container,
[class*="gradio-container"] {
  background: var(--a-bg) !important;
  color: var(--a-text) !important;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", Arial, sans-serif !important;
  -webkit-font-smoothing: antialiased !important;
  -moz-osx-font-smoothing: grayscale !important;
}

/* ===== Header ===== */
.a-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 28px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--a-border);
}
.a-brand {
  display: flex;
  align-items: center;
  gap: 14px;
}
.a-logo {
  width: 40px;
  height: 40px;
  border-radius: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, #2563eb 0%, #3b82f6 100%);
  color: #fff;
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.3px;
  box-shadow: 0 2px 8px rgba(37,99,235,0.2);
  flex-shrink: 0;
}
.a-title-wrap h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--a-text);
  line-height: 1.2;
}
.a-title-wrap p {
  margin: 3px 0 0;
  font-size: 13px;
  color: var(--a-text-secondary);
  font-weight: 400;
  letter-spacing: -0.2px;
}
.a-health {
  display: flex;
  gap: 10px;
  align-items: center;
}
.a-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: var(--a-radius-pill);
  background: var(--a-surface);
  border: 1px solid var(--a-border);
  font-size: 12px;
  font-weight: 500;
  color: var(--a-text-secondary);
  letter-spacing: -0.2px;
}
.a-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--a-success);
}
.a-dot.warn { background: var(--a-warning); }
.a-dot.err { background: var(--a-error); }

/* ===== Tabs ===== */
.a-tabs [class*="tab-container"] {
  gap: 16px !important;
}
.a-tabs [class*="tab-container"] button[class*="svelte-"] {
  font-weight: 600 !important;
  font-size: 15px !important;
  letter-spacing: -0.3px !important;
  padding: 10px 4px !important;
  margin-right: 24px !important;
  color: var(--a-text-secondary) !important;
  background: transparent !important;
  border: none !important;
  transition: var(--a-transition) !important;
}
.a-tabs [class*="tab-container"] button[class*="svelte-"]:last-child {
  margin-right: 0 !important;
}
.a-tabs [class*="tab-container"] button[class*="svelte-"]:hover {
  color: var(--a-text) !important;
}
.a-tabs .selected[class*="svelte-"] {
  color: var(--a-primary) !important;
}

/* ===== Layout ===== */
.a-workspace {
  align-items: flex-start !important;
  gap: 20px !important;
}
.a-stack {
  gap: 16px !important;
}

/* ===== Cards ===== */
.a-card {
  background: var(--a-surface) !important;
  border: 1px solid var(--a-border) !important;
  border-radius: var(--a-radius) !important;
  padding: 20px !important;
  box-shadow: var(--a-shadow) !important;
}
.a-card > div {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  padding: 0 !important;
  box-shadow: none !important;
}
.a-card h3,
.a-card > div h3 {
  margin: 0 0 14px !important;
  font-size: 15px !important;
  font-weight: 700 !important;
  color: var(--a-text) !important;
  letter-spacing: -0.3px !important;
  line-height: 1.3 !important;
}
.a-card p,
.a-card > div p {
  color: var(--a-text-secondary) !important;
  font-size: 12px !important;
  margin: 0 0 12px !important;
  line-height: 1.5 !important;
}

/* ===== Upload ===== */
.a-upload [data-testid="file"] {
  border: 1.5px dashed var(--a-border) !important;
  border-radius: var(--a-radius-sm) !important;
  background: #fafbfc !important;
  padding: 36px 24px !important;
  transition: var(--a-transition) !important;
}
.a-upload [data-testid="file"]:hover {
  border-color: var(--a-primary) !important;
  background: rgba(37,99,235,0.03) !important;
}
.a-upload [data-testid="file"] > label {
  color: var(--a-text-secondary) !important;
  font-size: 13px !important;
  font-weight: 500 !important;
}

/* ===== Buttons ===== */
.a-card .primary,
.a-card .secondary,
.a-card .stop {
  border-radius: var(--a-radius-sm) !important;
  font-weight: 600 !important;
  font-size: 13px !important;
  letter-spacing: -0.2px !important;
  padding: 9px 16px !important;
  transition: var(--a-transition) !important;
  box-shadow: none !important;
  border: 1px solid transparent !important;
}
.a-card .primary {
  background: var(--a-primary) !important;
  color: #fff !important;
  border-color: var(--a-primary) !important;
}
.a-card .primary:hover {
  background: var(--a-primary-hover) !important;
  box-shadow: 0 4px 12px rgba(37,99,235,0.2) !important;
}
.a-card .secondary {
  background: var(--a-bg) !important;
  border-color: var(--a-border) !important;
  color: var(--a-text) !important;
}
.a-card .secondary:hover {
  background: #e8e8ed !important;
}
.a-card .stop {
  background: #fef2f2 !important;
  border-color: #fecaca !important;
  color: var(--a-error) !important;
}
.a-card .stop:hover {
  background: #fee2e2 !important;
}

/* ===== Inputs ===== */
.a-card input[type="text"],
.a-card input[type="password"],
.a-card textarea,
.a-card select {
  border-radius: var(--a-radius-sm) !important;
  border: 1px solid var(--a-border) !important;
  background: #fff !important;
  color: var(--a-text) !important;
  font-size: 13px !important;
  padding: 9px 12px !important;
  min-height: 38px !important;
  transition: var(--a-transition) !important;
}
.a-card input:focus,
.a-card textarea:focus,
.a-card select:focus {
  border-color: var(--a-primary) !important;
  box-shadow: 0 0 0 3px rgba(37,99,235,0.12) !important;
  outline: none !important;
}

/* ===== Labels & Info ===== */
.a-card label,
.a-card div > label {
  font-size: 12px !important;
  font-weight: 600 !important;
  color: var(--a-text-secondary) !important;
  margin-bottom: 5px !important;
  letter-spacing: -0.2px !important;
}

/* ===== Checkbox / Radio ===== */
.a-card input[type="checkbox"] {
  accent-color: var(--a-primary) !important;
  width: 16px !important;
  height: 16px !important;
}

/* Radio selected - white text on blue background */
label.selected.svelte-19qdtil {
  color: #fff !important;
}

/* ===== Slider ===== */
.a-card input[type="range"] {
  accent-color: var(--a-primary) !important;
}

/* ===== Accordion ===== */
.a-card [class*="accordion-container"] {
  border: 1px solid var(--a-border) !important;
  border-radius: var(--a-radius-sm) !important;
  background: var(--a-surface) !important;
  margin-top: 10px !important;
}
.a-card [class*="accordion-container"] .label-wrap.svelte-e5lyqv {
  background: #fafbfc !important;
  padding: 10px 14px !important;
  border-bottom: 1px solid var(--a-border) !important;
  border-radius: var(--a-radius-sm) var(--a-radius-sm) 0 0 !important;
}
.a-card [class*="accordion-container"] .label-wrap.svelte-e5lyqv span {
  font-weight: 600 !important;
  font-size: 13px !important;
  color: var(--a-text) !important;
}
.a-card [class*="accordion-container"] [data-testid="accordion-content"] {
  padding: 14px !important;
  background: #fff !important;
  border-radius: 0 0 var(--a-radius-sm) var(--a-radius-sm) !important;
}

/* ===== Status Panel ===== */
.a-status {
  background: var(--a-surface) !important;
  border: 1px solid var(--a-border) !important;
  border-radius: var(--a-radius) !important;
  padding: 16px 20px !important;
  box-shadow: var(--a-shadow) !important;
}
.a-status-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.a-status-title {
  font-weight: 700;
  font-size: 14px;
  color: var(--a-text);
  letter-spacing: -0.3px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.a-tag {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: var(--a-radius-pill);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.a-tag.idle { background: var(--a-bg); color: var(--a-text-secondary); }
.a-tag.run { background: rgba(37,99,235,0.08); color: var(--a-primary); }
.a-tag.ok { background: rgba(34,197,94,0.08); color: #16a34a; }
.a-tag.err { background: rgba(239,68,68,0.08); color: #dc2626; }
.a-track {
  height: 5px;
  background: var(--a-border);
  border-radius: 3px;
  overflow: hidden;
}
.a-track-fill {
  height: 100%;
  width: 0%;
  background: linear-gradient(90deg, var(--a-primary), #60a5fa);
  border-radius: 3px;
  transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}
.a-track-meta {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  font-size: 12px;
  color: var(--a-text-secondary);
}
.a-track-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--a-border);
}
.a-track-cell {
  text-align: center;
}
.a-track-cell strong {
  display: block;
  font-size: 15px;
  font-weight: 700;
  color: var(--a-text);
  letter-spacing: -0.3px;
}
.a-track-cell span {
  font-size: 11px;
  color: var(--a-text-secondary);
  margin-top: 2px;
  display: block;
}

/* ===== Log ===== */
.a-log textarea {
  font-family: "SF Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
  background: #1c1c1e !important;
  color: #f5f5f7 !important;
  border: none !important;
  border-radius: var(--a-radius-sm) !important;
  min-height: 380px !important;
  padding: 14px !important;
}

/* ===== Stats ===== */
.a-stats textarea {
  font-family: "SF Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
  background: #fafbfc !important;
  color: var(--a-text) !important;
  border: 1px solid var(--a-border) !important;
  border-radius: var(--a-radius-sm) !important;
  min-height: 100px !important;
  padding: 12px !important;
}

/* ===== JSON ===== */
.a-json pre,
.a-json code {
  background: #fafbfc !important;
  border: 1px solid var(--a-border) !important;
  border-radius: var(--a-radius-sm) !important;
  font-size: 12px !important;
  padding: 12px !important;
}

/* ===== Compact ===== */
.a-compact textarea { min-height: 40px !important; }

/* ===== Remove default block borders inside cards ===== */
.a-card > div > div {
  border-color: transparent !important;
}

/* ===== Responsive ===== */
@media (max-width: 900px) {
  [class*="gradio-container"] { padding: 20px 16px 32px !important; }
  .a-header { flex-direction: column; align-items: flex-start; gap: 12px; }
  .a-title-wrap h1 { font-size: 20px; }
  .a-card { padding: 16px !important; }
  .a-log textarea { min-height: 280px !important; }
  .a-track-grid { grid-template-columns: repeat(2, 1fr); }
}
"""

def build_ui():
    if gr is None:
        raise RuntimeError("未安装 gradio，请先安装 `pip install gradio`")

    initial_db_choices = _list_databases()
    default_db = _choose_valid_value(MILVUS_DEFAULT_DB, initial_db_choices) or MILVUS_DEFAULT_DB
    initial_choices = _current_collection_choices(default_db)
    default_choice = _choose_valid_value(MILVUS_DEFAULT_COLLECTION, initial_choices)


    with gr.Blocks(
        title="SIQ 知识库管理系统 V8.1",
    ) as demo:
        runtime_timer = gr.Timer(1.0)

        # ===== Apple-style Header =====
        gr.HTML("""
        <div class="a-header">
          <div class="a-brand">
            <span class="a-logo">SIQ</span>
            <div class="a-title-wrap">
              <h1>知识库管理系统 <span style="font-size:14px;color:#86868b;font-weight:500;">V8.1</span></h1>
              <p>高质量证据入库 · 结构化切片 · Graph RAG sidecar</p>
            </div>
          </div>
          <div class="a-health">
            <span class="a-pill"><span class="a-dot"></span>Milvus</span>
            <span class="a-pill"><span class="a-dot"></span>MinerU</span>
            <span class="a-pill"><span class="a-dot"></span>Embedding</span>
          </div>
        </div>
        """)

        with gr.Tabs(elem_classes=["a-tabs"]):
            with gr.Tab("📥 知识入库"):
                with gr.Row(equal_height=False, elem_classes=["a-workspace"]):
                    with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):

                        with gr.Group(elem_classes=["a-card", "a-upload"]):
                            gr.Markdown("### 文档来源")
                            uploaded_docs = gr.File(
                                label="上传项目文件夹",
                                file_count="directory",
                                file_types=[".pdf", ".docx", ".md", ".txt"],
                                type="filepath",
                                interactive=True
                            )
                            gr.Markdown("上传后系统按原目录结构处理 PDF、DOCX、MD、TXT。")

                            doc_dir = gr.Textbox(
                                label="服务器路径（备用）",
                                value="",
                                placeholder="/home/maoyd/Desktop/knowledge/project_materials",
                                info="未上传文件夹时，使用此服务器绝对路径直接入库。"
                            )

                        with gr.Group(elem_classes=["a-card"]):
                            gr.Markdown("### 项目配置")
                            db_dropdown = gr.Dropdown(
                                choices=initial_db_choices,
                                value=default_db,
                                label="Milvus Database"
                            )
                            coll_dropdown = gr.Dropdown(
                                choices=initial_choices,
                                value=default_choice,
                                label="目标 Collection"
                            )
                            refresh_ingest_collections_btn = gr.Button("刷新", variant="secondary")

                            batch_tag = gr.Textbox(
                                label="批次标签",
                                value=lambda: f"SIQ-PROJECT-{datetime.now().strftime('%Y')}",
                                placeholder="SIQ-DAJIN-2026",
                                info="用于项目隔离和检索过滤。"
                            )

                            reset_check = gr.Checkbox(
                                label="重置并清空已有数据",
                                value=False,
                                info="删除后重建 Collection，谨慎使用。",
                                interactive=True
                            )

                        with gr.Group(elem_classes=["a-card"]):
                            gr.Markdown("### 入库策略")
                            embed_backend = gr.Radio(
                                choices=["vllm (本地)", "DashScope (阿里云)", "MiniMax (云端)"],
                                value="vllm (本地)",
                                label="Embedding 后端"
                            )
                            pdf_parse_mode = gr.Radio(
                                choices=PDF_PARSE_MODES,
                                value=PDF_PARSE_MODE_AUTO,
                                label="PDF 解析引擎",
                                info="共享底稿默认 MinerU，私有库默认 PyMuPDF。"
                            )

                            api_key = gr.Textbox(
                                label="云端 API Key",
                                type="password",
                                visible=False,
                                info="选择 DashScope 或 MiniMax 时填写"
                            )

                            minimax_model = gr.Textbox(
                                label="MiniMax 模型名",
                                value=MINIMAX_EMBED_MODEL,
                                visible=False,
                                info="默认读取 MINIMAX_EMBED_MODEL 环境变量"
                            )

                            with gr.Accordion("切片参数", open=False):
                                chunk_mode = gr.Radio(
                                    choices=CHUNK_MODES,
                                    value=CHUNK_MODE_AUTO,
                                    label="切片模式",
                                    info="默认已按标题、段落、法条优先切片。"
                                )
                                chunk_size_slider = gr.Slider(
                                    minimum=256, maximum=2048, step=32,
                                    value=DEFAULT_CHUNK_SIZE,
                                    label="切片字符数",
                                    info="仅手动模式生效",
                                    interactive=False
                                )
                                chunk_overlap_slider = gr.Slider(
                                    minimum=0, maximum=1024, step=16,
                                    value=DEFAULT_CHUNK_OVERLAP,
                                    label="重叠字符数",
                                    info="仅手动模式生效",
                                    interactive=False
                                )

                            with gr.Row():
                                table_chunk_check = gr.Checkbox(
                                    label="提取 MinerU 表格",
                                    value=True,
                                    info="生成 table_chunk，提升财务/股权表召回。",
                                    interactive=True
                                )
                                visual_chunk_check = gr.Checkbox(
                                    label="提取 MinerU 图片",
                                    value=True,
                                    info="保留图片路径与 caption，vLLM 支持时向量化。",
                                    interactive=True
                                )

                            with gr.Accordion("Graph Sidecar", open=False):
                                graph_sidecar_check = gr.Checkbox(
                                    label="同步到 Vector Graph RAG",
                                    value=False,
                                    info="仅支持 ic_collaboration_shared。",
                                    interactive=True
                                )
                                graph_prefix = gr.Textbox(
                                    label="Sidecar prefix",
                                    value=GRAPH_SIDECAR_DEFAULT_PREFIX,
                                    info="将创建 {prefix}_vgrag_entities / relations / passages"
                                )
                                graph_reset_check = gr.Checkbox(
                                    label="重建 sidecar 表",
                                    value=False,
                                    info="删除并重建上述三张表。",
                                    interactive=True
                                )

                        def toggle_api_key(backend):
                            is_cloud = backend in {"DashScope (阿里云)", "MiniMax (云端)"}
                            is_minimax = backend == "MiniMax (云端)"
                            return gr.update(visible=is_cloud), gr.update(visible=is_minimax)

                        embed_backend.change(
                            fn=toggle_api_key,
                            inputs=[embed_backend],
                            outputs=[api_key, minimax_model]
                        )

                        with gr.Group(elem_classes=["a-card"]):
                            gr.Markdown("### 执行")
                            with gr.Row():
                                start_btn = gr.Button("开始入库", variant="primary")
                                pause_btn = gr.Button("暂停", variant="secondary")
                                resume_btn = gr.Button("继续", variant="secondary")
                            control_output = gr.Textbox(
                                label="控制反馈",
                                interactive=False,
                                elem_classes=["a-compact"]
                            )
                            with gr.Accordion("技术参数", open=False):
                                gr.Markdown("""
                                **索引**: HNSW (M=32, efConstruction=256, metric=L2)  
                                **维度**: 1024  
                                **Metadata**: SIQChunkMetadata v1  
                                **分块**: 结构优先 + 按文件类型动态切分  
                                **视觉**: vLLM → OCR → 文本代理兜底  
                                **质量报告**: `ingest_quality_reports/`
                                """)

                    with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):

                        with gr.Group(elem_classes=["a-status"]):
                            gr.HTML("""
                            <div class="a-status-header">
                              <div class="a-status-title">📊 运行状态</div>
                              <span class="a-tag idle" id="aStatusBadge">等待中</span>
                            </div>
                            <div class="a-track">
                              <div class="a-track-fill" id="aProgressBar" style="width:0%"></div>
                            </div>
                            <div class="a-track-meta">
                              <span id="aStatusText">暂无运行中的任务</span>
                              <span id="aStatusPct">0%</span>
                            </div>
                            <div class="a-track-grid">
                              <div class="a-track-cell"><strong id="aCellPhase">-</strong><span>阶段</span></div>
                              <div class="a-track-cell"><strong id="aCellFiles">-</strong><span>文件进度</span></div>
                              <div class="a-track-cell"><strong id="aCellInserted">-</strong><span>已入库</span></div>
                              <div class="a-track-cell"><strong id="aCellTime" style="color:var(--a-primary)">-</strong><span>耗时</span></div>
                            </div>
                            """)

                        with gr.Group(elem_classes=["a-card"]):
                            gr.Markdown("### 运行日志")
                            log_output = gr.Textbox(
                                label="",
                                lines=24,
                                interactive=False,
                                max_lines=120,
                                elem_classes=["a-log"],
                                show_label=False
                            )

                        with gr.Group(elem_classes=["a-card"]):
                            gr.Markdown("### 统计信息")
                            stats_output = gr.Textbox(
                                label="",
                                lines=8,
                                max_lines=20,
                                interactive=False,
                                elem_classes=["a-stats"],
                                show_label=False
                            )

            with gr.Tab("🗄️ Collection 管理"):
                with gr.Row(equal_height=False, elem_classes=["a-workspace"]):
                    with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):
                        with gr.Group(elem_classes=["a-card"]):
                            gr.Markdown("### 状态总览")
                            manage_db_dropdown = gr.Dropdown(
                                choices=initial_db_choices,
                                value=default_db,
                                label="Milvus Database"
                            )
                            refresh_btn = gr.Button("刷新状态", variant="secondary")
                            collection_status_json = gr.JSON(
                                label="Collection 列表",
                                elem_classes=["a-json"]
                            )
                            collection_status_msg = gr.Textbox(
                                label="状态摘要",
                                interactive=False
                            )

                    with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):
                        with gr.Group(elem_classes=["a-card"]):
                            gr.Markdown("### Collection 操作")
                            create_name = gr.Textbox(
                                label="新 Collection 名称",
                                placeholder="ic_custom_workspace"
                            )
                            create_btn = gr.Button("创建", variant="primary")
                            create_result = gr.Textbox(
                                label="结果",
                                interactive=False
                            )

                            drop_coll_dropdown = gr.Dropdown(
                                choices=initial_choices,
                                value=default_choice,
                                label="删除 Collection"
                            )
                            drop_btn = gr.Button("删除", variant="stop")
                            drop_result = gr.Textbox(label="结果", interactive=False)

                            rebuild_coll_dropdown = gr.Dropdown(
                                choices=initial_choices,
                                value=default_choice,
                                label="重建索引"
                            )
                            rebuild_btn = gr.Button("重建", variant="secondary")
                            rebuild_result = gr.Textbox(label="结果", interactive=False)

                        with gr.Group(elem_classes=["a-card"]):
                            gr.Markdown("### Tag 与 Graph")
                            tag_stats_coll_dropdown = gr.Dropdown(
                                choices=initial_choices,
                                value=default_choice,
                                label="查看 Tag 统计"
                            )
                            tag_stats_btn = gr.Button("统计", variant="secondary")
                            tag_stats_output = gr.Textbox(
                                label="统计结果",
                                lines=8,
                                interactive=False
                            )

                            graph_manage_prefix = gr.Textbox(
                                label="Graph prefix",
                                value=GRAPH_SIDECAR_DEFAULT_PREFIX,
                            )
                            graph_manage_reset = gr.Checkbox(
                                label="重建 Graph sidecar",
                                value=False,
                                interactive=True,
                            )
                            graph_create_btn = gr.Button(
                                "初始化 Graph sidecar",
                                variant="secondary"
                            )
                            graph_create_result = gr.Textbox(
                                label="结果",
                                lines=6,
                                interactive=False
                            )

        def _adjust_overlap(size, current_overlap):
            size_val = int(size)
            max_overlap = max(0, size_val - 1)
            overlap_val = min(int(current_overlap), max_overlap)
            return gr.update(maximum=max_overlap, value=overlap_val)

        def _toggle_chunk_controls(mode):
            manual = mode == CHUNK_MODE_MANUAL
            return gr.update(interactive=manual), gr.update(interactive=manual)

        def on_pause():
            INGEST_CONTROL["paused"] = True
            msg = "⏸️ 已请求暂停，当前文件完成后会暂停。"
            _append_runtime_log(msg, {"paused": True})
            return msg

        def on_resume():
            INGEST_CONTROL["paused"] = False
            msg = "▶️ 已恢复入库。"
            _append_runtime_log(msg, {"paused": False})
            return msg

        async def on_start(
            db_name, coll, uploaded_docs_value, doc_dir, tag, backend, key, minimax_model_name, reset,
            chunk_mode_value, chunk_size, chunk_overlap, pdf_parse_mode_value, table_chunks, visual_chunks,
            graph_enabled, graph_prefix_value, graph_reset
        ):
            resolved_doc_dir, source_mode, uploaded_count, uploaded_paths = _resolve_document_root(uploaded_docs_value, doc_dir)
            if not resolved_doc_dir or not Path(resolved_doc_dir).exists():
                return "❌ 未选择有效文档目录，请上传文件夹或填写服务器绝对路径", ""

            db_name = _normalize_db_name(db_name)
            coll = _normalize_collection_name(coll)
            graph_prefix_value = _normalize_graph_prefix(graph_prefix_value) if graph_enabled else ""
            if graph_enabled and coll != "ic_collaboration_shared":
                return "❌ Graph sidecar 当前仅支持 ic_collaboration_shared（项目共享底稿库）", ""

            if chunk_mode_value == CHUNK_MODE_AUTO:
                chunk_size = DEFAULT_CHUNK_SIZE
                chunk_overlap = DEFAULT_CHUNK_OVERLAP
            else:
                chunk_size = int(chunk_size)
                chunk_overlap = int(chunk_overlap)
            if chunk_overlap >= chunk_size:
                chunk_overlap = max(0, chunk_size - 1)

            global MINIMAX_EMBED_MODEL
            if backend == "MiniMax (云端)":
                MINIMAX_EMBED_MODEL = (minimax_model_name or MINIMAX_EMBED_MODEL).strip()

            INGEST_CONTROL["paused"] = False
            now = datetime.now().isoformat(timespec="seconds")
            task_config = {
                "database": db_name,
                "collection": coll,
                "doc_dir": str(resolved_doc_dir),
                "source_mode": source_mode,
                "uploaded_files": uploaded_count,
                "tag": str(tag or ""),
                "backend": backend,
                "chunk_mode": chunk_mode_value,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "pdf_parse_mode": pdf_parse_mode_value,
                "table_chunks": bool(table_chunks),
                "visual_chunks": bool(visual_chunks),
                "graph_sidecar_prefix": graph_prefix_value,
                "graph_sidecar_reset": bool(graph_reset),
            }
            initial_meta = {
                "phase": "init",
                "processed_files": 0,
                "total_files": 0,
                "inserted_total": 0,
                "current_file": "-",
                "paused": False,
            }
            with INGEST_RUNTIME_LOCK:
                existing_task = INGEST_RUNTIME.get("task")
                if INGEST_RUNTIME.get("active") and existing_task and not existing_task.done():
                    return _runtime_public_snapshot()
                INGEST_RUNTIME.update({
                    "active": True,
                    "started_at": now,
                    "updated_at": now,
                    "finished_at": "",
                    "logs": [
                        f"🚀 已开始任务，Database={db_name}，Collection={coll}，来源={source_mode or 'unknown'}，目录={resolved_doc_dir}，后端={backend}，切片/重叠={chunk_size}/{chunk_overlap}",
                    ],
                    "meta": initial_meta,
                    "config": task_config,
                    "result": "",
                    "task": asyncio.create_task(_run_ingest_background(
                        db_name,
                        coll,
                        str(resolved_doc_dir),
                        uploaded_paths,
                        str(tag or ""),
                        backend,
                        str(key or ""),
                        bool(reset),
                        chunk_size,
                        chunk_overlap,
                        pdf_parse_mode_value,
                        bool(table_chunks),
                        bool(visual_chunks),
                        graph_prefix_value,
                        bool(graph_reset),
                    )),
                })
                _save_runtime_state_unlocked()
            return _runtime_public_snapshot()

        chunk_size_slider.change(
            fn=_adjust_overlap,
            inputs=[chunk_size_slider, chunk_overlap_slider],
            outputs=[chunk_overlap_slider],
            queue=False,
        )
        chunk_mode.change(
            fn=_toggle_chunk_controls,
            inputs=[chunk_mode],
            outputs=[chunk_size_slider, chunk_overlap_slider],
            queue=False,
        )

        pause_btn.click(fn=on_pause, outputs=[control_output], queue=False)
        resume_btn.click(fn=on_resume, outputs=[control_output], queue=False)

        start_btn.click(
            fn=on_start,
            inputs=[
                db_dropdown,
                coll_dropdown,
                uploaded_docs,
                doc_dir,
                batch_tag,
                embed_backend,
                api_key,
                minimax_model,
                reset_check,
                chunk_mode,
                chunk_size_slider,
                chunk_overlap_slider,
                pdf_parse_mode,
                table_chunk_check,
                visual_chunk_check,
                graph_sidecar_check,
                graph_prefix,
                graph_reset_check,
            ],
            outputs=[log_output, stats_output]
        )

        def on_refresh(current_db, current_ingest, current_drop, current_rebuild, current_tag):
            return _refresh_ui_state(current_db, current_ingest, current_drop, current_rebuild, current_tag)

        refresh_outputs = [
            collection_status_json,
            collection_status_msg,
            db_dropdown,
            manage_db_dropdown,
            coll_dropdown,
            drop_coll_dropdown,
            rebuild_coll_dropdown,
            tag_stats_coll_dropdown,
        ]
        refresh_inputs = [manage_db_dropdown, coll_dropdown, drop_coll_dropdown, rebuild_coll_dropdown, tag_stats_coll_dropdown]
        ingest_refresh_inputs = [db_dropdown, coll_dropdown, drop_coll_dropdown, rebuild_coll_dropdown, tag_stats_coll_dropdown]

        refresh_btn.click(fn=on_refresh, inputs=refresh_inputs, outputs=refresh_outputs)
        refresh_ingest_collections_btn.click(fn=on_refresh, inputs=ingest_refresh_inputs, outputs=refresh_outputs)
        db_dropdown.change(fn=on_refresh, inputs=ingest_refresh_inputs, outputs=refresh_outputs, queue=False)
        manage_db_dropdown.change(fn=on_refresh, inputs=refresh_inputs, outputs=refresh_outputs, queue=False)

        create_btn.click(
            fn=_create_collection,
            inputs=[manage_db_dropdown, create_name],
            outputs=[create_result]
        ).then(fn=on_refresh, inputs=refresh_inputs, outputs=refresh_outputs)

        drop_btn.click(
            fn=_drop_collection,
            inputs=[manage_db_dropdown, drop_coll_dropdown],
            outputs=[drop_result]
        ).then(fn=on_refresh, inputs=refresh_inputs, outputs=refresh_outputs)

        rebuild_btn.click(
            fn=_rebuild_index,
            inputs=[manage_db_dropdown, rebuild_coll_dropdown],
            outputs=[rebuild_result]
        ).then(fn=on_refresh, inputs=refresh_inputs, outputs=refresh_outputs)

        tag_stats_btn.click(
            fn=_get_tag_stats,
            inputs=[manage_db_dropdown, tag_stats_coll_dropdown],
            outputs=[tag_stats_output]
        )

        graph_create_btn.click(
            fn=_create_graph_sidecar,
            inputs=[manage_db_dropdown, graph_manage_prefix, graph_manage_reset],
            outputs=[graph_create_result]
        )

        runtime_timer.tick(fn=_runtime_public_snapshot, outputs=[log_output, stats_output], queue=False)
        demo.load(fn=_runtime_public_snapshot, outputs=[log_output, stats_output], queue=False)
        demo.load(fn=on_refresh, inputs=refresh_inputs, outputs=refresh_outputs)

    return demo

def _resolve_launch_port() -> int:
    try:
        preferred = int(os.getenv("GRADIO_SERVER_PORT", "7862"))
    except ValueError:
        preferred = 7860
    try:
        max_port = int(os.getenv("GRADIO_SERVER_PORT_MAX", str(preferred + 20)))
    except ValueError:
        max_port = preferred + 20
    if max_port < preferred:
        max_port = preferred

    for port in range(preferred, max_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise OSError(f"没有可用端口（范围 {preferred}-{max_port}）")


if __name__ == "__main__":
    app = build_ui()
    launch_port = _resolve_launch_port()
    app.launch(
        server_name="0.0.0.0",
        server_port=launch_port,
        css=INGEST_UI_CSS,
        theme=gr.themes.Soft(primary_hue="blue", neutral_hue="slate", radius_size="lg", spacing_size="sm"),
    )
