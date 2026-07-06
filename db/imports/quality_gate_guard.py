from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from market_report_rules_service.evidence_package import build_quality_gates, compute_artifact_hashes


ALLOW_DECISIONS = {"allow", "pass"}
REVIEW_DECISION = "review"
BLOCK_DECISION = "block"


@dataclass(frozen=True)
class QualityGateEnforcement:
    target: str
    decision: str
    gates: dict[str, Any]
    package_hash: str
    force_review: bool = False
    promotion_override: dict[str, Any] | None = None


def _as_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _target_payload(gates: dict[str, Any], target: str) -> dict[str, Any]:
    decisions = gates.get("decisions_by_target") if isinstance(gates, dict) else {}
    payload = decisions.get(target) if isinstance(decisions, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _decision_for_target(gates: dict[str, Any], target: str) -> str:
    direct = gates.get(f"{target}_decision") if isinstance(gates, dict) else None
    if direct not in (None, ""):
        return str(direct)
    target_decision = _target_payload(gates, target).get("decision")
    if target_decision not in (None, ""):
        return str(target_decision)
    return str(gates.get("decision") or "allow")


def _package_hash(package_dir: Path) -> str:
    artifact_hashes = compute_artifact_hashes(package_dir, include_manifest=True)
    encoded = json.dumps(artifact_hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _gate_reasons(gates: dict[str, Any], target: str) -> list[str]:
    reasons = _as_strings(gates.get("block_reasons"))
    if reasons:
        return reasons
    return _as_strings(_target_payload(gates, target).get("reasons"))


def _format_gate_failure(*, target: str, decision: str, gates: dict[str, Any], package_hash: str) -> str:
    hard_rule_ids = _as_strings(gates.get("hard_gate_rule_ids"))
    soft_rule_ids = _as_strings(gates.get("soft_gate_rule_ids"))
    target_rules = _as_strings(_target_payload(gates, target).get("rule_ids"))
    reasons = _gate_reasons(gates, target)
    parts = [
        f"Quality gate blocked {target} import",
        f"decision={decision}",
        f"package_hash={package_hash}",
    ]
    if hard_rule_ids:
        parts.append("hard_gate_rule_ids=" + ",".join(hard_rule_ids))
    if soft_rule_ids:
        parts.append("soft_gate_rule_ids=" + ",".join(soft_rule_ids))
    if target_rules:
        parts.append("target_rule_ids=" + ",".join(target_rules))
    if reasons:
        parts.append("reasons=" + "; ".join(reasons))
    return "; ".join(parts)


def _force_review_audit(
    *,
    target: str,
    gates: dict[str, Any],
    package_hash: str,
    requested_by: str,
    reason: str,
    approved_by: str | None,
    expires_at: str | None,
    created_at: str | None,
) -> dict[str, Any]:
    created = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rule_ids = _as_strings(gates.get("soft_gate_rule_ids"))
    seed = json.dumps(
        {
            "created_at": created,
            "package_hash": package_hash,
            "promotion_target": target,
            "requested_by": requested_by,
            "reason": reason,
            "rule_ids": rule_ids,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return {
        "exception_id": f"qg-force-{digest[:16]}",
        "gate_rule_id": rule_ids[0] if rule_ids else None,
        "gate_rule_ids": rule_ids,
        "package_hash": package_hash,
        "promotion_target": target,
        "requested_by": requested_by,
        "approved_by": approved_by or requested_by,
        "reason": reason,
        "expires_at": expires_at,
        "created_at": created,
        "audit_log_id": f"qg-audit-{digest}",
        "original_decision": REVIEW_DECISION,
        "override_decision": "allow",
    }


def enforce_quality_gates(
    package_dir: Path,
    *,
    target: str = "canonical",
    force_review: bool = False,
    requested_by: str | None = None,
    reason: str | None = None,
    approved_by: str | None = None,
    expires_at: str | None = None,
    created_at: str | None = None,
) -> QualityGateEnforcement:
    gates = build_quality_gates(package_dir)
    decision = _decision_for_target(gates, target)
    package_hash = _package_hash(package_dir)

    if decision == BLOCK_DECISION:
        raise SystemExit(_format_gate_failure(target=target, decision=decision, gates=gates, package_hash=package_hash))

    if decision == REVIEW_DECISION:
        hard_rule_ids = _as_strings(gates.get("hard_gate_rule_ids"))
        if hard_rule_ids:
            raise SystemExit(
                _format_gate_failure(target=target, decision=decision, gates=gates, package_hash=package_hash)
                + "; force_review_denied=hard gates cannot be forced"
            )
        if not force_review:
            raise SystemExit(
                _format_gate_failure(target=target, decision=decision, gates=gates, package_hash=package_hash)
                + "; rerun with --force-review, --force-requested-by, and --force-reason to audit a soft-gate override"
            )
        if not str(requested_by or "").strip() or not str(reason or "").strip():
            raise SystemExit("force-review requires --force-requested-by and --force-reason")
        return QualityGateEnforcement(
            target=target,
            decision=decision,
            gates=gates,
            package_hash=package_hash,
            force_review=True,
            promotion_override=_force_review_audit(
                target=target,
                gates=gates,
                package_hash=package_hash,
                requested_by=str(requested_by).strip(),
                reason=str(reason).strip(),
                approved_by=str(approved_by).strip() if str(approved_by or "").strip() else None,
                expires_at=expires_at,
                created_at=created_at,
            ),
        )

    if force_review:
        raise SystemExit(f"force-review is only valid when {target} decision is review; current decision={decision}")

    if decision not in ALLOW_DECISIONS:
        raise SystemExit(_format_gate_failure(target=target, decision=decision, gates=gates, package_hash=package_hash))

    return QualityGateEnforcement(target=target, decision=decision, gates=gates, package_hash=package_hash)


def quality_with_gate_audit(quality: dict[str, Any], enforcement: QualityGateEnforcement) -> dict[str, Any]:
    payload = dict(quality) if isinstance(quality, dict) else {}
    payload["quality_gates"] = enforcement.gates
    if enforcement.promotion_override:
        payload["promotion_override"] = enforcement.promotion_override
        overrides = payload.get("promotion_overrides")
        payload["promotion_overrides"] = [*overrides, enforcement.promotion_override] if isinstance(overrides, list) else [enforcement.promotion_override]
    return payload


def should_write_target(enforcement: QualityGateEnforcement, target: str) -> bool:
    decision = _decision_for_target(enforcement.gates, target)
    if decision in ALLOW_DECISIONS:
        return True
    return bool(enforcement.promotion_override and target == enforcement.target and decision == REVIEW_DECISION)
