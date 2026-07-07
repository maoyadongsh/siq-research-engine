#!/usr/bin/env python3
"""Run or prepare SIQ analysis research subagents.

This runner is the execution seam between the deterministic report pipeline and
tool/LLM-driven specialist agents. It keeps the existing research_pack contract:
all usable packs must land in <work_dir>/research_packs/*.json before validation
and merge.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROFILE_DIR = SCRIPT_DIR.parent
REPO_ROOT = PROFILE_DIR.parents[3]
SUBAGENTS_DIR = PROFILE_DIR / "subagents"
RESEARCH_PACK_SCHEMA = PROFILE_DIR / "templates" / "research_pack.schema.json"
GENERATE_RESEARCH_PACKS_SCRIPT = SCRIPT_DIR / "generate_research_packs.py"
VALIDATE_RESEARCH_PACKS_SCRIPT = SCRIPT_DIR / "validate_research_packs.py"

REQUIRED_RESEARCH_AGENT_IDS = [
    "evidence_curator",
    "financial_modeler",
    "business_strategy_researcher",
    "industry_peer_researcher",
    "governance_risk_researcher",
]

OPTIONAL_AGENT_IDS = ["editor_in_chief"]
ALLOWED_AGENT_IDS = [*REQUIRED_RESEARCH_AGENT_IDS, *OPTIONAL_AGENT_IDS]

CHECKPOINT_FILES = [
    "preflight.json",
    "wiki_inventory.json",
    "metric_snapshot.json",
    "evidence_package.json",
    "analysis_outline.json",
    "peer_metrics.json",
    "qualitative_snapshot.json",
    "market_snapshot.json",
    "industry_research.json",
]

MARKET_BENCHMARK_SEARCH_ROOTS = [
    {
        "market": "A",
        "root": "data/wiki/companies",
        "purpose": "A 股目标公司与严格同业样本",
    },
    {
        "market": "JP",
        "root": "data/wiki/jp/companies",
        "purpose": "日本市场公司 wiki，可按用户提示词检索全球标杆",
    },
    {
        "market": "KR",
        "root": "data/wiki/kr/companies",
        "purpose": "韩国市场公司 wiki，可按用户提示词检索全球标杆",
    },
    {
        "market": "downloads",
        "root": "data/market-report-finder",
        "purpose": "多市场下载 manifest 与原始 PDF 元数据补充",
    },
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


SENSITIVE_CMD_VALUE_FLAGS = {
    "--api-key",
    "--auth-token",
    "--bearer",
    "--benchmark-hint",
    "--password",
    "--research-benchmark-hint",
    "--research-prompt",
    "--research-subagent-prompt",
    "--secret",
    "--token",
}

SENSITIVE_CMD_VALUE_PREFIXES = tuple(f"{flag}=" for flag in sorted(SENSITIVE_CMD_VALUE_FLAGS))


def redact_cmd(cmd: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for raw_part in cmd:
        part = str(raw_part)
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if part in SENSITIVE_CMD_VALUE_FLAGS:
            redacted.append(part)
            redact_next = True
            continue
        matched_prefix = next((prefix for prefix in SENSITIVE_CMD_VALUE_PREFIXES if part.startswith(prefix)), None)
        if matched_prefix:
            redacted.append(f"{matched_prefix}<redacted>")
        else:
            redacted.append(part)
    return redacted


def run_json(cmd: list[str]) -> dict[str, Any]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    payload: Any = None
    stdout = result.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
    return {
        "cmd": redact_cmd(cmd),
        "returncode": result.returncode,
        "stdout": stdout[-4000:],
        "stderr": result.stderr.strip()[-4000:],
        "json": payload,
        "ok": result.returncode == 0,
    }


def path_availability(path_text: str) -> dict[str, str]:
    path = REPO_ROOT / path_text
    return {
        "path": path_text,
        "status": "available" if path.exists() else "missing",
    }


def load_research_prompt(prompt_text: str | None, prompt_file: Path | None) -> str:
    parts: list[str] = []
    if prompt_text and prompt_text.strip():
        parts.append(prompt_text.strip())
    if prompt_file:
        try:
            file_text = prompt_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"research prompt file unreadable: {prompt_file}: {exc}") from exc
        if file_text.strip():
            parts.append(file_text.strip())
    return "\n\n".join(parts)


def build_benchmark_research_context(research_prompt: str, benchmark_hints: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "mode": "prompt_driven_query",
        "research_prompt": research_prompt,
        "benchmark_hints": [hint for hint in benchmark_hints if hint.strip()],
        "search_roots": [
            {
                **root,
                **path_availability(root["root"]),
            }
            for root in MARKET_BENCHMARK_SEARCH_ROOTS
        ],
        "query_policy": [
            "不得在脚本层硬编码公司、市场或检索词；由子智能体从 research_prompt/benchmark_hints 中提取查询对象。",
            "分析事实底座以本地 wiki、年报、metrics、evidence 和 semantic 证据为主；Tavily/EXA 只能作为行业上下文、外部补证、技术路线和跨市场参考补充。",
            "外部补充可以充分展开，但不得覆盖公司年报事实；若外部来源与本地证据冲突，只能写入 missing_inputs/review_required 或 risk_chains 触发复核。",
            "若提示词提到日本、韩国或全球汽车标杆，先检索本地多市场 wiki，再决定是否需要 Tavily/EXA 补充。",
            "A 股同业分位、peer_count、估值均值/中位数只能使用 A 股严格同业样本。",
            "海外市场公司仅作为 cross_market_reference，必须披露市场、币种、会计准则、期间和可比性限制。",
        ],
        "suggested_local_discovery": [
            "从提示词抽取公司名、英文名、股票代码或市场词，再用 rg/文件索引在 search_roots 中查找 company.json、manifest.json、normalized_metrics.json 和 sections/report.md。",
            "如果本地 wiki 找不到提示词对象，再使用 Hermes web search 工具补充来源，并在 external_sources 写 provider/query/url/title。",
        ],
        "output_policy": [
            "把检索到的全球标杆写成 key_findings 或 evidence_facts 时，section_ids 优先绑定 industry_competition、strategy_policy_external_risk、risk_chain_scenario。",
            "key_findings 应先连接本地证据事实和财务变量，再使用外部搜索补强行业解释、技术/专利/政策背景或跨市场参照。",
            "不得把海外标杆公司的数值直接混入目标公司事实或 A 股同业分位。",
        ],
    }


def checkpoint_inputs(work_dir: Path) -> list[dict[str, str]]:
    inputs: list[dict[str, str]] = []
    for name in CHECKPOINT_FILES:
        path = work_dir / name
        inputs.append({
            "path": str(path),
            "status": "read" if path.exists() else "missing",
            "purpose": "siq_analysis report checkpoint",
        })
    return inputs


def load_prompt(agent_id: str) -> tuple[str, str | None]:
    prompt_path = SUBAGENTS_DIR / f"{agent_id}.md"
    if not prompt_path.exists():
        return str(prompt_path), None
    return str(prompt_path), prompt_path.read_text(encoding="utf-8")


def build_prompt_bundle(
    work_dir: Path,
    output_dir: Path,
    year: int,
    prompt_bundle_path: Path,
    research_prompt: str,
    benchmark_hints: list[str],
) -> dict[str, Any]:
    benchmark_research_context = build_benchmark_research_context(research_prompt, benchmark_hints)
    agents: list[dict[str, Any]] = []
    for agent_id in ALLOWED_AGENT_IDS:
        prompt_file, instructions = load_prompt(agent_id)
        agent_payload: dict[str, Any] = {
            "agent_id": agent_id,
            "required_for_validation": agent_id in REQUIRED_RESEARCH_AGENT_IDS,
            "prompt_file": prompt_file,
            "prompt_status": "read" if instructions else "missing",
            "instructions": instructions,
            "output_file": str(output_dir / f"{agent_id}.json"),
            "schema_path": str(RESEARCH_PACK_SCHEMA),
            "checkpoint_inputs": checkpoint_inputs(work_dir),
        }
        if agent_id == "industry_peer_researcher":
            agent_payload["benchmark_research_context"] = benchmark_research_context
        agents.append(agent_payload)
    bundle = {
        "schema_version": "1.0",
        "generated_by": "run_research_subagents.py",
        "generated_at": now_iso(),
        "work_dir": str(work_dir),
        "report_year": year,
        "research_packs_dir": str(output_dir),
        "research_pack_schema": str(RESEARCH_PACK_SCHEMA),
        "required_research_agent_ids": REQUIRED_RESEARCH_AGENT_IDS,
        "optional_agent_ids": OPTIONAL_AGENT_IDS,
        "research_prompt": research_prompt,
        "benchmark_research_context": benchmark_research_context,
        "agents": agents,
    }
    dump_json(prompt_bundle_path, bundle)
    return bundle


def clear_known_pack_files(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for agent_id in ALLOWED_AGENT_IDS:
        path = output_dir / f"{agent_id}.json"
        if path.exists():
            path.unlink()


def load_pack_agent_id(path: Path) -> tuple[str | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"read_failed:{path}:{exc}"
    except json.JSONDecodeError as exc:
        return None, f"json_parse_failed:{path}:{exc.msg}:line_{exc.lineno}"
    if not isinstance(data, dict):
        return None, f"json_root_not_object:{path}"
    agent_id = data.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None, f"agent_id_missing:{path}"
    if agent_id not in ALLOWED_AGENT_IDS:
        return None, f"agent_id_invalid:{path}:{agent_id}"
    return agent_id, None


def copy_external_packs(external_pack_dir: Path, output_dir: Path) -> tuple[dict[str, str], list[str], list[str]]:
    copied: dict[str, str] = {}
    failures: list[str] = []
    warnings: list[str] = []
    if not external_pack_dir.exists():
        return copied, [f"external_pack_dir_missing:{external_pack_dir}"], warnings
    if not external_pack_dir.is_dir():
        return copied, [f"external_pack_dir_not_directory:{external_pack_dir}"], warnings
    for path in sorted(external_pack_dir.glob("*.json")):
        agent_id, error = load_pack_agent_id(path)
        if error:
            failures.append(error)
            continue
        assert agent_id is not None
        if agent_id in copied:
            failures.append(f"duplicate_external_agent_pack:{agent_id}:{path}")
            continue
        target = output_dir / f"{agent_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied[agent_id] = str(path)
    if not copied:
        warnings.append(f"external_pack_json_missing:{external_pack_dir}/*.json")
    return copied, failures, warnings


def generate_deterministic_packs(work_dir: Path, output_dir: Path, year: int, manifest_path: Path) -> dict[str, Any]:
    return run_json([
        sys.executable,
        str(GENERATE_RESEARCH_PACKS_SCRIPT),
        "--work-dir",
        str(work_dir),
        "--year",
        str(year),
        "--output-dir",
        str(output_dir),
        "--write-manifest",
        str(manifest_path),
    ])


def copy_missing_from_deterministic(
    work_dir: Path,
    output_dir: Path,
    year: int,
    missing_agent_ids: list[str],
) -> tuple[dict[str, str], dict[str, Any]]:
    if not missing_agent_ids:
        return {}, {"ok": True, "stage": "not_needed"}
    with tempfile.TemporaryDirectory(prefix="siq_research_pack_fallback_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        temp_output_dir = temp_dir / "research_packs"
        temp_manifest = temp_dir / "research_pack_manifest.json"
        generated = generate_deterministic_packs(work_dir, temp_output_dir, year, temp_manifest)
        payload = generated.get("json") if isinstance(generated.get("json"), dict) else {}
        copied: dict[str, str] = {}
        if generated["ok"] and payload.get("ok"):
            for agent_id in missing_agent_ids:
                source = temp_output_dir / f"{agent_id}.json"
                if source.exists():
                    target = output_dir / f"{agent_id}.json"
                    shutil.copy2(source, target)
                    copied[agent_id] = str(source)
        return copied, generated


def pack_sources_from_dir(output_dir: Path, source_by_agent: dict[str, str]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for agent_id in ALLOWED_AGENT_IDS:
        if (output_dir / f"{agent_id}.json").exists():
            sources[agent_id] = source_by_agent.get(agent_id, "unknown")
    return sources


def build_pack_manifest(
    work_dir: Path,
    output_dir: Path,
    execution_mode: str,
    prompt_bundle_path: Path,
    source_by_agent: dict[str, str],
) -> dict[str, Any]:
    packs: list[dict[str, Any]] = []
    for agent_id in ALLOWED_AGENT_IDS:
        path = output_dir / f"{agent_id}.json"
        data = load_json_if_exists(path)
        if data:
            packs.append(data)
    return {
        "schema_version": 1,
        "generated_by": "run_research_subagents.py",
        "generated_at": now_iso(),
        "work_dir": str(work_dir),
        "research_packs_dir": str(output_dir),
        "implementation_stage": "subagent_runner",
        "execution_mode": execution_mode,
        "prompt_bundle": str(prompt_bundle_path),
        "agent_ids": [pack.get("agent_id") for pack in packs if pack.get("agent_id")],
        "required_research_agent_ids": REQUIRED_RESEARCH_AGENT_IDS,
        "optional_agent_ids": OPTIONAL_AGENT_IDS,
        "pack_files": {
            str(pack["agent_id"]): str(output_dir / f"{pack['agent_id']}.json")
            for pack in packs
            if pack.get("agent_id")
        },
        "pack_sources": pack_sources_from_dir(output_dir, source_by_agent),
        "review_required_agent_ids": [
            str(pack["agent_id"])
            for pack in packs
            if pack.get("agent_id") and pack.get("review_required")
        ],
        "missing_input_count": sum(
            len(pack.get("missing_inputs", []))
            for pack in packs
            if isinstance(pack.get("missing_inputs"), list)
        ),
    }


def normalized_source_name(source: str) -> str:
    if source.startswith("external:"):
        return "external"
    return source


def count_values(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def benchmark_context_metrics(prompt_bundle: dict[str, Any]) -> dict[str, int]:
    context = prompt_bundle.get("benchmark_research_context")
    if not isinstance(context, dict):
        return {"benchmark_hint_count": 0, "search_root_count": 0, "research_prompt_chars": 0}
    hints = context.get("benchmark_hints")
    roots = context.get("search_roots")
    prompt = context.get("research_prompt")
    return {
        "benchmark_hint_count": len(hints) if isinstance(hints, list) else 0,
        "search_root_count": len(roots) if isinstance(roots, list) else 0,
        "research_prompt_chars": len(prompt) if isinstance(prompt, str) else 0,
    }


def build_prompt_only_metrics(prompt_bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "required_agent_count": len(REQUIRED_RESEARCH_AGENT_IDS),
        "prompt_agent_count": len(prompt_bundle.get("agents", [])) if isinstance(prompt_bundle.get("agents"), list) else 0,
        **benchmark_context_metrics(prompt_bundle),
    }


def build_run_metrics(
    pack_manifest: dict[str, Any],
    validation_payload: dict[str, Any],
    failures: list[str],
    warnings: list[str],
    fallback_used_agent_ids: list[str],
    prompt_bundle: dict[str, Any],
) -> dict[str, Any]:
    pack_sources = pack_manifest.get("pack_sources")
    pack_sources = pack_sources if isinstance(pack_sources, dict) else {}
    validation_metrics = validation_payload.get("metrics")
    validation_metrics = validation_metrics if isinstance(validation_metrics, dict) else {}
    return {
        "required_agent_count": len(REQUIRED_RESEARCH_AGENT_IDS),
        "present_required_agent_count": sum(1 for agent_id in REQUIRED_RESEARCH_AGENT_IDS if agent_id in pack_sources),
        "pack_count": len(pack_sources),
        "pack_source_counts": count_values([normalized_source_name(str(source)) for source in pack_sources.values()]),
        "fallback_used_count": len(fallback_used_agent_ids),
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "missing_input_count": pack_manifest.get("missing_input_count", 0),
        "validation_ok": bool(validation_payload.get("ok")) if validation_payload else False,
        "validation_pack_count": validation_metrics.get("pack_count"),
        **benchmark_context_metrics(prompt_bundle),
    }


def validate_packs(work_dir: Path) -> dict[str, Any]:
    return run_json([sys.executable, str(VALIDATE_RESEARCH_PACKS_SCRIPT), str(work_dir)])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run siq_analysis research subagent pack preparation.")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument(
        "--mode",
        choices=["deterministic", "external", "hybrid", "prompt-only"],
        default="deterministic",
        help="deterministic keeps existing behavior; external copies packs; hybrid copies then fills missing; prompt-only writes prompts.",
    )
    parser.add_argument("--output-dir", type=Path, help="Default: <work-dir>/research_packs")
    parser.add_argument("--external-pack-dir", type=Path)
    parser.add_argument("--write-manifest", type=Path, help="Default: <work-dir>/research_pack_manifest.json")
    parser.add_argument("--write-run-manifest", type=Path, help="Default: <work-dir>/research_subagent_run_manifest.json")
    parser.add_argument("--prompt-bundle", type=Path, help="Default: <work-dir>/research_subagent_prompts.json")
    parser.add_argument("--research-prompt", default="", help="User/task prompt that subagents may use to derive extra queries.")
    parser.add_argument("--research-prompt-file", type=Path, help="Read additional user/task prompt text from a file.")
    parser.add_argument(
        "--benchmark-hint",
        action="append",
        default=[],
        help="Optional prompt-derived benchmark hint. May be repeated; runner does not hardcode benchmark queries.",
    )
    parser.add_argument("--no-fallback", action="store_true", help="Do not fill missing external packs deterministically.")
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    started_at = now_iso()
    started_perf = time.perf_counter()
    args = parse_args(argv or sys.argv[1:])
    work_dir = args.work_dir
    output_dir = args.output_dir or work_dir / "research_packs"
    manifest_path = args.write_manifest or work_dir / "research_pack_manifest.json"
    run_manifest_path = args.write_run_manifest or work_dir / "research_subagent_run_manifest.json"
    prompt_bundle_path = args.prompt_bundle or work_dir / "research_subagent_prompts.json"

    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    research_prompt = load_research_prompt(args.research_prompt, args.research_prompt_file)
    prompt_bundle = build_prompt_bundle(
        work_dir,
        output_dir,
        args.year,
        prompt_bundle_path,
        research_prompt,
        args.benchmark_hint,
    )

    failures: list[str] = []
    warnings: list[str] = []
    source_by_agent: dict[str, str] = {}
    fallback_used_agent_ids: list[str] = []
    steps: dict[str, Any] = {}

    if args.mode == "prompt-only":
        completed_at = now_iso()
        result = {
            "ok": True,
            "stage": "prompt_bundle_ready",
            "mode": args.mode,
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_ms": round((time.perf_counter() - started_perf) * 1000),
            "work_dir": str(work_dir),
            "research_packs_dir": str(output_dir),
            "prompt_bundle": str(prompt_bundle_path),
            "run_manifest": str(run_manifest_path),
            "required_research_agent_ids": REQUIRED_RESEARCH_AGENT_IDS,
            "metrics": build_prompt_only_metrics(prompt_bundle),
            "benchmark_research_context": prompt_bundle.get("benchmark_research_context", {}),
            "next_action": "让 Hermes/LLM 子智能体按 prompt bundle 写入 research_packs 后，使用 external 或 hybrid 模式继续。",
        }
        dump_json(run_manifest_path, result)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2))
        return 0

    clear_known_pack_files(output_dir)

    if args.mode == "deterministic":
        generated = generate_deterministic_packs(work_dir, output_dir, args.year, manifest_path)
        steps["deterministic_generation"] = generated
        payload = generated.get("json") if isinstance(generated.get("json"), dict) else {}
        if not generated["ok"] or not payload.get("ok"):
            failures.append("deterministic_generation_failed")
        else:
            source_by_agent.update({agent_id: "deterministic" for agent_id in REQUIRED_RESEARCH_AGENT_IDS})

    if args.mode in {"external", "hybrid"}:
        if not args.external_pack_dir:
            if args.mode == "external" or args.no_fallback:
                failures.append("external_pack_dir_required")
            else:
                warnings.append("external_pack_dir_missing:hybrid_will_use_deterministic_fallback")
        else:
            copied, copy_failures, copy_warnings = copy_external_packs(args.external_pack_dir, output_dir)
            source_by_agent.update({agent_id: f"external:{source}" for agent_id, source in copied.items()})
            failures.extend(copy_failures)
            warnings.extend(copy_warnings)
            steps["copy_external_packs"] = {
                "ok": not copy_failures,
                "external_pack_dir": str(args.external_pack_dir),
                "copied_agent_ids": sorted(copied),
                "failures": copy_failures,
                "warnings": copy_warnings,
            }

        present_required = {
            agent_id
            for agent_id in REQUIRED_RESEARCH_AGENT_IDS
            if (output_dir / f"{agent_id}.json").exists()
        }
        missing_required = [
            agent_id
            for agent_id in REQUIRED_RESEARCH_AGENT_IDS
            if agent_id not in present_required
        ]
        if args.mode == "hybrid" and missing_required and not args.no_fallback:
            fallback_copied, fallback_step = copy_missing_from_deterministic(
                work_dir,
                output_dir,
                args.year,
                missing_required,
            )
            steps["deterministic_fallback"] = fallback_step
            fallback_used_agent_ids = sorted(fallback_copied)
            source_by_agent.update({agent_id: "deterministic_fallback" for agent_id in fallback_copied})
            still_missing = [
                agent_id
                for agent_id in missing_required
                if not (output_dir / f"{agent_id}.json").exists()
            ]
            if still_missing:
                failures.append(f"fallback_missing_required_packs:{','.join(still_missing)}")

    pack_manifest = build_pack_manifest(work_dir, output_dir, args.mode, prompt_bundle_path, source_by_agent)
    dump_json(manifest_path, pack_manifest)

    validation_step = validate_packs(work_dir)
    steps["validate_research_packs"] = validation_step
    validation_payload = validation_step.get("json") if isinstance(validation_step.get("json"), dict) else {}
    if not validation_step["ok"] or not validation_payload.get("ok"):
        failures.append("research_pack_validation_failed")

    completed_at = now_iso()
    result = {
        "ok": not failures,
        "stage": "completed" if not failures else "failed",
        "mode": args.mode,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_ms": round((time.perf_counter() - started_perf) * 1000),
        "work_dir": str(work_dir),
        "research_packs_dir": str(output_dir),
        "manifest": str(manifest_path),
        "run_manifest": str(run_manifest_path),
        "prompt_bundle": str(prompt_bundle_path),
        "pack_sources": pack_manifest.get("pack_sources", {}),
        "fallback_used_agent_ids": fallback_used_agent_ids,
        "metrics": build_run_metrics(
            pack_manifest,
            validation_payload,
            failures,
            warnings,
            fallback_used_agent_ids,
            prompt_bundle,
        ),
        "benchmark_research_context": prompt_bundle.get("benchmark_research_context", {}),
        "validation": validation_payload,
        "failures": failures,
        "warnings": warnings,
        "steps": steps,
    }
    dump_json(run_manifest_path, result)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
