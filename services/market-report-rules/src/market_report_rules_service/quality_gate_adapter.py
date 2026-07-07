from __future__ import annotations

from pathlib import Path
from typing import Any

from .evidence_package import build_quality_gates
from .models import CheckStatus, ValidationCheck, ValidationResult


DECISION_TO_STATUS = {
    "allow": CheckStatus.PASS,
    "review": CheckStatus.WARNING,
    "block": CheckStatus.FAIL,
}


def apply_package_quality_gates(validation: ValidationResult, *, package_dir: str | Path | None) -> ValidationResult:
    if package_dir is None or str(package_dir).strip() == "":
        return validation
    gates = build_quality_gates(Path(package_dir))
    if not isinstance(gates, dict):
        return validation
    decisions = gates.get("decisions_by_target") if isinstance(gates.get("decisions_by_target"), dict) else {}
    canonical = decisions.get("canonical") if isinstance(decisions.get("canonical"), dict) else {}
    canonical_decision = str(canonical.get("decision") or gates.get("canonical_decision") or gates.get("decision") or "allow")
    status = DECISION_TO_STATUS.get(canonical_decision, CheckStatus.FAIL)
    gate_results = gates.get("gate_results") if isinstance(gates.get("gate_results"), list) else []
    canonical_gate = next(
        (gate for gate in gate_results if isinstance(gate, dict) and gate.get("target") == "canonical"),
        canonical if canonical else {},
    )
    check = ValidationCheck(
        rule_id="package.quality_gates",
        rule_name="Package quality gates",
        statement_type="document",
        status=status,
        inputs=["package_dir"],
        left={
            "package_dir": str(package_dir),
            "overall_status": gates.get("overall_status"),
            "evidence_resolvability_ratio": gates.get("evidence_resolvability_ratio"),
            "unresolvable_evidence_count": gates.get("unresolvable_evidence_count"),
        },
        right={"canonical_decision": canonical_decision},
        reason=f"package_quality_gate_{canonical_decision}",
        raw={
            "gate_contract_version": gates.get("gate_contract_version"),
            "gate_results": gate_results,
            "gate_decisions_by_target": decisions,
            "gate": canonical_gate,
            "quality_gates": gates,
            "evidence_resolvability_ratio": gates.get("evidence_resolvability_ratio"),
            "resolvable_evidence_count": gates.get("resolvable_evidence_count"),
            "unresolvable_evidence_count": gates.get("unresolvable_evidence_count"),
            "unresolvable_evidence": gates.get("unresolvable_evidence"),
        },
    )
    checks = [*validation.checks, check]
    summary = dict(validation.summary)
    summary[status.value] = summary.get(status.value, 0) + 1
    overall = validation.overall_status
    if status == CheckStatus.FAIL:
        overall = CheckStatus.FAIL
    elif status == CheckStatus.WARNING and overall != CheckStatus.FAIL:
        overall = CheckStatus.WARNING
    return validation.model_copy(update={"checks": checks, "summary": summary, "overall_status": overall})
