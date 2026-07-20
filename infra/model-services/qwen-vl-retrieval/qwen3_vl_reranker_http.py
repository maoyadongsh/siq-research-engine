#!/usr/bin/env python3
"""Local HTTP wrapper for Qwen3-VL-Reranker-2B via vLLM LLM.score()."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from vllm import EngineArgs, LLM


MODEL_PATH = Path(os.environ.get("RERANKER_MODEL_PATH", "/model"))
DEFAULT_INSTRUCTION = os.environ.get(
    "RERANKER_DEFAULT_INSTRUCTION",
    "Given a search query, retrieve relevant candidates that answer the query.",
)
HOST = os.environ.get("RERANKER_HTTP_HOST", "0.0.0.0")
PORT = int(os.environ.get("RERANKER_HTTP_PORT", "8000"))
MAX_MODEL_LEN = int(os.environ.get("RERANKER_MAX_MODEL_LEN", "8192"))
GPU_MEMORY_UTILIZATION = float(os.environ.get("RERANKER_GPU_MEMORY_UTILIZATION", "0.10"))


class DocumentInput(BaseModel):
    text: str | None = None
    image: str | None = None


class RerankRequest(BaseModel):
    query: str
    documents: list[DocumentInput] = Field(default_factory=list)
    instruction: str = DEFAULT_INSTRUCTION
    return_sigmoid: bool = True


class RerankItem(BaseModel):
    index: int
    score: float
    document: DocumentInput


class RerankResponse(BaseModel):
    query: str
    instruction: str
    results: list[RerankItem]


class OpenAIRerankRequest(BaseModel):
    model: str | None = None
    query: str
    documents: list[DocumentInput] = Field(default_factory=list)
    top_n: int | None = None
    return_sigmoid: bool = True
    instruction: str = DEFAULT_INSTRUCTION


class OpenAIRerankItem(BaseModel):
    index: int
    relevance_score: float
    document: DocumentInput


class OpenAIRerankResponse(BaseModel):
    object: str = "list"
    model: str
    data: list[OpenAIRerankItem]


app = FastAPI(title="Qwen3-VL-Reranker HTTP Service", version="1.0.0")

_llm: LLM | None = None
_chat_template: str | None = None
_engine_healthy = False
_score_lock = threading.Lock()


def _load_chat_template() -> str:
    score_template = Path("/vllm-workspace/examples/pooling/score/template/qwen3_vl_reranker.jinja")
    if score_template.exists():
        return score_template.read_text()

    template_file = MODEL_PATH / "chat_template.jinja"
    if template_file.exists():
        return template_file.read_text()

    raise RuntimeError("No reranker chat template found.")


def _make_engine() -> LLM:
    engine_args = EngineArgs(
        model=str(MODEL_PATH),
        runner="pooling",
        dtype="bfloat16",
        trust_remote_code=True,
        enforce_eager=True,
        max_model_len=MAX_MODEL_LEN,
        gpu_memory_utilization=GPU_MEMORY_UTILIZATION,
        hf_overrides={
            "architectures": ["Qwen3VLForSequenceClassification"],
            "classifier_from_token": ["no", "yes"],
            "is_original_qwen3_reranker": True,
        },
    )
    return LLM(**vars(engine_args))


def _format_document(doc: DocumentInput) -> dict[str, Any]:
    content: list[dict[str, Any]] = []

    if doc.text:
        content.append({"type": "text", "text": doc.text})

    if doc.image:
        image_url = doc.image
        if not image_url.startswith(("http://", "https://", "oss://", "file://")):
            image_url = "file://" + str(Path(image_url).resolve())
        content.append({"type": "image_url", "image_url": {"url": image_url}})

    if not content:
        content.append({"type": "text", "text": ""})

    return {"content": content}


def _sigmoid(x: float) -> float:
    import math

    return 1.0 / (1.0 + math.exp(-x))


def _run_rerank(req: RerankRequest) -> list[RerankItem]:
    global _engine_healthy

    if _llm is None or _chat_template is None:
        raise HTTPException(status_code=503, detail="Reranker model is not loaded yet.")

    if not _engine_healthy:
        raise HTTPException(status_code=503, detail="Reranker engine is not healthy.")

    if not req.documents:
        raise HTTPException(status_code=400, detail="documents cannot be empty")

    try:
        # The offline LLM client owns one EngineCore connection and is not
        # safe for concurrent calls from FastAPI's worker threads. One 1:N
        # score call also lets vLLM batch all documents efficiently.
        with _score_lock:
            outputs = _llm.score(
                req.query,
                [_format_document(doc) for doc in req.documents],
                chat_template=_chat_template,
                use_tqdm=False,
            )
    except Exception as exc:
        _engine_healthy = False
        raise HTTPException(
            status_code=503,
            detail=f"Reranker inference failed: {type(exc).__name__}",
        ) from exc

    if len(outputs) != len(req.documents):
        _engine_healthy = False
        raise HTTPException(
            status_code=503,
            detail=(
                "Reranker returned an incomplete batch: "
                f"expected {len(req.documents)}, got {len(outputs)}"
            ),
        )

    results: list[RerankItem] = []
    for idx, (doc, output) in enumerate(zip(req.documents, outputs, strict=True)):
        score = output.outputs.score
        if req.return_sigmoid:
            score = _sigmoid(float(score))
        results.append(RerankItem(index=idx, score=float(score), document=doc))

    results.sort(key=lambda item: item.score, reverse=True)
    return results


@app.on_event("startup")
def startup_event() -> None:
    global _llm, _chat_template, _engine_healthy
    _chat_template = _load_chat_template()
    _llm = _make_engine()
    _engine_healthy = True


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": _llm is not None and _engine_healthy,
        "model": str(MODEL_PATH),
        "max_model_len": MAX_MODEL_LEN,
        "gpu_memory_utilization": GPU_MEMORY_UTILIZATION,
        "template_loaded": _chat_template is not None,
    }


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_PATH.name or str(MODEL_PATH),
                "object": "model",
                "owned_by": "local-vllm",
            }
        ],
    }


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    results = _run_rerank(req)
    return RerankResponse(query=req.query, instruction=req.instruction, results=results)


@app.post("/v1/rerank", response_model=OpenAIRerankResponse)
def openai_rerank(req: OpenAIRerankRequest) -> OpenAIRerankResponse:
    base_req = RerankRequest(
        query=req.query,
        documents=req.documents,
        instruction=req.instruction,
        return_sigmoid=req.return_sigmoid,
    )
    results = _run_rerank(base_req)
    if req.top_n is not None:
        results = results[: req.top_n]

    return OpenAIRerankResponse(
        model=req.model or (MODEL_PATH.name or str(MODEL_PATH)),
        data=[
            OpenAIRerankItem(
                index=item.index,
                relevance_score=item.score,
                document=item.document,
            )
            for item in results
        ],
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
