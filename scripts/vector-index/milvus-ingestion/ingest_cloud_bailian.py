#!/usr/bin/env python3
"""
Cloud-only SIQ Milvus ingestor powered by Alibaba Cloud Model Studio.

This script is intentionally independent from ingest_final.py. It does not call
local MinerU, vLLM, OCR, reranker, or any other localhost model service.

Default pipeline:
  local files -> lightweight local parsing -> Bailian embeddings/captions -> Milvus

Examples:
  export DASHSCOPE_API_KEY=sk-...
  python ingest_cloud_bailian.py \
    --input-dir /path/to/project_docs \
    --project-tag SIQ-PROJECT-2026 \
    --milvus-uri http://127.0.0.1:19530 \
    --db-name default \
    --collection ic_collaboration_shared \
    --enable-visual \
    --enable-captions \
    --enable-vgrag-passages

For remote Milvus or Zilliz Cloud:
  python ingest_cloud_bailian.py \
    --input-dir ./docs \
    --project-tag SIQ-PROJECT-2026 \
    --milvus-uri https://in03-xxxx.api.gcp-us-west1.zillizcloud.com \
    --milvus-token "$ZILLIZ_TOKEN" \
    --collection ic_collaboration_shared
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import requests

try:
    import fitz  # PyMuPDF
except ModuleNotFoundError:
    fitz = None

try:
    from docx import Document
except ModuleNotFoundError:
    Document = None

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None

try:
    import gradio as gr
except ModuleNotFoundError:
    gr = None

try:
    from pymilvus import (
        Collection,
        CollectionSchema,
        DataType,
        FieldSchema,
        MilvusClient,
        connections,
        utility,
    )
except ModuleNotFoundError:
    Collection = CollectionSchema = DataType = FieldSchema = MilvusClient = None
    connections = utility = None


SCRIPT_DIR = Path(__file__).resolve().parent
METADATA_SCHEMA_VERSION = "siq_chunk_v1"
PIPELINE_VERSION = "cloud_bailian_v1"

DEFAULT_DIM = 1024
DEFAULT_COLLECTION = "ic_collaboration_shared"
DEFAULT_DB = os.getenv("MILVUS_DB_NAME", "default").strip() or "default"
DEFAULT_MILVUS_URI = os.getenv("MILVUS_URI", "http://127.0.0.1:19530").strip()

DEFAULT_DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com").rstrip("/")
TEXT_EMBED_ENDPOINT = "/api/v1/services/embeddings/text-embedding/text-embedding"
MM_EMBED_ENDPOINT = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
CHAT_COMPLETIONS_ENDPOINT = "/compatible-mode/v1/chat/completions"

TEXT_EMBED_MODEL = "text-embedding-v4"
MULTIMODAL_EMBED_MODEL = "qwen3-vl-embedding"
DEFAULT_CAPTION_MODEL = os.getenv("BAILIAN_CAPTION_MODEL", "qwen3-vl-flash").strip() or "qwen3-vl-flash"

INDEX_PARAMS = {
    "metric_type": "L2",
    "index_type": "HNSW",
    "params": {"M": 32, "efConstruction": 256},
}
GRAPH_INDEX_PARAMS = {"M": 32, "efConstruction": 256}

ROLE_REGISTRY = {
    "ic_chairman": "SIQ investment committee chairman",
    "ic_finance_auditor": "SIQ finance auditor",
    "ic_sector_expert": "SIQ sector expert",
    "ic_legal_scanner": "SIQ legal scanner",
    "ic_strategist": "SIQ strategist",
    "ic_risk_controller": "SIQ risk controller",
    "ic_master_coordinator": "SIQ master coordinator",
    "ic_collaboration_shared": "SIQ shared project evidence workspace",
    "ic_archive_sop": "SIQ historical cases and SOP archive",
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

CHUNK_POLICY_BY_EXT = {
    "pdf": {"size": 760, "overlap": 120, "min_len": 80},
    "docx": {"size": 900, "overlap": 140, "min_len": 100},
    "md": {"size": 1100, "overlap": 140, "min_len": 120},
    "txt": {"size": 900, "overlap": 120, "min_len": 100},
    "legal": {"size": 900, "overlap": 80, "min_len": 70},
    "finance": {"size": 900, "overlap": 100, "min_len": 80},
    "discussion": {"size": 900, "overlap": 80, "min_len": 80},
    "table": {"size": 1400, "overlap": 80, "min_len": 40},
    "visual": {"size": 700, "overlap": 60, "min_len": 20},
    "default": {"size": 700, "overlap": 120, "min_len": 90},
}

TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
DOCX_EXTENSIONS = {".docx"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCX_EXTENSIONS | PDF_EXTENSIONS | IMAGE_EXTENSIONS

GRAPH_ENTITY_COLLECTION = "vgrag_entities"
GRAPH_RELATION_COLLECTION = "vgrag_relations"
GRAPH_PASSAGE_COLLECTION = "vgrag_passages"

GRAPH_ENTITY_RULES = {
    "product": [
        r"风电基础装备",
        r"海上风电基础装备",
        r"单桩(?:基础)?",
        r"过渡段",
        r"导管架",
        r"浮式基础",
        r"塔筒",
        r"甲板运输船",
        r"重吊运输船",
        r"特种运输船(?:队)?",
        r"风电场",
        r"光伏电场",
    ],
    "region": [
        r"欧洲",
        r"亚太(?:地区)?",
        r"全球",
        r"中国",
        r"美国",
        r"香港",
        r"曹妃甸",
        r"蓬莱",
        r"唐山",
        r"山东",
    ],
    "capacity_metric": [
        r"\d+(?:\.\d+)?\s*(?:万吨|吨|GW|MW|万千瓦|亿千瓦时|亿元|万元|%)",
    ],
    "certification": [
        r"SBTi",
        r"EcoVadis(?:铜牌)?",
        r"CDP(?:评级)?",
        r"ISO\s*\d+",
        r"绿钢战略框架协议",
    ],
    "capability": [
        r"全球化运营能力",
        r"可持续发展能力",
        r"工艺技术创新设计能力",
        r"建造\+运输\+交付",
        r"一站式解决方案",
    ],
    "financial_metric": [
        r"(?:收入|营收|毛利率|净利润|EBITDA|现金流|销售金额|订单|资产|负债)[^\n，。；:：]{0,24}(?:\d+(?:\.\d+)?\s*(?:亿元|万元|%|倍))?",
    ],
    "legal_clause": [
        r"第[一二三四五六七八九十百千万0-9]+条",
        r"保密义务",
        r"违约责任",
        r"陈述与保证",
        r"交割条件",
    ],
    "regulation": [
        r"《[^》]{2,40}》",
    ],
    "risk": [
        r"(?:风险|诉讼|仲裁|处罚|限制|禁止|不得|违约|不确定性)",
    ],
    "date": [
        r"\d{4}年\d{1,2}月(?:\d{1,2}日)?",
        r"截至\d{4}年\d{1,2}月末",
    ],
    "stakeholder": [
        r"(?:客户|供应商|开发商|投资者|发行人|买方|卖方|债权人|债务人)",
    ],
}

GRAPH_PROFILE_ENTITY_TYPES = {
    "teaser": {"company", "product", "region", "capacity_metric", "certification", "capability", "stakeholder", "date"},
    "financials": {"company", "financial_metric", "capacity_metric", "date", "risk", "stakeholder"},
    "legal": {"company", "legal_clause", "regulation", "risk", "date", "stakeholder"},
    "industry_research": {"company", "product", "region", "capacity_metric", "financial_metric", "date"},
    "meeting_note": {"company", "product", "region", "financial_metric", "risk", "stakeholder", "date"},
    "committee_opinion": {"company", "product", "financial_metric", "risk", "stakeholder", "date"},
    "sop": {"company", "capability", "risk", "stakeholder"},
    "default": set(GRAPH_ENTITY_RULES.keys()),
}


@dataclass
class ChunkItem:
    text: str
    meta: Dict[str, Any]
    image_path: Optional[Path] = None
    vector: Optional[List[float]] = None
    skipped_reason: str = ""


@dataclass
class IngestStats:
    files_seen: int = 0
    files_ingested: int = 0
    chunks_created: int = 0
    chunks_inserted: int = 0
    chunks_skipped: int = 0
    text_embedding_calls: int = 0
    visual_embedding_calls: int = 0
    caption_calls: int = 0
    errors: List[str] = field(default_factory=list)


CLOUD_RUNTIME_LOCK = threading.RLock()
CLOUD_RUNTIME = {
    "active": False,
    "paused": False,
    "started_at": "",
    "finished_at": "",
    "updated_at": "",
    "logs": [],
    "config": {},
    "stats": {},
    "result": "",
}
MAX_RUNTIME_LOGS = 600


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clean_text(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)

    raw_lines = [line.rstrip() for line in text.split("\n")]
    counts: Counter[str] = Counter()
    for line in raw_lines:
        normalized = re.sub(r"\s+", " ", line).strip()
        normalized = re.sub(r"\d+", "#", normalized)
        if 0 < len(normalized) <= 90:
            counts[normalized] += 1

    cleaned: List[str] = []
    for line in raw_lines:
        stripped = line.strip()
        normalized = re.sub(r"\s+", " ", stripped)
        normalized = re.sub(r"\d+", "#", normalized)
        repeated_noise = (
            counts.get(normalized, 0) >= 3
            and not re.match(r"^(#{1,6}\s+|第[一二三四五六七八九十百千万0-9]+[章节条款部分])", stripped)
            and not re.search(r"(收入|利润|客户|供应商|股东|诉讼|风险|订单|金额|合计|资产|负债)", stripped)
        )
        page_noise = bool(re.match(r"^(第\s*)?\d+\s*(页|/|-|of)\s*\d*$", stripped, re.IGNORECASE))
        disclaimer = len(stripped) <= 100 and bool(re.search(
            r"(免责声明|仅供参考|confidential|disclaimer|本文件.*保密|内部资料.*请勿外传|未经.*许可.*不得)",
            stripped,
            re.IGNORECASE,
        ))
        if repeated_noise or page_noise or disclaimer:
            continue
        cleaned.append(line)

    text = "\n".join(cleaned)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def normalize_dashscope_api_key(value: str) -> str:
    key = str(value or "").strip().strip("\"'")
    key = re.sub(r"^\s*(?:export\s+)?DASHSCOPE_API_KEY\s*=\s*", "", key).strip().strip("\"'")
    key = re.sub(r"^\s*Bearer\s+", "", key, flags=re.IGNORECASE).strip()
    key = key.split()[0] if key else ""
    return key


def validate_dashscope_api_key(value: str) -> str:
    key = normalize_dashscope_api_key(value)
    if not key:
        return ""
    try:
        key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "阿里百炼 API Key 含有中文或其他非 ASCII 字符，请只粘贴 sk-... 这一段密钥，"
            "不要包含中文说明、全角标点或网页提示文字"
        ) from exc
    if re.search(r"[\r\n\t]", key):
        raise ValueError("阿里百炼 API Key 含有换行或制表符，请只粘贴单行 sk-... 密钥")
    return key


def strip_html(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</t[dh]>\s*<t[dh][^>]*>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>\s*<tr[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def infer_doc_type(path: str, explicit_format: str = "") -> str:
    file_obj = Path(path)
    haystack = " ".join([file_obj.name, str(file_obj.parent), explicit_format]).lower()
    for doc_type, patterns in DOC_TYPE_PATTERNS:
        if any(p.lower() in haystack for p in patterns):
            return doc_type
    return "default"


def policy_key_for_doc_type(file_ext: str, doc_type: str) -> str:
    if file_ext in {"table", "visual"}:
        return file_ext
    if doc_type in {"legal", "financials", "meeting_note", "committee_opinion"}:
        return {
            "legal": "legal",
            "financials": "finance",
            "meeting_note": "discussion",
            "committee_opinion": "discussion",
        }[doc_type]
    return file_ext or "default"


def infer_language(text: str) -> str:
    if not text:
        return "unknown"
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    if zh >= max(10, ascii_letters * 0.4):
        return "zh"
    if ascii_letters > 0:
        return "en"
    return "unknown"


def line_offsets(text: str) -> List[Tuple[int, str]]:
    offsets: List[Tuple[int, str]] = []
    cursor = 0
    for line in text.splitlines(True):
        offsets.append((cursor, line.rstrip("\n")))
        cursor += len(line)
    return offsets


def page_for_offset(offsets: List[Tuple[int, str]], char_start: int) -> Optional[int]:
    page: Optional[int] = None
    for offset, line in offsets:
        if offset > char_start:
            break
        match = re.match(r"^\s*(?:\[PDF_PAGE:\s*(\d+)\]|<!--\s*PDF_PAGE:\s*(\d+)\s*-->)\s*$", line, re.I)
        if match:
            page = int(match.group(1) or match.group(2))
    return page


def heading_from_line(line: str) -> Optional[Tuple[int, str]]:
    stripped = re.sub(r"\s+", " ", str(line or "").strip())
    if not stripped or len(stripped) > 140:
        return None
    if re.match(r"^\[PDF_PAGE:\s*\d+\]$", stripped, re.I):
        return None
    if "|" in stripped and stripped.count("|") >= 2:
        return None

    md = re.match(r"^(#{1,6})\s+(.+)$", stripped)
    if md:
        return len(md.group(1)), md.group(2).strip(" #")[:100]

    cn = re.match(r"^(第[一二三四五六七八九十百千万0-9]+[章节条款部分])[\s、：:.-]*(.+)?$", stripped)
    if cn:
        marker = cn.group(1)
        title = stripped
        level = 2 if any(key in marker for key in ("章", "节", "部分")) else 3
        return level, title[:100]

    decimal = re.match(r"^(\d{1,2}(?:\.\d{1,2}){0,5})[\.、\s]+([^\d\s].+)$", stripped)
    if decimal and len(decimal.group(2)) <= 100:
        level = min(decimal.group(1).count(".") + 2, 6)
        return level, stripped[:100]

    cn_list = re.match(r"^([一二三四五六七八九十]+)[、.．]\s*(.{2,100})$", stripped)
    if cn_list:
        return 3, stripped[:100]

    bracket = re.match(r"^[（(]([一二三四五六七八九十0-9]+)[）)]\s*(.{2,100})$", stripped)
    if bracket:
        return 4, stripped[:100]

    return None


def section_path_for_offset(offsets: List[Tuple[int, str]], char_start: int) -> str:
    headings: List[Tuple[int, str]] = []
    for offset, line in offsets:
        if offset > char_start:
            break
        heading = heading_from_line(line)
        if not heading:
            continue

        level, title = heading
        title = re.sub(r"\s+", " ", title).strip(" #")
        if not title:
            continue
        headings = [(lvl, val) for lvl, val in headings if lvl < level]
        headings.append((level, title[:80]))
    return " / ".join(title for _, title in headings[-4:])


def structured_units(text: str, policy_key: str) -> List[Tuple[str, int, int]]:
    if policy_key not in {"md", "pdf", "docx", "legal", "discussion"}:
        return []
    text = clean_text(text)
    if not text:
        return []

    boundaries = [0]
    for offset, line in line_offsets(text):
        stripped = line.strip()
        if not stripped:
            continue
        is_page_marker = bool(re.match(r"^(?:\[PDF_PAGE:\s*\d+\]|<!--\s*PDF_PAGE:\s*\d+\s*-->)$", stripped, re.I))
        if offset > 0 and (is_page_marker or heading_from_line(stripped)):
            boundaries.append(offset)

    if policy_key == "discussion":
        patterns = [
            r"(?m)^【[^】]{1,40}】",
            r"(?m)^[A-Za-z0-9_\-\u4e00-\u9fff]{2,30}[：:]\s*",
        ]
        for pat in patterns:
            for match in re.finditer(pat, text):
                if match.start() > 0:
                    boundaries.append(match.start())
    boundaries = sorted(set(boundaries + [len(text)]))

    units: List[Tuple[str, int, int]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        raw = text[start:end]
        unit = raw.strip()
        if unit:
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            units.append((unit, start + leading, end - trailing))
    return units if len(units) > 1 else []


def split_unit_by_length(
    unit: str,
    base_start: int,
    policy: Dict[str, int],
    structural: bool = False,
) -> List[Tuple[str, int, int]]:
    size = policy["size"]
    overlap = min(policy["overlap"], size - 1)
    min_len = min(policy["min_len"], 30) if structural else policy["min_len"]

    if len(unit) <= size:
        return [(unit, base_start, base_start + len(unit))] if len(unit) >= min_len else []

    chunks: List[Tuple[str, int, int]] = []
    start = 0
    split_seps = ("\n\n", "\n", "。", "！", "？", "；", ". ", "; ", ", ", " ")
    while start < len(unit):
        end = min(start + size, len(unit))
        if end < len(unit):
            window = unit[start:end]
            for sep in split_seps:
                pos = window.rfind(sep)
                if pos >= int(size * 0.55):
                    end = start + pos + len(sep)
                    break
        raw = unit[start:end]
        chunk = raw.strip()
        if len(chunk) >= min_len:
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            chunks.append((chunk, base_start + start + leading, base_start + end - trailing))
        start = end - overlap if end < len(unit) else end
    return chunks


def smart_chunk(text: str, policy_key: str) -> List[Tuple[str, int, int]]:
    policy = CHUNK_POLICY_BY_EXT.get(policy_key, CHUNK_POLICY_BY_EXT["default"])
    text = clean_text(text)
    if not text:
        return []

    units = structured_units(text, policy_key)
    if units:
        chunks: List[Tuple[str, int, int]] = []
        for unit, start, _ in units:
            chunks.extend(split_unit_by_length(unit, start, policy, structural=True))
        if chunks:
            return chunks

    return split_unit_by_length(text, 0, policy, structural=False)


def build_citation(meta: Dict[str, Any]) -> str:
    bits = [str(meta.get("source") or "unknown")]
    if meta.get("page"):
        bits.append(f"p.{meta['page']}")
    if meta.get("section_path"):
        bits.append(str(meta["section_path"]))
    if meta.get("chunk_index"):
        bits.append(f"chunk {meta['chunk_index']}")
    return " | ".join(bits)


def collection_role(collection: str) -> str:
    if collection == "ic_collaboration_shared":
        return "shared"
    if collection == "ic_archive_sop":
        return "archive"
    if collection in ROLE_REGISTRY:
        return "private"
    return "custom"


def agent_id(collection: str) -> Optional[str]:
    return collection if collection_role(collection) == "private" else None


def build_embed_text(meta: Dict[str, Any], text: str) -> str:
    parts = [
        str(meta.get("source", "")),
        str(meta.get("section_path", "")),
        str(meta.get("doc_type", "")),
        str(meta.get("project_tag", "")),
        text,
    ]
    return "\n".join(part for part in parts if part)


def is_low_information_visual_caption(caption: str) -> bool:
    text = clean_text(caption).lower()
    if not text:
        return True
    low_info_patterns = [
        "纯白背景",
        "空白",
        "无任何可识别",
        "无法提取有效信息",
        "无有效信息",
        "未包含公司、产品",
        "未见公司、产品",
        "no identifiable",
        "no readable",
        "blank page",
    ]
    if any(pattern in text for pattern in low_info_patterns):
        return True
    evidence_terms = ["公司", "产品", "客户", "供应商", "财务", "指标", "金额", "收入", "利润", "风险", "时间", "产能", "认证"]
    return len(text) < 80 and sum(1 for term in evidence_terms if term in text) == 0


def normalize_vector(raw_vec: Sequence[float], dimension: int, backend_name: str) -> List[float]:
    arr = np.asarray(raw_vec, dtype=np.float32)
    if arr.ndim != 1:
        raise ValueError(f"{backend_name} returned a non-1D embedding")
    if arr.shape[0] != dimension:
        raise ValueError(f"{backend_name} returned dim={arr.shape[0]}, expected dim={dimension}")
    arr = arr / (np.linalg.norm(arr) + 1e-12)
    return arr.tolist()


def extract_embedding_from_response(payload: Dict[str, Any]) -> List[float]:
    output = payload.get("output") or {}
    embeddings = output.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, dict) and "embedding" in first:
            return first["embedding"]
        if isinstance(first, list):
            return first
    embedding = output.get("embedding")
    if isinstance(embedding, list):
        return embedding
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and "embedding" in first:
            return first["embedding"]
    raise ValueError(f"Cannot locate embedding in response keys={list(payload.keys())}")


def image_to_data_url(path: Path, max_bytes: int = 4_800_000) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    raw = path.read_bytes()
    if len(raw) <= max_bytes:
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    if Image is None:
        raise RuntimeError(f"Image {path} exceeds {max_bytes} bytes and Pillow is not installed")

    with Image.open(path) as img:
        img = img.convert("RGB")
        max_side = 1800
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        quality = 88
        while quality >= 55:
            from io import BytesIO

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                return f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"
            quality -= 8
    raise RuntimeError(f"Unable to compress image {path} below {max_bytes} bytes")


def markdown_image_refs(text: str, base_dir: Path) -> List[Tuple[str, Path, int]]:
    refs: List[Tuple[str, Path, int]] = []
    for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text):
        alt = match.group(1).strip()
        raw = match.group(2).strip().strip('"').strip("'")
        if re.match(r"^https?://", raw, re.I):
            continue
        img_path = (base_dir / raw).resolve()
        if img_path.exists() and img_path.suffix.lower() in IMAGE_EXTENSIONS:
            refs.append((alt, img_path, match.start()))
    return refs


def extract_markdown_tables(text: str) -> List[Tuple[str, int]]:
    tables: List[Tuple[str, int]] = []
    lines = text.splitlines()
    cursor = 0
    start_line: Optional[int] = None
    block: List[str] = []
    block_start_offset = 0

    for line in lines + [""]:
        is_table_line = "|" in line and len(line.strip("| ").split("|")) >= 2
        if is_table_line:
            if start_line is None:
                start_line = 1
                block_start_offset = cursor
            block.append(line)
        else:
            if block and len(block) >= 2:
                tables.append(("\n".join(block), block_start_offset))
            block = []
            start_line = None
        cursor += len(line) + 1
    return tables


def iter_docx_blocks(doc: Any) -> Iterable[Any]:
    try:
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except Exception:
        return []

    blocks: List[Any] = []
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            blocks.append(Paragraph(child, doc))
        elif isinstance(child, CT_Tbl):
            blocks.append(Table(child, doc))
    return blocks


def docx_heading_level(para: Any) -> Optional[int]:
    style_name = str(getattr(getattr(para, "style", None), "name", "") or "").strip()
    lowered = style_name.lower()
    match = re.search(r"\bheading\s*([1-6])\b", lowered)
    if not match:
        match = re.search(r"标题\s*([1-6])", style_name)
    if match:
        return int(match.group(1))
    if lowered in {"title", "标题"}:
        return 1
    try:
        outline = para.style.element.pPr.outlineLvl
        if outline is not None and outline.val is not None:
            return min(int(outline.val) + 1, 6)
    except Exception:
        pass
    return None


def docx_table_to_markdown(table: Any) -> str:
    rows: List[List[str]] = []
    for row in table.rows:
        cells = [clean_text(cell.text).replace("\n", " ") for cell in row.cells]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(row) + " |" for row in normalized]
    if len(lines) == 1:
        return lines[0]
    separator = "| " + " | ".join(["---"] * width) + " |"
    return "\n".join([lines[0], separator, *lines[1:]])


def pdf_page_structured_text(page: Any) -> str:
    try:
        data = page.get_text("dict") or {}
    except Exception:
        return clean_text(page.get_text("text") or "")

    rows: List[Tuple[str, float, bool]] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = clean_text("".join(str(span.get("text", "")) for span in spans))
            if not text:
                continue
            sizes = [float(span.get("size", 0) or 0) for span in spans if span.get("text")]
            fonts = " ".join(str(span.get("font", "")) for span in spans).lower()
            rows.append((text, max(sizes) if sizes else 0.0, any(key in fonts for key in ("bold", "black", "heavy", "semibold"))))

    if not rows:
        return clean_text(page.get_text("text") or "")

    sizes = [size for text, size, _ in rows if size > 0 and len(text) >= 8]
    body_size = float(np.median(sizes)) if sizes else 10.0
    out: List[str] = []
    for text, size, bold in rows:
        explicit_heading = heading_from_line(text)
        likely_visual_heading = (
            len(text) <= 110
            and not re.search(r"[。！？；.!?;]$", text)
            and (
                size >= body_size * 1.18
                or (bold and size >= body_size * 1.05)
            )
        )
        if explicit_heading:
            level, title = explicit_heading
            out.append("#" * level + " " + title)
        elif likely_visual_heading:
            level = 2 if size >= body_size * 1.35 else 3
            out.append("#" * level + " " + text)
        else:
            out.append(text)
    return clean_text("\n\n".join(out))


def iter_supported_files(input_dir: Path) -> List[Path]:
    if input_dir.is_file():
        return [input_dir] if input_dir.suffix.lower() in SUPPORTED_EXTENSIONS else []
    files = [
        p for p in input_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and ".cloud_ingest_assets" not in p.parts
    ]
    return sorted(files)


def runtime_log(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    with CLOUD_RUNTIME_LOCK:
        logs = list(CLOUD_RUNTIME.get("logs", []))
        logs.append(line)
        CLOUD_RUNTIME["logs"] = logs[-MAX_RUNTIME_LOGS:]
        CLOUD_RUNTIME["updated_at"] = now_iso()


def runtime_snapshot() -> Tuple[str, str]:
    with CLOUD_RUNTIME_LOCK:
        logs = "\n".join(CLOUD_RUNTIME.get("logs", [])[-300:])
        config = dict(CLOUD_RUNTIME.get("config", {}))
        stats = dict(CLOUD_RUNTIME.get("stats", {}))
        active = bool(CLOUD_RUNTIME.get("active", False))
        paused = bool(CLOUD_RUNTIME.get("paused", False))
        result = str(CLOUD_RUNTIME.get("result", ""))

    status_lines = [
        f"状态: {'暂停中' if active and paused else ('运行中' if active else '空闲')}",
        f"Milvus: {config.get('milvus_uri', '-')}",
        f"Database: {config.get('db_name', '-')}",
        f"Collection: {config.get('collection', '-')}",
        f"project_tag: {config.get('project_tag', '-')}",
        f"维度: {config.get('dimension', '-')}",
        f"已处理文件: {stats.get('files_ingested', 0)}/{stats.get('files_seen', 0)}",
        f"已创建 chunks: {stats.get('chunks_created', 0)}",
        f"已入库 chunks: {stats.get('chunks_inserted', 0)}",
        f"跳过 chunks: {stats.get('chunks_skipped', 0)}",
        f"文本 embedding 调用: {stats.get('text_embedding_calls', 0)}",
        f"视觉 embedding 调用: {stats.get('visual_embedding_calls', 0)}",
        f"视觉 caption 调用: {stats.get('caption_calls', 0)}",
    ]
    errors = stats.get("errors") or []
    if errors:
        status_lines.append(f"错误数: {len(errors)}")
    if result:
        status_lines.append(result)
    return "\n".join(status_lines), logs


def pause_runtime() -> Tuple[str, str]:
    with CLOUD_RUNTIME_LOCK:
        if CLOUD_RUNTIME.get("active"):
            CLOUD_RUNTIME["paused"] = True
            CLOUD_RUNTIME["updated_at"] = now_iso()
    runtime_log("Pause requested")
    return runtime_snapshot()


def resume_runtime() -> Tuple[str, str]:
    with CLOUD_RUNTIME_LOCK:
        if CLOUD_RUNTIME.get("active"):
            CLOUD_RUNTIME["paused"] = False
            CLOUD_RUNTIME["updated_at"] = now_iso()
    runtime_log("Resume requested")
    return runtime_snapshot()


def stats_to_dict(stats: IngestStats) -> Dict[str, Any]:
    return {
        "files_seen": stats.files_seen,
        "files_ingested": stats.files_ingested,
        "chunks_created": stats.chunks_created,
        "chunks_inserted": stats.chunks_inserted,
        "chunks_skipped": stats.chunks_skipped,
        "text_embedding_calls": stats.text_embedding_calls,
        "visual_embedding_calls": stats.visual_embedding_calls,
        "caption_calls": stats.caption_calls,
        "errors": stats.errors,
    }


def collection_status_snapshot(
    uri: str,
    db_name: str,
    token: str = "",
    user: str = "",
    password: str = "",
) -> Dict[str, Any]:
    if connections is None:
        return {"error": "pymilvus is not installed", "collections": [], "total_collections": 0}
    alias = f"cloud_manage_{os.getpid()}_{re.sub(r'[^A-Za-z0-9_]', '_', db_name or DEFAULT_DB)}"
    try:
        if connections.has_connection(alias):
            connections.disconnect(alias)
        kwargs: Dict[str, Any] = {"alias": alias, "uri": uri, "db_name": db_name or DEFAULT_DB}
        if token:
            kwargs["token"] = token
        if user:
            kwargs["user"] = user
        if password:
            kwargs["password"] = password
        connections.connect(**kwargs)
        names = sorted(utility.list_collections(using=alias))
        rows = []
        for name in names:
            try:
                coll = Collection(name, using=alias)
                vector_dim = None
                for field in coll.schema.fields:
                    if field.name == "vector":
                        vector_dim = field.params.get("dim")
                        break
                rows.append({
                    "name": name,
                    "entities": int(coll.num_entities),
                    "vector_dim": vector_dim,
                    "managed_role": name in ROLE_REGISTRY,
                    "graph_sidecar": name.endswith((
                        f"_{GRAPH_ENTITY_COLLECTION}",
                        f"_{GRAPH_RELATION_COLLECTION}",
                        f"_{GRAPH_PASSAGE_COLLECTION}",
                    )),
                })
            except Exception as exc:
                rows.append({"name": name, "error": str(exc)})
        return {
            "database": db_name or DEFAULT_DB,
            "updated_at": now_iso(),
            "total_collections": len(names),
            "collections": rows,
        }
    except Exception as exc:
        return {
            "database": db_name or DEFAULT_DB,
            "updated_at": now_iso(),
            "error": str(exc),
            "total_collections": 0,
            "collections": [],
        }


def list_database_snapshot(
    uri: str,
    token: str = "",
    user: str = "",
    password: str = "",
) -> Dict[str, Any]:
    if MilvusClient is None:
        return {
            "updated_at": now_iso(),
            "databases": [DEFAULT_DB],
            "error": "pymilvus is not installed",
        }
    client = None
    try:
        kwargs: Dict[str, Any] = {"uri": uri or DEFAULT_MILVUS_URI}
        if token:
            kwargs["token"] = token
        if user:
            kwargs["user"] = user
        if password:
            kwargs["password"] = password
        client = MilvusClient(**kwargs)
        if not hasattr(client, "list_databases"):
            return {
                "updated_at": now_iso(),
                "databases": [DEFAULT_DB],
                "error": "current pymilvus does not support list_databases",
            }
        dbs = sorted({str(db) for db in client.list_databases() if str(db).strip()})
        if not dbs:
            dbs = [DEFAULT_DB]
        elif DEFAULT_DB not in dbs:
            dbs.insert(0, DEFAULT_DB)
        return {
            "updated_at": now_iso(),
            "databases": dbs,
            "error": "",
        }
    except Exception as exc:
        return {
            "updated_at": now_iso(),
            "databases": [DEFAULT_DB],
            "error": str(exc),
        }
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass


def list_database_choices(
    uri: str,
    token: str = "",
    user: str = "",
    password: str = "",
) -> List[str]:
    return list_database_snapshot(uri, token, user, password).get("databases", [DEFAULT_DB])


def list_collection_choices(
    uri: str,
    db_name: str,
    token: str = "",
    user: str = "",
    password: str = "",
) -> List[str]:
    snapshot = collection_status_snapshot(uri, db_name, token, user, password)
    rows = snapshot.get("collections", [])
    if isinstance(rows, list) and rows:
        return [
            str(row.get("name"))
            for row in rows
            if isinstance(row, dict)
            and row.get("name")
            and not row.get("graph_sidecar")
        ]
    if snapshot.get("error"):
        return list(ROLE_REGISTRY.keys())
    return []


def create_collection_for_ui(
    uri: str,
    db_name: str,
    collection_name: str,
    dimension: int,
    token: str = "",
    user: str = "",
    password: str = "",
) -> str:
    name = (collection_name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        return "Collection 名称仅允许字母、数字、下划线"
    try:
        MilvusWriter(uri, db_name or DEFAULT_DB, name, int(dimension), token, user, password, reset=False)
        return f"已创建或确认存在: {db_name or DEFAULT_DB}.{name}"
    except Exception as exc:
        return f"创建失败: {exc}"


def drop_collection_for_ui(
    uri: str,
    db_name: str,
    collection_name: str,
    token: str = "",
    user: str = "",
    password: str = "",
) -> str:
    name = (collection_name or "").strip()
    if not name:
        return "请选择 Collection"
    if connections is None:
        return "pymilvus 未安装"
    alias = f"cloud_drop_{os.getpid()}_{re.sub(r'[^A-Za-z0-9_]', '_', db_name or DEFAULT_DB)}"
    try:
        kwargs: Dict[str, Any] = {"alias": alias, "uri": uri, "db_name": db_name or DEFAULT_DB}
        if token:
            kwargs["token"] = token
        if user:
            kwargs["user"] = user
        if password:
            kwargs["password"] = password
        connections.connect(**kwargs)
        if not utility.has_collection(name, using=alias):
            return f"Collection 不存在: {name}"
        try:
            Collection(name, using=alias).release()
        except Exception:
            pass
        utility.drop_collection(name, using=alias)
        return f"已删除: {db_name or DEFAULT_DB}.{name}"
    except Exception as exc:
        return f"删除失败: {exc}"


class BailianClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_DASHSCOPE_BASE_URL,
        dimension: int = DEFAULT_DIM,
        text_model: str = TEXT_EMBED_MODEL,
        multimodal_model: str = MULTIMODAL_EMBED_MODEL,
        caption_model: str = DEFAULT_CAPTION_MODEL,
        timeout: int = 90,
        max_retries: int = 3,
    ):
        api_key = validate_dashscope_api_key(api_key)
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.dimension = dimension
        self.text_model = text_model
        self.multimodal_model = multimodal_model
        self.caption_model = caption_model
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        last_error = ""
        for attempt in range(self.max_retries):
            try:
                resp = self.session.post(url, headers=self.headers, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
                last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                if resp.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(2 ** attempt)
                    continue
                break
            except Exception as exc:
                last_error = str(exc)
                time.sleep(2 ** attempt)
        raise RuntimeError(last_error or "request failed")

    def embed_texts(self, texts: List[str], text_type: str = "document") -> List[List[float]]:
        url = self.base_url + TEXT_EMBED_ENDPOINT
        results: List[List[float]] = []
        for start in range(0, len(texts), 10):
            batch = [text[:12000] for text in texts[start:start + 10]]
            payload = {
                "model": self.text_model,
                "input": {"texts": batch},
                "parameters": {
                    "dimension": self.dimension,
                    "text_type": text_type,
                },
            }
            data = self._post_json(url, payload)
            embeddings = data.get("output", {}).get("embeddings", [])
            if not embeddings:
                raise RuntimeError(f"text embedding response has no embeddings: {data}")
            embeddings = sorted(
                embeddings,
                key=lambda item: int(item.get("text_index", item.get("index", 0))) if isinstance(item, dict) else 0,
            )
            for item in embeddings:
                vec = item["embedding"] if isinstance(item, dict) else item
                results.append(normalize_vector(vec, self.dimension, self.text_model))
        return results

    def preflight(self) -> None:
        self.embed_texts(["百炼 API connectivity check"], text_type="document")

    def embed_image_fusion(self, image_data_url: str, context_text: str = "") -> List[float]:
        url = self.base_url + MM_EMBED_ENDPOINT
        contents: List[Dict[str, str]] = []
        if context_text.strip():
            contents.append({"text": context_text[:4000]})
        contents.append({"image": image_data_url})
        payload = {
            "model": self.multimodal_model,
            "input": {"contents": contents},
            "parameters": {
                "dimension": self.dimension,
                "enable_fusion": True,
            },
        }
        try:
            data = self._post_json(url, payload)
        except RuntimeError:
            fallback_payload = {
                "model": self.multimodal_model,
                "input": {"contents": [{"text": context_text[:1000] or "visual content", "image": image_data_url}]},
                "parameters": {"dimension": self.dimension},
            }
            data = self._post_json(url, fallback_payload)
        return normalize_vector(extract_embedding_from_response(data), self.dimension, self.multimodal_model)

    def caption_image(self, image_data_url: str, context_text: str = "") -> str:
        url = self.base_url + CHAT_COMPLETIONS_ENDPOINT
        prompt = (
            "请用中文提取这张项目底稿图片/图表/页面中的可检索证据。"
            "重点写出公司、产品、客户、供应商、财务指标、表格口径、风险事项、时间和金额。"
            "如果是图表或表格，请保留关键字段和数值。输出控制在300字以内。"
        )
        if context_text.strip():
            prompt += "\n邻近文本:\n" + context_text[:1200]
        payload = {
            "model": self.caption_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 500,
        }
        data = self._post_json(url, payload)
        choices = data.get("choices") or []
        if not choices:
            return ""
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item))
            return clean_text("\n".join(parts))
        return clean_text(str(content))


class MilvusWriter:
    def __init__(
        self,
        uri: str,
        db_name: str,
        collection_name: str,
        dimension: int,
        token: str = "",
        user: str = "",
        password: str = "",
        reset: bool = False,
    ):
        if connections is None:
            raise RuntimeError("pymilvus is required. Install with: pip install pymilvus")
        self.uri = uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.dimension = dimension
        self.token = token
        self.user = user
        self.password = password
        self.alias = f"cloud_ingest_{os.getpid()}_{re.sub(r'[^A-Za-z0-9_]', '_', db_name)}"
        self._connect()
        self.collection = self._ensure_collection(reset=reset)

    def _connect(self) -> None:
        kwargs: Dict[str, Any] = {"alias": self.alias, "uri": self.uri, "db_name": self.db_name}
        if self.token:
            kwargs["token"] = self.token
        if self.user:
            kwargs["user"] = self.user
        if self.password:
            kwargs["password"] = self.password
        connections.connect(**kwargs)

    def _ensure_collection(self, reset: bool) -> Collection:
        if reset and utility.has_collection(self.collection_name, using=self.alias):
            try:
                Collection(self.collection_name, using=self.alias).release()
            except Exception:
                pass
            utility.drop_collection(self.collection_name, using=self.alias)

        if not utility.has_collection(self.collection_name, using=self.alias):
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.dimension),
                FieldSchema(name="project_tag", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="metadata", dtype=DataType.JSON),
            ]
            schema = CollectionSchema(fields, description=ROLE_REGISTRY.get(self.collection_name, self.collection_name))
            collection = Collection(self.collection_name, schema, using=self.alias)
            collection.create_index(field_name="vector", index_params=INDEX_PARAMS)
            collection.create_index(field_name="project_tag", index_params={"index_type": "INVERTED"})
        collection = Collection(self.collection_name, using=self.alias)
        self._assert_dimension(collection)
        collection.load()
        return collection

    def _assert_dimension(self, collection: Collection) -> None:
        for field in collection.schema.fields:
            if field.name == "vector":
                dim = int(field.params.get("dim", 0))
                if dim != self.dimension:
                    raise ValueError(
                        f"Collection {self.db_name}.{self.collection_name} vector dim={dim}, "
                        f"but script dimension={self.dimension}"
                    )

    def delete_existing_chunks(self, chunks: List[ChunkItem]) -> int:
        chunk_uids = {str(chunk.meta.get("chunk_uid") or "") for chunk in chunks if chunk.meta.get("chunk_uid")}
        if not chunk_uids:
            return 0
        project_tags = {str(chunk.meta.get("project_tag") or "") for chunk in chunks if chunk.meta.get("project_tag")}
        source_paths = {str(chunk.meta.get("source_path") or "") for chunk in chunks if chunk.meta.get("source_path")}
        if not project_tags:
            return 0

        ids_to_delete: List[int] = []
        for project_tag in project_tags:
            escaped_tag = project_tag.replace("\\", "\\\\").replace('"', '\\"')
            try:
                rows = self.collection.query(
                    expr=f'project_tag == "{escaped_tag}"',
                    output_fields=["id", "metadata"],
                    limit=16384,
                )
            except Exception:
                continue
            for row in rows:
                meta = row.get("metadata") or {}
                if not isinstance(meta, dict):
                    continue
                if source_paths and str(meta.get("source_path") or "") not in source_paths:
                    continue
                if str(meta.get("chunk_uid") or "") in chunk_uids and row.get("id") is not None:
                    ids_to_delete.append(int(row["id"]))

        if not ids_to_delete:
            return 0
        for start in range(0, len(ids_to_delete), 512):
            batch = ids_to_delete[start:start + 512]
            self.collection.delete(expr=f"id in {batch}")
        self.collection.flush()
        return len(ids_to_delete)

    def insert(self, chunks: List[ChunkItem], batch_size: int = 128) -> int:
        inserted = 0
        ready = [chunk for chunk in chunks if chunk.vector is not None]
        for start in range(0, len(ready), batch_size):
            batch = ready[start:start + batch_size]
            vectors = [chunk.vector for chunk in batch]
            tags = [str(chunk.meta.get("project_tag") or "") for chunk in batch]
            metas = [chunk.meta for chunk in batch]
            self.collection.insert([vectors, tags, metas])
            inserted += len(batch)
        if inserted:
            self.collection.flush()
        return inserted


class GraphPassageWriter:
    def __init__(
        self,
        uri: str,
        db_name: str,
        prefix: str,
        dimension: int,
        token: str = "",
        user: str = "",
        password: str = "",
        reset: bool = False,
    ):
        if MilvusClient is None:
            raise RuntimeError("pymilvus is required. Install with: pip install pymilvus")
        self.uri = uri
        self.db_name = db_name
        self.prefix = normalize_graph_prefix(prefix)
        self.dimension = dimension
        kwargs: Dict[str, Any] = {"uri": uri, "db_name": db_name}
        if token:
            kwargs["token"] = token
        if user:
            kwargs["user"] = user
        if password:
            kwargs["password"] = password
        self.client = MilvusClient(**kwargs)
        self.names = graph_collection_names(self.prefix)
        self._ensure(reset=reset)

    def _ensure(self, reset: bool) -> None:
        for name in self.names.values():
            if self.client.has_collection(name):
                if reset:
                    self.client.drop_collection(name)
                else:
                    continue
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)
            schema.add_field(field_name="id", datatype=DataType.VARCHAR, max_length=64, is_primary=True)
            schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=self.dimension)
            schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_type="HNSW",
                metric_type="IP",
                params=GRAPH_INDEX_PARAMS,
            )
            self.client.create_collection(
                collection_name=name,
                schema=schema,
                index_params=index_params,
                consistency_level="Bounded",
            )

    @staticmethod
    def _entity_name(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n，。；：:、,.()（）[]【】")

    def _entity_id(self, meta: Dict[str, Any], entity_type: str, name: str) -> str:
        identity = {
            "prefix": self.prefix,
            "collection": meta.get("collection"),
            "project_tag": meta.get("project_tag"),
            "type": entity_type,
            "name": name,
        }
        return sha1_text(json.dumps(identity, ensure_ascii=False, sort_keys=True))

    def _relation_id(self, meta: Dict[str, Any], subject_id: str, predicate: str, object_id: str) -> str:
        identity = {
            "prefix": self.prefix,
            "collection": meta.get("collection"),
            "project_tag": meta.get("project_tag"),
            "chunk_uid": meta.get("chunk_uid"),
            "subject_id": subject_id,
            "predicate": predicate,
            "object_id": object_id,
        }
        return sha1_text(json.dumps(identity, ensure_ascii=False, sort_keys=True))

    def _infer_company_names(self, meta: Dict[str, Any], text: str) -> List[str]:
        haystack = " ".join([
            str(meta.get("source") or ""),
            str(meta.get("source_path") or ""),
            text,
        ])
        names = set()
        if re.search(r"\bDajin\b|大金", haystack, re.I):
            names.add("大金重工")
        for match in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9（）()]{2,40}(?:股份有限公司|有限公司|集团|重工|公司)", haystack):
            name = self._entity_name(match.group(0))
            if name and name not in {"公司", "集团公司"}:
                names.add(name[:80])
        if not names and re.search(r"\b公司\b", text):
            stem = Path(str(meta.get("source") or "")).stem
            if stem:
                names.add(self._entity_name(stem)[:80])
        return sorted(names)

    def _extract_entities_for_chunk(self, chunk: ChunkItem) -> Dict[str, Dict[str, Any]]:
        if chunk.vector is None:
            return {}
        meta = chunk.meta
        text = clean_text(" ".join([
            str(meta.get("source") or ""),
            str(meta.get("section_path") or ""),
            str(meta.get("text") or chunk.text or ""),
            str(meta.get("visual_caption") or ""),
        ]))
        entities: Dict[str, Dict[str, Any]] = {}

        def add(entity_type: str, name: str, evidence: str = "") -> str:
            clean_name = self._entity_name(name)
            if not clean_name or len(clean_name) < 2:
                return ""
            entity_id = self._entity_id(meta, entity_type, clean_name)
            if entity_id not in entities:
                entities[entity_id] = {
                    "id": entity_id,
                    "text": clean_name,
                    "vector": chunk.vector,
                    "name": clean_name,
                    "entity_type": entity_type,
                    "project_tag": meta.get("project_tag"),
                    "collection": meta.get("collection"),
                    "source": meta.get("source"),
                    "source_path": meta.get("source_path"),
                    "page": meta.get("page"),
                    "section_path": meta.get("section_path"),
                    "chunk_uid": meta.get("chunk_uid"),
                    "citation": meta.get("citation"),
                    "evidence": evidence[:500] if evidence else str(meta.get("text", ""))[:500],
                }
            return entity_id

        for company in self._infer_company_names(meta, text):
            add("company", company)

        enabled_entity_types = graph_extraction_entity_types(meta)
        for entity_type, patterns in GRAPH_ENTITY_RULES.items():
            if entity_type not in enabled_entity_types:
                continue
            for pat in patterns:
                for match in re.finditer(pat, text, flags=re.IGNORECASE):
                    add(entity_type, match.group(0), evidence=text[max(0, match.start() - 80):match.end() + 80])

        return entities

    def _build_relations_for_chunk(
        self,
        chunk: ChunkItem,
        entities: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        if chunk.vector is None or not entities:
            return {}
        meta = chunk.meta
        text = clean_text(str(meta.get("text") or chunk.text or ""))
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for row in entities.values():
            by_type.setdefault(str(row.get("entity_type")), []).append(row)

        companies = by_type.get("company") or []
        subjects = companies or by_type.get("product") or []
        relations: Dict[str, Dict[str, Any]] = {}

        def add(subject: Dict[str, Any], predicate: str, obj: Dict[str, Any]) -> None:
            subject_id = str(subject.get("id"))
            object_id = str(obj.get("id"))
            rel_id = self._relation_id(meta, subject_id, predicate, object_id)
            relations[rel_id] = {
                "id": rel_id,
                "text": f"{subject.get('name')} - {predicate} - {obj.get('name')}",
                "vector": chunk.vector,
                "subject_id": subject_id,
                "subject_name": subject.get("name"),
                "subject_type": subject.get("entity_type"),
                "predicate": predicate,
                "object_id": object_id,
                "object_name": obj.get("name"),
                "object_type": obj.get("entity_type"),
                "project_tag": meta.get("project_tag"),
                "collection": meta.get("collection"),
                "source": meta.get("source"),
                "source_path": meta.get("source_path"),
                "page": meta.get("page"),
                "section_path": meta.get("section_path"),
                "chunk_uid": meta.get("chunk_uid"),
                "citation": meta.get("citation"),
                "evidence": text[:800],
            }

        for subject in subjects:
            for product in by_type.get("product", []):
                if subject.get("id") != product.get("id"):
                    add(subject, "涉及产品", product)
            for region in by_type.get("region", []):
                predicate = "位于" if re.search(r"基地|工厂|港|泊位", str(subject.get("name"))) else "覆盖区域"
                add(subject, predicate, region)
            for metric in by_type.get("capacity_metric", []):
                predicate = "具备产能" if re.search(r"产能|万吨|GW|MW|万千瓦|吨", str(metric.get("name"))) else "具备指标"
                add(subject, predicate, metric)
            for cert in by_type.get("certification", []):
                add(subject, "获得认证", cert)
            for capability in by_type.get("capability", []):
                add(subject, "具备能力", capability)
            for metric in by_type.get("financial_metric", []):
                add(subject, "具备财务指标", metric)
            for clause in by_type.get("legal_clause", []):
                add(subject, "包含条款", clause)
            for regulation in by_type.get("regulation", []):
                add(subject, "适用法规", regulation)
            for risk in by_type.get("risk", []):
                add(subject, "存在风险", risk)
            for date in by_type.get("date", []):
                add(subject, "发生时间", date)
            for stakeholder in by_type.get("stakeholder", []):
                add(subject, "相关方", stakeholder)

        return relations

    def upsert_passages(self, chunks: List[ChunkItem]) -> Dict[str, int]:
        rows = []
        entity_rows: Dict[str, Dict[str, Any]] = {}
        relation_rows: Dict[str, Dict[str, Any]] = {}
        for chunk in chunks:
            if chunk.vector is None:
                continue
            meta = chunk.meta
            chunk_entities = self._extract_entities_for_chunk(chunk)
            chunk_relations = self._build_relations_for_chunk(chunk, chunk_entities)
            entity_rows.update(chunk_entities)
            relation_rows.update(chunk_relations)
            rows.append({
                "id": str(meta.get("chunk_uid")),
                "text": str(meta.get("text", ""))[:65535],
                "vector": chunk.vector,
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
                "entity_ids": list(chunk_entities.keys()),
                "relation_ids": list(chunk_relations.keys()),
            })
        if not rows:
            return {"passages": 0, "entities": 0, "relations": 0}
        if hasattr(self.client, "upsert"):
            self.client.upsert(collection_name=self.names["passages"], data=rows)
            if entity_rows:
                self.client.upsert(collection_name=self.names["entities"], data=list(entity_rows.values()))
            if relation_rows:
                self.client.upsert(collection_name=self.names["relations"], data=list(relation_rows.values()))
        else:
            self.client.insert(collection_name=self.names["passages"], data=rows)
            if entity_rows:
                self.client.insert(collection_name=self.names["entities"], data=list(entity_rows.values()))
            if relation_rows:
                self.client.insert(collection_name=self.names["relations"], data=list(relation_rows.values()))
        if hasattr(self.client, "flush"):
            self.client.flush(collection_name=self.names["passages"])
            if entity_rows:
                self.client.flush(collection_name=self.names["entities"])
            if relation_rows:
                self.client.flush(collection_name=self.names["relations"])
        return {"passages": len(rows), "entities": len(entity_rows), "relations": len(relation_rows)}


def normalize_graph_prefix(prefix: str) -> str:
    raw = (prefix or "siq_project").strip()
    raw = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    return raw or "siq_project"


def default_graph_prefix(collection: str) -> str:
    return normalize_graph_prefix(collection or DEFAULT_COLLECTION)


def graph_collection_names(prefix: str) -> Dict[str, str]:
    prefix = normalize_graph_prefix(prefix)
    return {
        "entities": f"{prefix}_{GRAPH_ENTITY_COLLECTION}",
        "relations": f"{prefix}_{GRAPH_RELATION_COLLECTION}",
        "passages": f"{prefix}_{GRAPH_PASSAGE_COLLECTION}",
    }


def graph_extraction_entity_types(meta: Dict[str, Any]) -> set[str]:
    doc_type = str(meta.get("doc_type") or "default")
    return GRAPH_PROFILE_ENTITY_TYPES.get(doc_type, GRAPH_PROFILE_ENTITY_TYPES["default"])


class CloudBailianIngestor:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.dimension = int(args.dimension)
        self.assets_dir = Path(args.assets_dir).resolve()
        self.reports_dir = Path(args.reports_dir).resolve()
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.bailian: Optional[BailianClient] = None
        if not args.dry_run:
            self.bailian = BailianClient(
                api_key=args.dashscope_api_key or os.getenv("DASHSCOPE_API_KEY", ""),
                base_url=args.dashscope_base_url,
                dimension=self.dimension,
                text_model=args.text_embedding_model,
                multimodal_model=args.multimodal_embedding_model,
                caption_model=args.caption_model,
                timeout=args.timeout,
                max_retries=args.max_retries,
            )
        self.stats = IngestStats()

    def log(self, message: str) -> None:
        runtime_log(message)

    def wait_if_paused(self, where: str = "") -> None:
        announced = False
        while True:
            with CLOUD_RUNTIME_LOCK:
                paused = bool(CLOUD_RUNTIME.get("paused", False))
                active = bool(CLOUD_RUNTIME.get("active", False))
            if not paused or not active:
                if announced:
                    self.log("  resumed")
                return
            if not announced:
                suffix = f" before {where}" if where else ""
                self.log(f"  paused{suffix}")
                announced = True
            time.sleep(1)

    def parse_file(self, path: Path) -> List[ChunkItem]:
        suffix = path.suffix.lower()
        if suffix in PDF_EXTENSIONS:
            return self.parse_pdf(path)
        if suffix in {".md", ".markdown"}:
            return self.parse_markdown(path)
        if suffix == ".txt":
            return self.parse_text_file(path)
        if suffix == ".docx":
            return self.parse_docx(path)
        if suffix in IMAGE_EXTENSIONS:
            return self.parse_image(path)
        return []

    def base_meta(
        self,
        path: Path,
        chunk_type: str,
        modality: str,
        text: str,
        file_format: str,
        page: Optional[int] = None,
        section_path: str = "",
        chunk_index: Optional[int] = None,
        total_chunks: Optional[int] = None,
        parser: str = "cloud_bailian_lightweight",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        doc_type = infer_doc_type(str(path), file_format)
        meta: Dict[str, Any] = {
            "type": chunk_type,
            "modality": modality,
            "schema_version": METADATA_SCHEMA_VERSION,
            "source": path.name,
            "source_path": str(path.resolve()),
            "file_stem": path.stem,
            "file_ext": path.suffix.lower().lstrip("."),
            "format": file_format,
            "doc_type": doc_type,
            "section_path": section_path,
            "page": page,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "text": clean_text(text),
            "parser": parser,
        }
        if extra:
            meta.update(extra)
        return self.finalize_metadata(meta)

    def finalize_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        text = clean_text(str(meta.get("text", "")))
        doc_type = str(meta.get("doc_type") or infer_doc_type(meta.get("source_path", ""), meta.get("format", "")))
        meta.update({
            "schema_version": METADATA_SCHEMA_VERSION,
            "project_tag": self.args.project_tag,
            "collection": self.args.collection,
            "collection_role": collection_role(self.args.collection),
            "agent_id": agent_id(self.args.collection),
            "doc_type": doc_type,
            "evidence_level": meta.get("evidence_level") or EVIDENCE_LEVEL_BY_DOC_TYPE.get(doc_type, "source_doc"),
            "language": meta.get("language") or infer_language(text),
            "ingest_version": PIPELINE_VERSION,
            "embedding_backend": "aliyun_bailian",
            "embedding_model": meta.get("embedding_model") or TEXT_EMBED_MODEL,
            "vector_dim": self.dimension,
            "text": text,
            "text_len": len(text),
            "created_at": now_iso(),
        })
        identity = {
            "collection": self.args.collection,
            "project_tag": self.args.project_tag,
            "source_path": meta.get("source_path"),
            "page": meta.get("page"),
            "chunk_index": meta.get("chunk_index"),
            "type": meta.get("type"),
            "image_path": meta.get("image_path"),
            "text_sha1": sha1_text(text),
        }
        meta["chunk_uid"] = sha1_text(json.dumps(identity, ensure_ascii=False, sort_keys=True))
        meta["text_sha1"] = identity["text_sha1"]
        meta["citation"] = build_citation(meta)
        return meta

    def text_chunks_from_text(
        self,
        text: str,
        path: Path,
        file_format: str,
        page: Optional[int] = None,
        parser: str = "cloud_bailian_lightweight",
    ) -> List[ChunkItem]:
        clean = clean_text(text)
        if not clean:
            return []
        doc_type = infer_doc_type(str(path), file_format)
        policy_key = policy_key_for_doc_type(file_format, doc_type)
        chunks = smart_chunk(clean, policy_key)
        offsets = line_offsets(clean)
        total = len(chunks)
        items: List[ChunkItem] = []
        for idx, (chunk, start, end) in enumerate(chunks, start=1):
            section = section_path_for_offset(offsets, start)
            page_value = page or page_for_offset(offsets, start)
            parent_identity = {
                "source_path": str(path.resolve()),
                "section_path": section or "(root)",
                "format": file_format,
            }
            meta = self.base_meta(
                path=path,
                chunk_type="text_chunk",
                modality="text",
                text=chunk,
                file_format=file_format,
                page=page_value,
                section_path=section,
                chunk_index=idx,
                total_chunks=total,
                parser=parser,
                extra={
                    "char_start": start,
                    "char_end": end,
                    "parent_id": sha1_text(json.dumps(parent_identity, ensure_ascii=False, sort_keys=True)),
                    "parent_type": "section",
                    "neighbor_prev_index": idx - 1 if idx > 1 else None,
                    "neighbor_next_index": idx + 1 if idx < total else None,
                },
            )
            items.append(ChunkItem(text=chunk, meta=meta))
        return items

    def parse_text_file(self, path: Path) -> List[ChunkItem]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return self.text_chunks_from_text(text, path, "txt")

    def parse_docx(self, path: Path) -> List[ChunkItem]:
        if Document is None:
            raise RuntimeError("python-docx is required for .docx. Install with: pip install python-docx")
        doc = Document(str(path))
        parts: List[str] = []
        table_refs: List[Tuple[str, int]] = []
        cursor = 0

        def append_part(value: str) -> int:
            nonlocal cursor
            if parts:
                cursor += 2
            start = cursor
            parts.append(value)
            cursor += len(value)
            return start

        for block in iter_docx_blocks(doc):
            if hasattr(block, "paragraph_format"):
                text = clean_text(getattr(block, "text", ""))
                if not text:
                    continue
                level = docx_heading_level(block)
                if level and not text.startswith("#"):
                    append_part("#" * min(level, 6) + " " + text)
                else:
                    append_part(text)
                continue

            if hasattr(block, "rows"):
                table_text = docx_table_to_markdown(block)
                if table_text:
                    start = append_part(table_text)
                    table_refs.append((table_text, start))

        structured_text = "\n\n".join(parts)
        items = self.text_chunks_from_text(structured_text, path, "docx", parser="cloud_bailian_docx_structured")
        if self.args.enable_table:
            offsets = line_offsets(structured_text)
            for table_index, (table_text, start) in enumerate(table_refs, start=1):
                meta = self.base_meta(
                    path=path,
                    chunk_type="table_chunk",
                    modality="table",
                    text="[table evidence]\n" + clean_text(table_text),
                    file_format="table",
                    section_path=section_path_for_offset(offsets, start),
                    chunk_index=table_index,
                    total_chunks=None,
                    parser="cloud_bailian_docx_table",
                    extra={
                        "table_index": table_index,
                        "table_row_count": len([line for line in table_text.splitlines() if line.strip()]),
                    },
                )
                items.append(ChunkItem(text=meta["text"], meta=meta))
        return items

    def parse_markdown(self, path: Path) -> List[ChunkItem]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        items = self.text_chunks_from_text(text, path, "md")
        offsets = line_offsets(text)

        if self.args.enable_table:
            table_index = 0
            for table_text, start in extract_markdown_tables(text):
                stripped = clean_text(table_text)
                if len(stripped) < 20:
                    continue
                table_index += 1
                section = section_path_for_offset(offsets, start)
                meta = self.base_meta(
                    path=path,
                    chunk_type="table_chunk",
                    modality="table",
                    text="[table evidence]\n" + stripped,
                    file_format="table",
                    page=page_for_offset(offsets, start),
                    section_path=section,
                    chunk_index=table_index,
                    total_chunks=None,
                    parser="cloud_bailian_markdown_table",
                    extra={
                        "table_index": table_index,
                        "table_row_count": len([line for line in stripped.splitlines() if line.strip()]),
                    },
                )
                items.append(ChunkItem(text=meta["text"], meta=meta))

        if self.args.enable_visual:
            for visual_index, (alt, img_path, start) in enumerate(markdown_image_refs(text, path.parent), start=1):
                section = section_path_for_offset(offsets, start)
                context = self.nearby_text(text, start)
                visual_text = clean_text("\n".join([
                    "[visual evidence]",
                    f"alt: {alt}" if alt else "",
                    f"context: {context}" if context else "",
                ]))
                meta = self.base_meta(
                    path=path,
                    chunk_type="visual_chunk",
                    modality="image",
                    text=visual_text or f"Image asset referenced by {path.name}",
                    file_format="visual",
                    page=page_for_offset(offsets, start),
                    section_path=section,
                    chunk_index=visual_index,
                    parser="cloud_bailian_markdown_image",
                    extra={
                        "image_path": str(img_path),
                        "image_source": "markdown_ref",
                        "image_alt": alt,
                        "visual_context": context,
                        "visual_asset_sha1": sha1_file(img_path),
                        "embedding_model": MULTIMODAL_EMBED_MODEL,
                    },
                )
                items.append(ChunkItem(text=meta["text"], meta=meta, image_path=img_path))
        return items

    def parse_pdf(self, path: Path) -> List[ChunkItem]:
        if fitz is None:
            raise RuntimeError("PyMuPDF is required for .pdf. Install with: pip install pymupdf")
        doc = fitz.open(str(path))
        items: List[ChunkItem] = []
        rendered_dir = self.assets_dir / sha1_text(str(path.resolve()))[:12]
        rendered_dir.mkdir(parents=True, exist_ok=True)

        for page_index, page in enumerate(doc, start=1):
            page_text = pdf_page_structured_text(page)
            if page_text:
                page_marked_text = f"[PDF_PAGE: {page_index}]\n\n{page_text}"
                items.extend(self.text_chunks_from_text(
                    page_marked_text,
                    path,
                    "pdf",
                    page=page_index,
                    parser="cloud_bailian_pymupdf_structured_text",
                ))

            if self.args.enable_visual and self.args.enable_pdf_page_visuals:
                if self.args.max_pdf_visual_pages and page_index > self.args.max_pdf_visual_pages:
                    continue
                img_path = rendered_dir / f"{path.stem}_page_{page_index:04d}.jpg"
                if not img_path.exists():
                    matrix = fitz.Matrix(self.args.pdf_render_scale, self.args.pdf_render_scale)
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    pix.save(str(img_path))
                visual_text = clean_text("\n".join([
                    "[pdf page visual evidence]",
                    f"source: {path.name}",
                    f"page: {page_index}",
                    page_text[:1200] if page_text else "",
                ]))
                meta = self.base_meta(
                    path=path,
                    chunk_type="visual_chunk",
                    modality="page_image",
                    text=visual_text or f"PDF page image {path.name} p.{page_index}",
                    file_format="visual",
                    page=page_index,
                    section_path="",
                    chunk_index=page_index,
                    total_chunks=len(doc),
                    parser="cloud_bailian_pymupdf_page_render",
                    extra={
                        "image_path": str(img_path),
                        "image_source": "pdf_page_render",
                        "visual_context": page_text[:1200],
                        "visual_asset_sha1": sha1_file(img_path),
                        "embedding_model": MULTIMODAL_EMBED_MODEL,
                    },
                )
                items.append(ChunkItem(text=meta["text"], meta=meta, image_path=img_path))
        doc.close()
        return items

    def parse_image(self, path: Path) -> List[ChunkItem]:
        text = f"[image evidence]\nsource: {path.name}"
        meta = self.base_meta(
            path=path,
            chunk_type="visual_chunk",
            modality="image",
            text=text,
            file_format="visual",
            parser="cloud_bailian_image_file",
            extra={
                "image_path": str(path.resolve()),
                "image_source": "image_file",
                "visual_asset_sha1": sha1_file(path),
                "embedding_model": MULTIMODAL_EMBED_MODEL,
            },
        )
        return [ChunkItem(text=meta["text"], meta=meta, image_path=path)]

    @staticmethod
    def nearby_text(text: str, offset: int, window: int = 900) -> str:
        start = max(0, offset - window)
        end = min(len(text), offset + window)
        snippet = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text[start:end])
        return clean_text(snippet)[:1200]

    def enrich_visual_captions(self, chunks: List[ChunkItem]) -> None:
        if self.args.dry_run or not self.args.enable_captions:
            return
        assert self.bailian is not None
        visual_chunks = [chunk for chunk in chunks if chunk.image_path is not None and chunk.meta.get("type") == "visual_chunk"]
        if visual_chunks:
            self.log(f"  captioning {len(visual_chunks)} visual chunks")
        for visual_index, chunk in enumerate(visual_chunks, start=1):
            page = chunk.meta.get("page")
            where = f"p.{page}" if page else Path(str(chunk.image_path)).name
            self.wait_if_paused(f"caption {where}")
            self.log(f"    caption {visual_index}/{len(visual_chunks)} {where}")
            if chunk.image_path is None or chunk.meta.get("type") != "visual_chunk":
                continue
            try:
                data_url = image_to_data_url(chunk.image_path)
                caption = self.bailian.caption_image(data_url, str(chunk.meta.get("visual_context", "")))
                self.stats.caption_calls += 1
                if caption:
                    if is_low_information_visual_caption(caption):
                        chunk.meta["visual_caption"] = caption
                        chunk.meta["visual_quality"] = "empty"
                        chunk.meta["caption_model"] = self.args.caption_model
                        chunk.skipped_reason = "low-information visual caption"
                        self.log(f"    skipped low-information visual {where}")
                        continue
                    merged = clean_text("\n".join([
                        str(chunk.meta.get("text", "")),
                        "[cloud visual caption]",
                        caption,
                    ]))
                    chunk.text = merged
                    chunk.meta["text"] = merged
                    chunk.meta["visual_caption"] = caption
                    chunk.meta["visual_quality"] = "useful"
                    chunk.meta["caption_model"] = self.args.caption_model
                    chunk.meta["text_len"] = len(merged)
                    chunk.meta["text_sha1"] = sha1_text(merged)
                    chunk.meta["citation"] = build_citation(chunk.meta)
            except Exception as exc:
                chunk.meta["caption_error"] = str(exc)
                self.log(f"    caption failed {where}: {exc}")

    def add_caption_text_chunks(self, chunks: List[ChunkItem]) -> List[ChunkItem]:
        additions: List[ChunkItem] = []
        visual_chunks = [chunk for chunk in chunks if chunk.meta.get("type") == "visual_chunk"]
        for visual_index, chunk in enumerate(visual_chunks, start=1):
            if chunk.skipped_reason:
                continue
            caption = clean_text(str(chunk.meta.get("visual_caption") or ""))
            if not caption or is_low_information_visual_caption(caption):
                continue
            meta = dict(chunk.meta)
            meta.update({
                "type": "caption_text_chunk",
                "modality": "text",
                "format": "caption_text",
                "text": caption,
                "parser": "cloud_bailian_visual_caption_text",
                "chunk_index": visual_index,
                "parent_visual_chunk_uid": chunk.meta.get("chunk_uid"),
                "parent_type": "visual_chunk",
                "image_path": chunk.meta.get("image_path"),
                "embedding_model": self.args.text_embedding_model,
                "visual_quality": chunk.meta.get("visual_quality", "useful"),
            })
            meta = self.finalize_metadata(meta)
            additions.append(ChunkItem(text=meta["text"], meta=meta))
        if additions:
            self.log(f"  created {len(additions)} caption_text_chunk items")
        return chunks + additions

    def embed_chunks(self, chunks: List[ChunkItem]) -> None:
        if self.args.dry_run:
            return
        assert self.bailian is not None

        text_indices: List[int] = []
        text_inputs: List[str] = []
        for idx, chunk in enumerate(chunks):
            if chunk.skipped_reason:
                continue
            if chunk.image_path is not None and chunk.meta.get("type") == "visual_chunk":
                continue
            text = build_embed_text(chunk.meta, chunk.text)
            if not text.strip():
                chunk.skipped_reason = "empty text"
                continue
            text_indices.append(idx)
            text_inputs.append(text)

        if text_inputs:
            self.log(f"  embedding {len(text_inputs)} text/table chunks")
            vectors = self.bailian.embed_texts(text_inputs, text_type="document")
            self.stats.text_embedding_calls += (len(text_inputs) + 9) // 10
            for idx, vec in zip(text_indices, vectors):
                chunks[idx].vector = vec
                chunks[idx].meta["embedding_model"] = self.args.text_embedding_model

        visual_chunks = [chunk for chunk in chunks if chunk.image_path is not None and chunk.meta.get("type") == "visual_chunk"]
        visual_chunks = [chunk for chunk in visual_chunks if not chunk.skipped_reason]
        if visual_chunks:
            self.log(f"  embedding {len(visual_chunks)} visual chunks")
        for visual_index, chunk in enumerate(visual_chunks, start=1):
            page = chunk.meta.get("page")
            where = f"p.{page}" if page else Path(str(chunk.image_path)).name
            self.wait_if_paused(f"visual embedding {where}")
            self.log(f"    visual embedding {visual_index}/{len(visual_chunks)} {where}")
            try:
                data_url = image_to_data_url(chunk.image_path)
                context = build_embed_text(chunk.meta, chunk.text)
                chunk.vector = self.bailian.embed_image_fusion(data_url, context)
                chunk.meta["embedding_model"] = self.args.multimodal_embedding_model
                chunk.meta["visual_embedding"] = "bailian_qwen3_vl_fusion"
                self.stats.visual_embedding_calls += 1
            except Exception as exc:
                chunk.meta["visual_embedding_error"] = str(exc)
                self.log(f"    visual embedding failed {where}: {exc}")
                if self.args.visual_fallback_text:
                    fallback_text = build_embed_text(chunk.meta, chunk.text)
                    try:
                        self.log(f"    falling back to text embedding {where}")
                        chunk.vector = self.bailian.embed_texts([fallback_text], text_type="document")[0]
                        chunk.meta["embedding_model"] = self.args.text_embedding_model
                        chunk.meta["visual_embedding"] = "text_fallback"
                        self.stats.text_embedding_calls += 1
                    except Exception as fallback_exc:
                        chunk.skipped_reason = f"visual and text fallback failed: {fallback_exc}"
                        self.log(f"    text fallback failed {where}: {fallback_exc}")
                else:
                    chunk.skipped_reason = f"visual embedding failed: {exc}"

    def validate_chunks(self, chunks: List[ChunkItem]) -> List[ChunkItem]:
        valid: List[ChunkItem] = []
        for chunk in chunks:
            missing = [key for key in ["schema_version", "text", "source", "project_tag", "collection", "chunk_uid"] if not chunk.meta.get(key)]
            if missing:
                chunk.skipped_reason = "metadata missing: " + ",".join(missing)
            if len(str(chunk.meta.get("text", "")).strip()) < 5:
                chunk.skipped_reason = chunk.skipped_reason or "text too short"
            if not self.args.dry_run and chunk.vector is None:
                chunk.skipped_reason = chunk.skipped_reason or "missing vector"
            if chunk.skipped_reason:
                self.stats.chunks_skipped += 1
            else:
                valid.append(chunk)
        return valid

    def write_quality_report(self, file_path: Path, chunks: List[ChunkItem], valid: List[ChunkItem]) -> None:
        type_counts = Counter(str(chunk.meta.get("type", "unknown")) for chunk in chunks)
        modality_counts = Counter(str(chunk.meta.get("modality", "unknown")) for chunk in chunks)
        report = {
            "source_path": str(file_path.resolve()),
            "source": file_path.name,
            "collection": self.args.collection,
            "project_tag": self.args.project_tag,
            "created_at": now_iso(),
            "pipeline_version": PIPELINE_VERSION,
            "schema_version": METADATA_SCHEMA_VERSION,
            "total_chunks": len(chunks),
            "valid_chunks": len(valid),
            "skipped_chunks": len(chunks) - len(valid),
            "type_counts": dict(type_counts),
            "modality_counts": dict(modality_counts),
            "has_visual_chunks": bool(type_counts.get("visual_chunk")),
            "has_table_chunks": bool(type_counts.get("table_chunk")),
            "sample_citations": [chunk.meta.get("citation") for chunk in valid[:8]],
            "sample_chunks": [
                {
                    "type": chunk.meta.get("type"),
                    "modality": chunk.meta.get("modality"),
                    "page": chunk.meta.get("page"),
                    "section_path": chunk.meta.get("section_path"),
                    "citation": chunk.meta.get("citation"),
                    "text_preview": str(chunk.meta.get("text", ""))[:240],
                    "skipped_reason": chunk.skipped_reason,
                }
                for chunk in chunks[:10]
            ],
        }
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", file_path.stem)[:80] or "document"
        digest = sha1_text(str(file_path.resolve()))[:10]
        out = self.reports_dir / f"{datetime.now().strftime('%Y%m%d')}_{self.args.collection}_{safe_name}_{digest}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(self) -> IngestStats:
        input_path = Path(self.args.input_dir).expanduser().resolve()
        files = iter_supported_files(input_path)
        self.stats.files_seen = len(files)
        if not files:
            raise RuntimeError(f"No supported files found in {input_path}")

        self.log(f"Found {len(files)} supported files")
        writer: Optional[MilvusWriter] = None
        graph_writer: Optional[GraphPassageWriter] = None
        if not self.args.dry_run:
            if self.bailian is not None:
                self.log("Checking Bailian API key and text embedding model")
                self.bailian.preflight()
                self.stats.text_embedding_calls += 1
                self.log("Bailian API preflight ok")
            writer = MilvusWriter(
                uri=self.args.milvus_uri,
                db_name=self.args.db_name,
                collection_name=self.args.collection,
                dimension=self.dimension,
                token=self.args.milvus_token,
                user=self.args.milvus_user,
                password=self.args.milvus_password,
                reset=self.args.reset,
            )
            if self.args.enable_vgrag_passages:
                graph_writer = GraphPassageWriter(
                    uri=self.args.milvus_uri,
                    db_name=self.args.db_name,
                    prefix=self.args.graph_prefix or default_graph_prefix(self.args.collection),
                    dimension=self.dimension,
                    token=self.args.milvus_token,
                    user=self.args.milvus_user,
                    password=self.args.milvus_password,
                    reset=self.args.reset_graph,
                )

        for file_index, path in enumerate(files, start=1):
            self.wait_if_paused(f"file {file_index}")
            self.log(f"[{file_index}/{len(files)}] parsing {path}")
            try:
                chunks = self.parse_file(path)
                self.stats.chunks_created += len(chunks)
                if not chunks:
                    self.log(f"  no chunks: {path.name}")
                    continue
                type_counts = Counter(str(chunk.meta.get("type", "unknown")) for chunk in chunks)
                self.log(f"  created {len(chunks)} chunks: {dict(type_counts)}")
                self.enrich_visual_captions(chunks)
                chunks = self.add_caption_text_chunks(chunks)
                self.embed_chunks(chunks)
                valid = self.validate_chunks(chunks)
                self.log(f"  valid chunks after validation: {len(valid)}/{len(chunks)}")
                self.write_quality_report(path, chunks, valid)
                if writer and valid:
                    self.wait_if_paused("Milvus write")
                    deleted = writer.delete_existing_chunks(valid)
                    if deleted:
                        self.log(f"  removed {deleted} existing duplicate chunks before insert")
                    inserted = writer.insert(valid, batch_size=self.args.insert_batch_size)
                    self.stats.chunks_inserted += inserted
                    graph_inserted = graph_writer.upsert_passages(valid) if graph_writer else {}
                    if graph_writer:
                        self.log(
                            "  upserted graph sidecar: "
                            f"{graph_inserted.get('passages', 0)}/{len(valid)} passages, "
                            f"{graph_inserted.get('entities', 0)} entities, "
                            f"{graph_inserted.get('relations', 0)} relations"
                        )
                    self.log(f"  inserted {inserted}/{len(chunks)} chunks")
                else:
                    self.log(f"  dry-run/valid chunks: {len(valid)}/{len(chunks)}")
                self.stats.files_ingested += 1
                with CLOUD_RUNTIME_LOCK:
                    CLOUD_RUNTIME["stats"] = stats_to_dict(self.stats)
            except Exception as exc:
                msg = f"{path}: {exc}"
                self.stats.errors.append(msg)
                self.log(f"  ERROR {msg}")
                with CLOUD_RUNTIME_LOCK:
                    CLOUD_RUNTIME["stats"] = stats_to_dict(self.stats)
                if not self.args.continue_on_error:
                    raise
        return self.stats


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cloud-only Alibaba Bailian multimodal Milvus ingestor",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-dir", default="", help="File or directory to ingest")
    parser.add_argument("--project-tag", default="", help="Project/batch tag, for example SIQ-PROJECT-2026")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Target Milvus collection")
    parser.add_argument("--db-name", default=DEFAULT_DB, help="Target Milvus database")
    parser.add_argument("--milvus-uri", default=DEFAULT_MILVUS_URI, help="Milvus or Zilliz Cloud URI")
    parser.add_argument("--milvus-token", default=os.getenv("MILVUS_TOKEN", ""), help="Milvus/Zilliz token")
    parser.add_argument("--milvus-user", default=os.getenv("MILVUS_USER", ""), help="Milvus user")
    parser.add_argument("--milvus-password", default=os.getenv("MILVUS_PASSWORD", ""), help="Milvus password")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate target collection before ingest")

    parser.add_argument("--dashscope-api-key", default=os.getenv("DASHSCOPE_API_KEY", ""), help="Alibaba Model Studio API key")
    parser.add_argument("--dashscope-base-url", default=DEFAULT_DASHSCOPE_BASE_URL, help="DashScope base URL")
    parser.add_argument("--dimension", type=int, default=DEFAULT_DIM, help="Embedding dimension")
    parser.add_argument("--text-embedding-model", default=TEXT_EMBED_MODEL, help="Text embedding model")
    parser.add_argument("--multimodal-embedding-model", default=MULTIMODAL_EMBED_MODEL, help="Multimodal embedding model")
    parser.add_argument("--caption-model", default=DEFAULT_CAPTION_MODEL, help="Vision caption model")
    parser.add_argument("--timeout", type=int, default=90, help="HTTP timeout seconds")
    parser.add_argument("--max-retries", type=int, default=3, help="API retry count")

    parser.add_argument("--enable-visual", action="store_true", help="Create visual chunks for images and PDF pages")
    parser.add_argument("--enable-captions", action="store_true", help="Use Qwen-VL to caption visual chunks")
    parser.add_argument("--enable-table", action=argparse.BooleanOptionalAction, default=True, help="Create table chunks from Markdown tables")
    parser.add_argument("--enable-pdf-page-visuals", action=argparse.BooleanOptionalAction, default=True, help="Render PDF pages as visual chunks")
    parser.add_argument("--max-pdf-visual-pages", type=int, default=0, help="0 means no limit")
    parser.add_argument("--pdf-render-scale", type=float, default=1.6, help="PyMuPDF render scale for PDF page images")
    parser.add_argument("--visual-fallback-text", action=argparse.BooleanOptionalAction, default=True, help="Fallback to text embedding if visual embedding fails")

    parser.add_argument("--enable-vgrag-passages", action="store_true", help="Mirror chunks into Vector Graph RAG passage sidecar")
    parser.add_argument("--graph-prefix", default="", help="Vector Graph RAG sidecar prefix; defaults to target collection name")
    parser.add_argument("--reset-graph", action="store_true", help="Drop and recreate graph sidecar collections")

    parser.add_argument("--assets-dir", default=str(SCRIPT_DIR / ".cloud_ingest_assets"), help="Rendered PDF page image cache")
    parser.add_argument("--reports-dir", default=str(SCRIPT_DIR / "ingest_quality_reports"), help="Quality report directory")
    parser.add_argument("--insert-batch-size", type=int, default=128, help="Milvus insert batch size")
    parser.add_argument("--continue-on-error", action=argparse.BooleanOptionalAction, default=True, help="Continue if a file fails")
    parser.add_argument("--dry-run", action="store_true", help="Parse and write quality reports without API calls or Milvus writes")
    parser.add_argument("--ui", action=argparse.BooleanOptionalAction, default=True, help="Launch Gradio UI")
    parser.add_argument("--host", default=os.getenv("CLOUD_INGEST_HOST", "0.0.0.0"), help="UI host")
    parser.add_argument("--port", type=int, default=int(os.getenv("CLOUD_INGEST_PORT", "7863")), help="UI port")
    return parser


def namespace_from_ui(
    input_dir: str,
    project_tag: str,
    collection: str,
    db_name: str,
    milvus_uri: str,
    milvus_token: str,
    milvus_user: str,
    milvus_password: str,
    dashscope_api_key: str,
    reset: bool,
    dimension: int,
    enable_visual: bool,
    enable_captions: bool,
    enable_table: bool,
    enable_pdf_page_visuals: bool,
    max_pdf_visual_pages: int,
    enable_vgrag_passages: bool,
    graph_prefix: str,
    reset_graph: bool,
    dry_run: bool,
) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args([
        "--input-dir", input_dir or ".",
        "--project-tag", project_tag or "SIQ-PROJECT-2026",
    ])
    args.collection = collection or DEFAULT_COLLECTION
    args.db_name = db_name or DEFAULT_DB
    args.milvus_uri = milvus_uri or DEFAULT_MILVUS_URI
    args.milvus_token = milvus_token or ""
    args.milvus_user = milvus_user or ""
    args.milvus_password = milvus_password or ""
    args.dashscope_api_key = validate_dashscope_api_key(dashscope_api_key or os.getenv("DASHSCOPE_API_KEY", ""))
    args.reset = bool(reset)
    args.dimension = int(dimension or DEFAULT_DIM)
    args.enable_visual = bool(enable_visual)
    args.enable_captions = bool(enable_captions)
    args.enable_table = bool(enable_table)
    args.enable_pdf_page_visuals = bool(enable_pdf_page_visuals)
    args.max_pdf_visual_pages = int(max_pdf_visual_pages or 0)
    args.enable_vgrag_passages = bool(enable_vgrag_passages)
    args.graph_prefix = graph_prefix or default_graph_prefix(args.collection)
    args.reset_graph = bool(reset_graph)
    args.dry_run = bool(dry_run)
    return args


def prepare_uploaded_files(files: Optional[List[Any]]) -> str:
    if not files:
        return ""
    upload_root = SCRIPT_DIR / ".cloud_ingest_uploads" / datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_root.mkdir(parents=True, exist_ok=True)
    for item in files:
        src = Path(getattr(item, "name", str(item))).resolve()
        if src.exists() and src.is_file():
            shutil.copy2(src, upload_root / src.name)
    return str(upload_root)


def start_ui_ingest(
    files: Optional[List[Any]],
    input_dir: str,
    project_tag: str,
    collection: str,
    db_name: str,
    milvus_uri: str,
    milvus_token: str,
    milvus_user: str,
    milvus_password: str,
    dashscope_api_key: str,
    reset: bool,
    dimension: int,
    enable_visual: bool,
    enable_captions: bool,
    enable_table: bool,
    enable_pdf_page_visuals: bool,
    max_pdf_visual_pages: int,
    enable_vgrag_passages: bool,
    graph_prefix: str,
    reset_graph: bool,
    dry_run: bool,
) -> Tuple[str, str]:
    with CLOUD_RUNTIME_LOCK:
        if CLOUD_RUNTIME.get("active"):
            return runtime_snapshot()

    upload_dir = prepare_uploaded_files(files)
    effective_input = upload_dir or (input_dir or "").strip()
    if not effective_input:
        return "请选择文件或填写文档目录", ""
    if not project_tag.strip():
        return "请填写 project_tag", ""
    try:
        effective_dashscope_api_key = validate_dashscope_api_key(dashscope_api_key or os.getenv("DASHSCOPE_API_KEY", ""))
    except ValueError as exc:
        return str(exc), ""
    if not dry_run and not effective_dashscope_api_key:
        return "请填写阿里百炼 API Key，或设置 DASHSCOPE_API_KEY 环境变量", ""
    args = namespace_from_ui(
        effective_input,
        project_tag,
        collection,
        db_name,
        milvus_uri,
        milvus_token,
        milvus_user,
        milvus_password,
        effective_dashscope_api_key,
        reset,
        dimension,
        enable_visual,
        enable_captions,
        enable_table,
        enable_pdf_page_visuals,
        max_pdf_visual_pages,
        enable_vgrag_passages,
        graph_prefix,
        reset_graph,
        dry_run,
    )

    with CLOUD_RUNTIME_LOCK:
        CLOUD_RUNTIME.update({
            "active": True,
            "paused": False,
            "started_at": now_iso(),
            "finished_at": "",
            "updated_at": now_iso(),
            "logs": [],
            "config": {
                "input_dir": effective_input,
                "project_tag": args.project_tag,
                "collection": args.collection,
                "db_name": args.db_name,
                "milvus_uri": args.milvus_uri,
                "dimension": args.dimension,
                "enable_visual": args.enable_visual,
                "enable_captions": args.enable_captions,
                "enable_vgrag_passages": args.enable_vgrag_passages,
                "dry_run": args.dry_run,
            },
            "stats": {},
            "result": "",
        })
    runtime_log("Cloud Bailian ingest queued")

    def worker() -> None:
        try:
            runtime_log("Cloud Bailian ingest started")
            stats = CloudBailianIngestor(args).run()
            result = f"完成: 入库 {stats.chunks_inserted} chunks，错误 {len(stats.errors)} 个"
            with CLOUD_RUNTIME_LOCK:
                CLOUD_RUNTIME["stats"] = stats_to_dict(stats)
                CLOUD_RUNTIME["result"] = result
        except Exception as exc:
            runtime_log(f"FAILED: {exc}")
            with CLOUD_RUNTIME_LOCK:
                CLOUD_RUNTIME["result"] = f"失败: {exc}"
        finally:
            with CLOUD_RUNTIME_LOCK:
                CLOUD_RUNTIME["active"] = False
                CLOUD_RUNTIME["paused"] = False
                CLOUD_RUNTIME["finished_at"] = now_iso()
                CLOUD_RUNTIME["updated_at"] = now_iso()

    threading.Thread(target=worker, daemon=True).start()
    return runtime_snapshot()


def refresh_cloud_collections(
    milvus_uri: str,
    db_name: str,
    milvus_token: str,
    milvus_user: str,
    milvus_password: str,
    current_collection: Optional[str],
):
    choices = list_collection_choices(milvus_uri or DEFAULT_MILVUS_URI, db_name or DEFAULT_DB, milvus_token or "", milvus_user or "", milvus_password or "")
    value = current_collection if current_collection in choices else (DEFAULT_COLLECTION if DEFAULT_COLLECTION in choices else (choices[0] if choices else None))
    snapshot = collection_status_snapshot(milvus_uri or DEFAULT_MILVUS_URI, db_name or DEFAULT_DB, milvus_token or "", milvus_user or "", milvus_password or "")
    msg = f"已刷新 {db_name or DEFAULT_DB}: {snapshot.get('total_collections', 0)} 个 Collection"
    if snapshot.get("error"):
        msg = f"刷新失败: {snapshot['error']}"
    create_value = value or DEFAULT_COLLECTION
    if gr is None:
        return snapshot, msg, value, value, create_value
    return (
        snapshot,
        msg,
        gr.update(choices=choices, value=value),
        gr.update(choices=choices, value=value),
        gr.update(value=create_value),
    )


def refresh_manage_collections(
    milvus_uri: str,
    db_name: str,
    milvus_token: str,
    milvus_user: str,
    milvus_password: str,
    current_collection: Optional[str],
):
    snapshot, msg, _, drop_update, create_update = refresh_cloud_collections(
        milvus_uri,
        db_name,
        milvus_token,
        milvus_user,
        milvus_password,
        current_collection,
    )
    return snapshot, msg, drop_update, create_update


def refresh_database_dropdowns(
    milvus_uri: str,
    milvus_token: str,
    milvus_user: str,
    milvus_password: str,
    current_db: Optional[str],
):
    snapshot = list_database_snapshot(milvus_uri or DEFAULT_MILVUS_URI, milvus_token or "", milvus_user or "", milvus_password or "")
    choices = list(snapshot.get("databases") or [DEFAULT_DB])
    value = current_db if current_db in choices else (DEFAULT_DB if DEFAULT_DB in choices else (choices[0] if choices else DEFAULT_DB))
    msg = f"已刷新 Database：{', '.join(choices)}"
    if snapshot.get("error"):
        msg = f"刷新 Database 失败，已保留默认项: {snapshot['error']}"
    if gr is None:
        return value, value, msg
    return (
        gr.update(choices=choices, value=value),
        gr.update(choices=choices, value=value),
        msg,
    )


def refresh_database_and_collections(
    milvus_uri: str,
    milvus_token: str,
    milvus_user: str,
    milvus_password: str,
    current_db: Optional[str],
    current_collection: Optional[str],
):
    snapshot = list_database_snapshot(milvus_uri or DEFAULT_MILVUS_URI, milvus_token or "", milvus_user or "", milvus_password or "")
    db_choices = list(snapshot.get("databases") or [DEFAULT_DB])
    db_value = current_db if current_db in db_choices else (DEFAULT_DB if DEFAULT_DB in db_choices else (db_choices[0] if db_choices else DEFAULT_DB))

    collection_snapshot, collection_msg, collection_update, drop_update, create_update = refresh_cloud_collections(
        milvus_uri or DEFAULT_MILVUS_URI,
        db_value,
        milvus_token or "",
        milvus_user or "",
        milvus_password or "",
        current_collection,
    )
    db_msg = f"已刷新 Database：{', '.join(db_choices)}"
    if snapshot.get("error"):
        db_msg = f"刷新 Database 失败，已保留默认项: {snapshot['error']}"
    msg = f"{db_msg}\n{collection_msg}"
    if gr is None:
        return db_value, db_value, collection_snapshot, msg, collection_update, drop_update, create_update
    db_update = gr.update(choices=db_choices, value=db_value)
    return (
        db_update,
        gr.update(choices=db_choices, value=db_value),
        collection_snapshot,
        msg,
        collection_update,
        drop_update,
        create_update,
    )


def build_ui_css() -> str:
    return r"""
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

