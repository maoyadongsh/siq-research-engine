#!/usr/bin/env python3
"""Export the API's authoritative IC JSON Schemas for Hermes profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import ic_report_contracts, ic_task_contracts  # noqa: E402

SCHEMAS = {
    ic_task_contracts.IC_AGENT_TASK_SCHEMA: ic_task_contracts.IC_AGENT_TASK_JSON_SCHEMA,
    ic_task_contracts.IC_AGENT_HANDOFF_SCHEMA: ic_task_contracts.IC_AGENT_HANDOFF_JSON_SCHEMA,
    ic_task_contracts.IC_WORKFLOW_RUN_IDENTITY_SCHEMA: ic_task_contracts.IC_WORKFLOW_RUN_IDENTITY_JSON_SCHEMA,
    ic_task_contracts.IC_WORKFLOW_RUN_SCHEMA: ic_task_contracts.IC_WORKFLOW_RUN_JSON_SCHEMA,
    ic_report_contracts.IC_CLAIM_SCHEMA: ic_report_contracts.IC_CLAIM_JSON_SCHEMA,
    ic_report_contracts.IC_EXPERT_REPORT_SCHEMA: ic_report_contracts.IC_EXPERT_REPORT_JSON_SCHEMA,
    ic_report_contracts.IC_R0_READINESS_SCHEMA: ic_report_contracts.R0_READINESS_JSON_SCHEMA,
    ic_report_contracts.IC_R1_5_DISPUTE_SCHEMA: ic_report_contracts.R1_5_DISPUTE_JSON_SCHEMA,
    ic_report_contracts.IC_R1_5_CHAIRMAN_RULINGS_SCHEMA: (
        ic_report_contracts.R1_5_CHAIRMAN_RULINGS_JSON_SCHEMA
    ),
    ic_report_contracts.IC_R2_REVISION_SCHEMA: ic_report_contracts.R2_REVISION_JSON_SCHEMA,
    ic_report_contracts.IC_R3_PLAN_SCHEMA: ic_report_contracts.R3_PLAN_JSON_SCHEMA,
    ic_report_contracts.IC_R3_DEBATE_SCHEMA: ic_report_contracts.R3_DEBATE_JSON_SCHEMA,
    ic_report_contracts.IC_R3_DEBATE_TURN_SCHEMA: ic_report_contracts.R3_DEBATE_TURN_JSON_SCHEMA,
    ic_report_contracts.IC_R3_DEBATE_VERDICT_SCHEMA: (
        ic_report_contracts.R3_DEBATE_VERDICT_JSON_SCHEMA
    ),
    ic_report_contracts.IC_R4_DECISION_SCHEMA: ic_report_contracts.R4_DECISION_JSON_SCHEMA,
}

EXPECTED_SCHEMA_IDS = frozenset(
    {
        "siq_ic_agent_handoff_v2",
        "siq_ic_agent_task_v2",
        "siq_ic_claim_v1",
        "siq_ic_expert_report_v2",
        "siq_ic_r0_readiness_v1",
        "siq_ic_r1_5_chairman_rulings_v2",
        "siq_ic_r1_5_dispute_v1",
        "siq_ic_r2_revision_v1",
        "siq_ic_r3_debate_turn_v1",
        "siq_ic_r3_debate_v1",
        "siq_ic_r3_debate_verdict_v1",
        "siq_ic_r3_plan_v1",
        "siq_ic_r4_decision_v2",
        "siq_ic_workflow_run_identity_v1",
        "siq_ic_workflow_run_v1",
    }
)


def validate_schema_registry() -> None:
    actual = frozenset(SCHEMAS)
    if actual == EXPECTED_SCHEMA_IDS:
        return
    missing = ",".join(sorted(EXPECTED_SCHEMA_IDS - actual)) or "none"
    unexpected = ",".join(sorted(actual - EXPECTED_SCHEMA_IDS)) or "none"
    raise RuntimeError(f"IC schema registry mismatch: missing={missing} unexpected={unexpected}")


def export_schemas(output_dir: Path, *, check: bool = False) -> list[Path]:
    validate_schema_registry()
    output_dir.mkdir(parents=True, exist_ok=True)
    changed: list[Path] = []
    for schema_id, schema in sorted(SCHEMAS.items()):
        path = output_dir / f"{schema_id}.schema.json"
        rendered = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != rendered:
            changed.append(path)
            if not check:
                path.write_text(rendered, encoding="utf-8")
    expected_paths = {output_dir / f"{schema_id}.schema.json" for schema_id in EXPECTED_SCHEMA_IDS}
    changed.extend(
        path
        for path in sorted(output_dir.glob("siq_ic_*.schema.json"))
        if path not in expected_paths
    )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if exported schemas are stale")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "agents" / "hermes" / "profiles" / "siq_ic_shared" / "contracts",
    )
    args = parser.parse_args()
    changed = export_schemas(args.output_dir, check=args.check)
    if args.check and changed:
        for path in changed:
            print(f"stale: {path.relative_to(PROJECT_ROOT)}")
        return 1
    print(f"exported={len(SCHEMAS)} changed={len(changed)} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
