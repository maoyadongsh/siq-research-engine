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

GLOBAL_AUTO_BENCHMARK_CANDIDATES = [
    {
        "benchmark_id": "toyota_jp_7203",
        "role": "global_auto_benchmark",
        "market": "JP",
        "company_name": "Toyota Motor Corporation",
        "company_dir": "data/wiki/jp/companies/7203-Toyota-Motor-Corporation",
    },
    {
        "benchmark_id": "hyundai_kr_005380",
        "role": "global_auto_benchmark",
        "market": "KR",
        "company_name": "Hyundai Motor Company",
        "company_dir": "data/wiki/kr/companies/005380-HyundaiMotorCompany",
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
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": stdout[-4000:],
        "stderr": result.stderr.strip()[-4000:],
        "json": payload,
        "ok": result.returncode == 0,
    }


def rel_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_report_dir(company_dir: Path, company: dict[str, Any]) -> Path | None:
    reports = company.get("reports")
    if isinstance(reports, list) and reports:
        for report in reports:
            if not isinstance(report, dict):
                continue
            value = report.get("wiki_report_path")
            if not value:
                continue
            path = Path(str(value))
            if not path.is_absolute():
                path = REPO_ROOT / path
            if path.exists():
                return path
    reports_dir = company_dir / "reports"
    if not reports_dir.exists():
        return None
    candidates = sorted(path for path in reports_dir.iterdir() if path.is_dir())
    return candidates[-1] if candidates else None


def file_status(path: Path, purpose: str) -> dict[str, str]:
    return {
        "path": rel_to_repo(path),
        "status": "read" if path.exists() else "missing",
        "purpose": purpose,
    }


def discover_global_auto_benchmarks() -> list[dict[str, Any]]:
    benchmarks: list[dict[str, Any]] = []
    for candidate in GLOBAL_AUTO_BENCHMARK_CANDIDATES:
        company_dir = REPO_ROOT / candidate["company_dir"]
        company = load_json_if_exists(company_dir / "company.json")
        report_dir = resolve_report_dir(company_dir, company)
        manifest = load_json_if_exists(report_dir / "manifest.json") if report_dir else {}
        files: list[dict[str, str]] = [file_status(company_dir / "company.json", "company metadata")]
        if report_dir:
            files.extend([
                file_status(report_dir / "manifest.json", "report manifest"),
                file_status(report_dir / "metrics" / "normalized_metrics.json", "cross-market normalized metrics"),
                file_status(report_dir / "metrics" / "operating_metrics.json", "operating metrics if available"),
                file_status(report_dir / "sections" / "report.md", "parsed report markdown"),
                file_status(report_dir / "evidence" / "evidence_index.json", "evidence index if available"),
                file_status(report_dir / "qa" / "quality_report.json", "parser quality report"),
            ])
        benchmarks.append({
            **candidate,
            "status": "available" if company_dir.exists() and report_dir else "missing",
            "company_id": company.get("company_id") or manifest.get("company_id"),
            "ticker": company.get("ticker") or manifest.get("ticker"),
            "currency": company.get("currency") or manifest.get("currency"),
            "accounting_standard": manifest.get("accounting_standard"),
            "report_id": manifest.get("report_id"),
            "report_type": manifest.get("report_type") or manifest.get("form"),
            "fiscal_year": manifest.get("fiscal_year"),
            "period_end": manifest.get("period_end"),
            "quality_status": manifest.get("quality_status"),
            "company_dir": rel_to_repo(company_dir),
            "report_dir": rel_to_repo(report_dir) if report_dir else None,
            "files": files,
            "usage_policy": [
                "仅作为全球汽车标杆与商业模式参照，不混入 A 股严格同业分位。",
                "如引用财务数据，必须披露市场、币种、会计准则、期间和可比性限制。",
                "优先用于产品结构、全球化、成本曲线、混动/新能源路线、现金流质量等结构性比较。",
            ],
        })
    return benchmarks


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


def build_prompt_bundle(work_dir: Path, output_dir: Path, year: int, prompt_bundle_path: Path) -> dict[str, Any]:
    global_auto_benchmarks = discover_global_auto_benchmarks()
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
            agent_payload["global_auto_benchmarks"] = global_auto_benchmarks
            agent_payload["global_benchmark_policy"] = [
                "Toyota 与 Hyundai 可作为国际标杆参照。",
                "上汽集团报告的 A 股严格同业样本仍限定为本地 A 股汽车公司样本。",
                "不得把 JP/KR 标杆公司纳入 A 股 peer_count、分位数或估值中位数。",
                "国际标杆结论必须标记为 cross_market_reference，并说明可比性限制。",
            ]
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
        "global_auto_benchmarks": global_auto_benchmarks,
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
        "global_auto_benchmarks": discover_global_auto_benchmarks(),
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
    parser.add_argument("--no-fallback", action="store_true", help="Do not fill missing external packs deterministically.")
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    work_dir = args.work_dir
    output_dir = args.output_dir or work_dir / "research_packs"
    manifest_path = args.write_manifest or work_dir / "research_pack_manifest.json"
    run_manifest_path = args.write_run_manifest or work_dir / "research_subagent_run_manifest.json"
    prompt_bundle_path = args.prompt_bundle or work_dir / "research_subagent_prompts.json"

    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_bundle = build_prompt_bundle(work_dir, output_dir, args.year, prompt_bundle_path)

    failures: list[str] = []
    warnings: list[str] = []
    source_by_agent: dict[str, str] = {}
    fallback_used_agent_ids: list[str] = []
    steps: dict[str, Any] = {}

    if args.mode == "prompt-only":
        result = {
            "ok": True,
            "stage": "prompt_bundle_ready",
            "mode": args.mode,
            "work_dir": str(work_dir),
            "research_packs_dir": str(output_dir),
            "prompt_bundle": str(prompt_bundle_path),
            "run_manifest": str(run_manifest_path),
            "required_research_agent_ids": REQUIRED_RESEARCH_AGENT_IDS,
            "global_auto_benchmarks": prompt_bundle.get("global_auto_benchmarks", []),
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

    result = {
        "ok": not failures,
        "stage": "completed" if not failures else "failed",
        "mode": args.mode,
        "work_dir": str(work_dir),
        "research_packs_dir": str(output_dir),
        "manifest": str(manifest_path),
        "run_manifest": str(run_manifest_path),
        "prompt_bundle": str(prompt_bundle_path),
        "pack_sources": pack_manifest.get("pack_sources", {}),
        "fallback_used_agent_ids": fallback_used_agent_ids,
        "global_auto_benchmarks": prompt_bundle.get("global_auto_benchmarks", []),
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