/* ===== Labels ===== */
.a-card label,
.a-card div > label {
  font-size: 12px !important;
  font-weight: 600 !important;
  color: var(--a-text-secondary) !important;
  margin-bottom: 5px !important;
  letter-spacing: -0.2px !important;
}

/* ===== Checkbox ===== */
.a-card input[type="checkbox"] {
  accent-color: var(--a-primary) !important;
  width: 16px !important;
  height: 16px !important;
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

/* ===== Log ===== */
.a-log textarea {
  font-family: "SF Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
  background: #1c1c1e !important;
  color: #f5f5f7 !important;
  border: none !important;
  border-radius: var(--a-radius-sm) !important;
  min-height: 300px !important;
  padding: 14px !important;
}

/* ===== Stats / Status ===== */
.a-stats textarea,
.a-status textarea {
  font-family: "SF Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
  background: #fafbfc !important;
  color: var(--a-text) !important;
  border: 1px solid var(--a-border) !important;
  border-radius: var(--a-radius-sm) !important;
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
  .a-log textarea { min-height: 200px !important; }
}
"""

def build_ui():
    if gr is None:
        raise RuntimeError("gradio is required for UI. Install with: pip install gradio")

    initial_db_choices = list_database_choices(DEFAULT_MILVUS_URI)
    initial_choices = list_collection_choices(DEFAULT_MILVUS_URI, DEFAULT_DB)
    if not initial_choices:
        initial_choices = list(ROLE_REGISTRY.keys())
    default_collection = DEFAULT_COLLECTION if DEFAULT_COLLECTION in initial_choices else (initial_choices[0] if initial_choices else DEFAULT_COLLECTION)

    with gr.Blocks(title="Cloud Bailian Milvus Ingest") as demo:
        gr.HTML("""
        <div class="a-header">
          <div class="a-brand">
            <span class="a-logo">☁️</span>
            <div class="a-title-wrap">
              <h1>Cloud Bailian Milvus Ingest</h1>
              <p>阿里百炼云端多模态 embedding + Milvus 入库</p>
            </div>
          </div>
          <div class="a-health">
            <span class="a-pill">SIQChunkMetadata v1</span>
            <span class="a-pill">1024d</span>
            <span class="a-pill">text / table / visual</span>
          </div>
        </div>
        """)

        with gr.Row(equal_height=False, elem_classes=["a-workspace"]):
            with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):

                with gr.Group(elem_classes=["a-card", "a-upload"]):
                    gr.Markdown("### 文档来源")
                    files = gr.File(
                        label="选择本机文件",
                        file_count="multiple",
                        file_types=[".pdf", ".md", ".markdown", ".txt", ".docx", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"],
                    )
                    input_dir = gr.Textbox(label="或填写文档目录", placeholder="/path/to/project_docs")

                with gr.Group(elem_classes=["a-card"]):
                    gr.Markdown("### 项目配置")
                    project_tag = gr.Textbox(label="批次标签 / project_tag", value="SIQ-PROJECT-2026")
                    db_name = gr.Dropdown(label="Milvus Database", choices=initial_db_choices, value=DEFAULT_DB, allow_custom_value=True)
                    collection = gr.Dropdown(label="目标 Collection", choices=initial_choices, value=default_collection, allow_custom_value=True)
                    dimension = gr.Number(label="向量维度", value=DEFAULT_DIM, precision=0)

                with gr.Group(elem_classes=["a-card"]):
                    gr.Markdown("### 功能开关")
                    enable_visual = gr.Checkbox(label="启用多模态 visual_chunk", value=True)
                    enable_captions = gr.Checkbox(label="生成视觉 caption", value=True)
                    enable_table = gr.Checkbox(label="识别 Markdown 表格块", value=True)
                    enable_pdf_page_visuals = gr.Checkbox(label="PDF 页面渲染为视觉块", value=True)
                    max_pdf_visual_pages = gr.Number(label="PDF 视觉页数上限（0 不限）", value=8, precision=0)
                    enable_vgrag_passages = gr.Checkbox(label="同步到 Vector Graph RAG passages", value=False)
                    graph_prefix = gr.Textbox(label="Graph prefix", value="")
                    reset = gr.Checkbox(label="重建目标 Collection", value=False)
                    reset_graph = gr.Checkbox(label="重建 Graph sidecar", value=False)
                    dry_run = gr.Checkbox(label="Dry run（不调用 API，不写 Milvus）", value=False)

                with gr.Group(elem_classes=["a-card"]):
                    gr.Markdown("### 执行")
                    with gr.Row():
                        start_btn = gr.Button("开始入库", variant="primary")
                        pause_btn = gr.Button("暂停", variant="secondary")
                        resume_btn = gr.Button("继续", variant="secondary")
                    runtime_status = gr.Textbox(label="运行状态", lines=12, interactive=False, elem_classes=["a-stats"])

            with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):

                with gr.Group(elem_classes=["a-card"]):
                    gr.Markdown("### 连接配置")
                    dashscope_api_key = gr.Textbox(label="阿里百炼 API Key", type="password", placeholder="可留空，使用 DASHSCOPE_API_KEY")
                    milvus_uri = gr.Textbox(label="Milvus URI", value=DEFAULT_MILVUS_URI)
                    milvus_token = gr.Textbox(label="Milvus / Zilliz Token", type="password")
                    milvus_user = gr.Textbox(label="Milvus User")
                    milvus_password = gr.Textbox(label="Milvus Password", type="password")
                    refresh_db_btn = gr.Button("刷新 Database", variant="secondary")
                    refresh_btn = gr.Button("刷新 Collection", variant="secondary")

                with gr.Group(elem_classes=["a-card"]):
                    gr.Markdown("### 运行日志")
                    runtime_logs = gr.Textbox(label="", lines=18, interactive=False, elem_classes=["a-log"], show_label=False)

        with gr.Accordion("参数说明", open=False):
            gr.Markdown("""
### 设计边界
这个脚本只调用阿里百炼云端 API，不依赖本地 MinerU、vLLM、OCR 或 reranker。

### 入库能力
- 文本 chunk：`text-embedding-v4`，默认 `1024` 维。
- 视觉 chunk：`qwen3-vl-embedding`，默认 `1024` 维。
- 视觉 caption：默认 `qwen3-vl-flash`。
- Metadata：沿用 `SIQChunkMetadata v1`。
- Milvus schema：`id + vector + project_tag + metadata`。

### 文件建议
- 普通 PDF：可以直接入库。
- 扫描 PDF：建议开启 PDF 页面视觉块和视觉 caption。
- 带图片的 MD：图片路径存在时会生成 visual_chunk。
- 表格：Markdown 表格会生成 table_chunk。
""")

        with gr.Accordion("Collection 管理", open=True):
            with gr.Row(equal_height=False, elem_classes=["a-workspace"]):
                with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):
                    with gr.Group(elem_classes=["a-card"]):
                        gr.Markdown("### 连接信息")
                        stable_manage_uri = gr.Textbox(label="Milvus URI", value=DEFAULT_MILVUS_URI)
                        stable_manage_db = gr.Dropdown(label="Database", choices=initial_db_choices, value=DEFAULT_DB, allow_custom_value=True)
                        stable_manage_token = gr.Textbox(label="Token", type="password")
                        stable_manage_user = gr.Textbox(label="User")
                        stable_manage_password = gr.Textbox(label="Password", type="password")
                        stable_refresh_db_btn = gr.Button("刷新 Database", variant="secondary")
                        stable_manage_refresh = gr.Button("刷新 Collection", variant="secondary")

                with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):
                    with gr.Group(elem_classes=["a-card"]):
                        gr.Markdown("### 状态")
                        stable_collection_status = gr.JSON(label="Collection 状态", elem_classes=["a-json"])
                        stable_manage_msg = gr.Textbox(label="操作结果", interactive=False)

            with gr.Row(equal_height=False, elem_classes=["a-workspace"]):
                with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):
                    with gr.Group(elem_classes=["a-card"]):
                        gr.Markdown("### 创建 Collection")
                        stable_create_name = gr.Textbox(label="新建 Collection", value=DEFAULT_COLLECTION)
                        stable_create_dim = gr.Number(label="维度", value=DEFAULT_DIM, precision=0)
                        stable_create_btn = gr.Button("创建 / 确认存在", variant="primary")

                with gr.Column(scale=1, min_width=520, elem_classes=["a-stack"]):
                    with gr.Group(elem_classes=["a-card"]):
                        gr.Markdown("### 删除 Collection")
                        stable_drop_collection = gr.Dropdown(label="删除 Collection", choices=initial_choices, value=default_collection, allow_custom_value=True)
                        stable_drop_btn = gr.Button("删除 Collection", variant="stop")

        refresh_runtime_btn = gr.Button("刷新运行状态", variant="secondary")

        refresh_inputs = [milvus_uri, db_name, milvus_token, milvus_user, milvus_password, collection]
        refresh_outputs = [stable_collection_status, stable_manage_msg, collection, stable_drop_collection, stable_create_name]
        refresh_btn.click(fn=refresh_cloud_collections, inputs=refresh_inputs, outputs=refresh_outputs)
        refresh_db_btn.click(
            fn=refresh_database_and_collections,
            inputs=[milvus_uri, milvus_token, milvus_user, milvus_password, db_name, collection],
            outputs=[db_name, stable_manage_db, stable_collection_status, stable_manage_msg, collection, stable_drop_collection, stable_create_name],
        )
        db_name.change(fn=refresh_cloud_collections, inputs=refresh_inputs, outputs=refresh_outputs)

        start_inputs = [
            files, input_dir, project_tag, collection, db_name, milvus_uri, milvus_token, milvus_user, milvus_password,
            dashscope_api_key, reset, dimension, enable_visual, enable_captions, enable_table,
            enable_pdf_page_visuals, max_pdf_visual_pages, enable_vgrag_passages, graph_prefix, reset_graph, dry_run,
        ]
        start_btn.click(fn=start_ui_ingest, inputs=start_inputs, outputs=[runtime_status, runtime_logs])
        pause_btn.click(fn=pause_runtime, inputs=None, outputs=[runtime_status, runtime_logs], queue=False)
        resume_btn.click(fn=resume_runtime, inputs=None, outputs=[runtime_status, runtime_logs], queue=False)
        demo.load(fn=runtime_snapshot, inputs=None, outputs=[runtime_status, runtime_logs])

        stable_refresh_inputs = [stable_manage_uri, stable_manage_db, stable_manage_token, stable_manage_user, stable_manage_password, stable_drop_collection]
        stable_refresh_outputs = [
            stable_collection_status,
            stable_manage_msg,
            stable_drop_collection,
            stable_create_name,
        ]
        stable_manage_refresh.click(fn=refresh_manage_collections, inputs=stable_refresh_inputs, outputs=stable_refresh_outputs)
        stable_refresh_db_btn.click(
            fn=refresh_database_and_collections,
            inputs=[stable_manage_uri, stable_manage_token, stable_manage_user, stable_manage_password, stable_manage_db, stable_drop_collection],
            outputs=[db_name, stable_manage_db, stable_collection_status, stable_manage_msg, collection, stable_drop_collection, stable_create_name],
        )
        stable_manage_db.change(fn=refresh_manage_collections, inputs=stable_refresh_inputs, outputs=stable_refresh_outputs)
        stable_create_btn.click(
            fn=create_collection_for_ui,
            inputs=[stable_manage_uri, stable_manage_db, stable_create_name, stable_create_dim, stable_manage_token, stable_manage_user, stable_manage_password],
            outputs=stable_manage_msg,
        )
        stable_drop_btn.click(
            fn=drop_collection_for_ui,
            inputs=[stable_manage_uri, stable_manage_db, stable_drop_collection, stable_manage_token, stable_manage_user, stable_manage_password],
            outputs=stable_manage_msg,
        )

        refresh_runtime_btn.click(fn=runtime_snapshot, inputs=None, outputs=[runtime_status, runtime_logs], queue=False)
        runtime_timer = gr.Timer(value=2.0)
        runtime_timer.tick(fn=runtime_snapshot, inputs=None, outputs=[runtime_status, runtime_logs], queue=False)

    return demo

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.ui:
        demo = build_ui()
        demo.queue(default_concurrency_limit=2)
        demo.launch(server_name=args.host, server_port=args.port, share=False, css=build_ui_css())
        return 0
    if not args.input_dir:
        parser.error("--input-dir is required when --no-ui is set")
    if not args.project_tag:
        parser.error("--project-tag is required when --no-ui is set")
    if not args.dry_run and not (args.dashscope_api_key or os.getenv("DASHSCOPE_API_KEY")):
        parser.error("--dashscope-api-key or DASHSCOPE_API_KEY is required unless --dry-run is set")
    args.graph_prefix = args.graph_prefix or default_graph_prefix(args.collection)

    ingestor = CloudBailianIngestor(args)
    stats = ingestor.run()
    summary = {
        "files_seen": stats.files_seen,
        "files_ingested": stats.files_ingested,
        "chunks_created": stats.chunks_created,
        "chunks_inserted": stats.chunks_inserted,
        "chunks_skipped": stats.chunks_skipped,
        "text_embedding_calls": stats.text_embedding_calls,
        "visual_embedding_calls": stats.visual_embedding_calls,
        "caption_calls": stats.caption_calls,
        "errors": stats.errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if stats.errors and not args.continue_on_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
