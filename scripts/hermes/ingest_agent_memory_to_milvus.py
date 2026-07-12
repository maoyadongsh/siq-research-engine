#!/usr/bin/env python3
# isort: skip_file
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import agent_memory_milvus  # noqa: E402


DEFAULT_COLLECTION = "siq_agent_memory"
DEFAULT_EMBED_URL = "http://127.0.0.1:8013/v1/embeddings"
DEFAULT_EMBED_MODEL = "Qwen3-VL-Embedding-2B"
DEFAULT_VECTOR_DIM = 1024
DEFAULT_PROFILE_FILES = {
    "README.md",
    "SOUL.md",
    "IDENTITY.md",
    "BOOTSTRAP.md",
    "AGENTS.md",
    "HEARTBEAT.md",
    "TOOLS.md",
    "USER.md",
    "WORKFLOW.md",
    "ORCHESTRATION_BRIDGE.md",
    "KNOWLEDGE_BASE.md",
    "config.yaml",
}
SHARED_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".txt"}


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8")).hexdigest()


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def split_text(text: str, *, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    clean_text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", clean_text) if item.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
            continue
        step = max(1, chunk_size - overlap)
        for index in range(0, len(paragraph), step):
            chunk = paragraph[index : index + chunk_size].strip()
            if chunk:
                chunks.append(chunk)
        current = ""
    if current:
        chunks.append(current)
    return chunks


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_agent_group(profile: str) -> str:
    if profile.startswith("siq_ic_"):
        return "primary_market"
    if profile in {"shared", "siq_ic_shared"}:
        return "shared"
    return "secondary_market"


def iter_profile_files(profiles_root: Path, manifest: dict[str, Any], selected_profiles: set[str] | None) -> list[dict[str, Any]]:
    profiles = [str(item) for item in manifest.get("profiles") or []]
    if selected_profiles:
        profiles = [profile for profile in profiles if profile in selected_profiles]
    items: list[dict[str, Any]] = []
    for profile in profiles:
        profile_dir = profiles_root / profile
        if not profile_dir.is_dir():
            continue
        if profile in {"shared", "siq_ic_shared"}:
            files = [
                path
                for path in sorted(profile_dir.rglob("*"))
                if path.is_file() and path.suffix.lower() in SHARED_SUFFIXES and "__pycache__" not in path.parts
            ]
        else:
            files = [profile_dir / name for name in sorted(DEFAULT_PROFILE_FILES) if (profile_dir / name).is_file()]
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            for chunk_index, chunk in enumerate(split_text(text), start=1):
                source_path = repo_relative(path)
                chunk_id = "profile_file:" + stable_id(profile, source_path, chunk_index, content_hash(chunk))
                items.append(
                    {
                        "id": chunk_id,
                        "profile": profile,
                        "agent_group": infer_agent_group(profile),
                        "source_path": source_path,
                        "chunk_index": chunk_index,
                        "title": f"{profile}/{path.name}#{chunk_index}",
                        "content": chunk,
                        "content_hash": content_hash(chunk),
                        "updated_at_ts": int(path.stat().st_mtime),
                    }
                )
    return items


def embedding_endpoint(args: argparse.Namespace) -> str:
    configured = (
        args.embed_url
        or os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL")
        or os.getenv("SIQ_EMBEDDING_BASE_URL")
        or os.getenv("EMBEDDING_BASE_URL")
        or DEFAULT_EMBED_URL
    )
    endpoint = str(configured).strip().rstrip("/")
    if endpoint.endswith("/v1"):
        return endpoint + "/embeddings"
    if endpoint.endswith("/v1/embeddings"):
        return endpoint
    return endpoint + "/v1/embeddings"


def embedding_endpoint_configured(args: argparse.Namespace) -> bool:
    return bool(
        args.embed_url
        or os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL")
        or os.getenv("SIQ_EMBEDDING_BASE_URL")
        or os.getenv("EMBEDDING_BASE_URL")
    )


def embed_batch(texts: list[str], *, endpoint: str, model: str, timeout: float) -> list[list[float]]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_API_KEY") or os.getenv("SIQ_EMBEDDING_API_KEY") or os.getenv("EMBEDDING_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client() as client:
        response = client.post(
            endpoint,
            headers=headers,
            json={"model": model, "input": texts},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise RuntimeError("embedding response size mismatch")
    vectors_by_index: dict[int, list[float]] = {}
    for fallback_index, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
            raise RuntimeError("embedding response item missing embedding")
        index = item.get("index")
        vectors_by_index[int(index) if isinstance(index, int) else fallback_index] = [float(value) for value in item["embedding"]]
    return [vectors_by_index[index] for index in range(len(texts))]


def to_vector_records(items: list[dict[str, Any]], vectors: list[list[float]]) -> list[agent_memory_milvus.AgentMemoryVectorRecord]:
    records: list[agent_memory_milvus.AgentMemoryVectorRecord] = []
    for item, vector in zip(items, vectors, strict=True):
        metadata = {
            "schema_version": "siq_agent_profile_chunk_v1",
            "source_path": item["source_path"],
            "chunk_index": item["chunk_index"],
            "content_hash": item["content_hash"],
        }
        records.append(
            agent_memory_milvus.AgentMemoryVectorRecord(
                id=item["id"],
                vector=vector,
                tenant_id="default",
                visibility="system_shared",
                profile=item["profile"],
                agent_group=item["agent_group"],
                memory_type="profile_file",
                source_kind="profile_file",
                source_id=item["id"],
                source_path=item["source_path"],
                content_hash=item["content_hash"],
                title=item["title"],
                content=item["content"],
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                updated_at_ts=int(item.get("updated_at_ts") or 0),
            )
        )
    return records


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Hermes agent profile knowledge into the SIQ agent memory Milvus collection.")
    parser.add_argument("--profiles-root", default=str(PROJECT_ROOT / "agents" / "hermes" / "profiles"))
    parser.add_argument("--manifest", default=str(PROJECT_ROOT / "agents" / "hermes" / "profiles" / "manifest.json"))
    parser.add_argument("--profiles", default="", help="Comma-separated profile IDs. Defaults to all manifest profiles.")
    parser.add_argument("--collection", default=os.getenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", DEFAULT_COLLECTION))
    parser.add_argument("--embed-url", default="")
    parser.add_argument("--embed-model", default=os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_MODEL") or os.getenv("SIQ_EMBEDDING_MODEL") or DEFAULT_EMBED_MODEL)
    parser.add_argument("--vector-dim", type=int, default=int(os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_DIM", str(DEFAULT_VECTOR_DIM))))
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--flush", action="store_true", help="Call Milvus flush after all batches. Slower, but useful before immediate offline verification.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--require-configured-embed-url",
        action="store_true",
        help="Fail before embedding if no explicit embedding endpoint env/CLI value is configured.",
    )
    parser.add_argument("--output", default="", help="Optional JSON summary output path.")
    parser.add_argument("--markdown", default="", help="Optional Markdown summary output path.")
    return parser.parse_args(argv)


def write_summary(args: argparse.Namespace, summary: dict[str, Any]) -> None:
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown:
        markdown_path = Path(args.markdown)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def render_markdown(summary: dict[str, Any]) -> str:
    profile_filter = summary.get("profile_filter")
    if isinstance(profile_filter, list):
        profile_display = ", ".join(str(item) for item in profile_filter)
    else:
        profile_display = str(profile_filter)
    lines = [
        "# SIQ Agent Memory Milvus Seed",
        "",
        f"- Status: **{'PASS' if summary.get('passed') else 'FAIL'}**",
        f"- Collection: `{summary.get('collection')}`",
        f"- Profiles: `{profile_display}`",
        f"- Chunks planned: `{summary.get('chunk_count')}`",
        f"- Inserted: `{summary.get('inserted', 0)}`",
        f"- Dry run: `{summary.get('dry_run')}`",
    ]
    if summary.get("error_type"):
        lines.append(f"- Error type: `{summary.get('error_type')}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.environ["SIQ_AGENT_MEMORY_VECTOR_BACKEND"] = "milvus"
    os.environ["SIQ_AGENT_MEMORY_MILVUS_COLLECTION"] = args.collection
    os.environ["SIQ_AGENT_MEMORY_EMBEDDING_DIM"] = str(args.vector_dim)

    profiles = {item.strip() for item in args.profiles.split(",") if item.strip()} or None
    manifest = load_manifest(Path(args.manifest))
    items = iter_profile_files(Path(args.profiles_root), manifest, profiles)
    summary = {
        "schema_version": "siq_agent_memory_ingest_summary_v1",
        "passed": True,
        "collection": args.collection,
        "profiles_root": repo_relative(Path(args.profiles_root)),
        "manifest": repo_relative(Path(args.manifest)),
        "profile_filter": sorted(profiles) if profiles else "all",
        "chunk_count": len(items),
        "dry_run": bool(args.dry_run),
        "embedding_endpoint_configured": embedding_endpoint_configured(args),
        "requires_configured_embedding_endpoint": bool(args.require_configured_embed_url),
        "embed_model": args.embed_model,
    }
    if args.require_configured_embed_url and not summary["embedding_endpoint_configured"]:
        write_summary(
            args,
            {
                **summary,
                "passed": False,
                "inserted": 0,
                "error_type": "embedding_endpoint_not_configured",
            },
        )
        return 1
    if args.dry_run:
        write_summary(args, {**summary, "inserted": 0})
        return 0
    if not items:
        write_summary(args, {**summary, "inserted": 0})
        return 0

    endpoint = embedding_endpoint(args)
    batch_size = max(1, min(int(args.batch_size), 64))
    inserted = 0
    try:
        for offset in range(0, len(items), batch_size):
            batch = items[offset : offset + batch_size]
            batch_no = offset // batch_size + 1
            batch_total = (len(items) + batch_size - 1) // batch_size
            print(f"embedding/upserting batch {batch_no}/{batch_total} ({len(batch)} chunks)", flush=True)
            vectors = embed_batch(
                [item["content"] for item in batch],
                endpoint=endpoint,
                model=args.embed_model,
                timeout=args.timeout,
            )
            records = to_vector_records(batch, vectors)
            inserted += agent_memory_milvus.upsert_records(records, flush=False)
        if args.flush:
            agent_memory_milvus.flush_collection()
    except Exception as exc:
        write_summary(
            args,
            {
                **summary,
                "passed": False,
                "inserted": inserted,
                "error_type": type(exc).__name__,
            },
        )
        return 1

    write_summary(args, {**summary, "inserted": inserted})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
