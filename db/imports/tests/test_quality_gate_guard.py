import importlib.util
from pathlib import Path

import pytest


def _load_guard():
    path = Path(__file__).resolve().parents[1] / "quality_gate_guard.py"
    spec = importlib.util.spec_from_file_location("quality_gate_guard", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _package_dir(tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return package_dir


def _gates(decision, *, hard=None, soft=None, reasons=None, retrieval_decision=None):
    hard = hard or []
    soft = soft or []
    reasons = reasons or []
    return {
        "canonical_decision": decision,
        "retrieval_decision": retrieval_decision or ("review" if decision == "review" else "allow"),
        "decisions_by_target": {
            "canonical": {
                "decision": decision,
                "rule_ids": [*hard, *soft],
                "reasons": reasons,
            },
            "retrieval": {
                "decision": retrieval_decision or ("review" if decision == "review" else "allow"),
                "rule_ids": [*hard, *soft],
                "reasons": reasons,
            },
        },
        "hard_gate_rule_ids": hard,
        "soft_gate_rule_ids": soft,
        "block_reasons": reasons,
    }


def test_quality_gate_guard_blocks_canonical_with_rule_details(tmp_path, monkeypatch):
    guard = _load_guard()
    monkeypatch.setattr(
        guard,
        "build_quality_gates",
        lambda package_dir: _gates(
            "block",
            hard=["package.quality_status.fail"],
            reasons=["quality status is fail"],
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        guard.enforce_quality_gates(_package_dir(tmp_path))

    message = str(excinfo.value)
    assert "Quality gate blocked canonical import" in message
    assert "decision=block" in message
    assert "package.quality_status.fail" in message
    assert "quality status is fail" in message


def test_quality_gate_guard_review_requires_force(tmp_path, monkeypatch):
    guard = _load_guard()
    monkeypatch.setattr(
        guard,
        "build_quality_gates",
        lambda package_dir: _gates(
            "review",
            soft=["package.parser_or_rule_warnings.present"],
            reasons=["parser or rule warnings present"],
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        guard.enforce_quality_gates(_package_dir(tmp_path))

    message = str(excinfo.value)
    assert "decision=review" in message
    assert "package.parser_or_rule_warnings.present" in message
    assert "--force-review" in message


def test_quality_gate_guard_force_review_requires_audit_fields(tmp_path, monkeypatch):
    guard = _load_guard()
    monkeypatch.setattr(
        guard,
        "build_quality_gates",
        lambda package_dir: _gates("review", soft=["package.quality_status.warning"]),
    )

    with pytest.raises(SystemExit) as excinfo:
        guard.enforce_quality_gates(_package_dir(tmp_path), force_review=True, requested_by="analyst@example.com")

    assert "--force-reason" in str(excinfo.value)


def test_quality_gate_guard_force_review_records_audit_and_skips_retrieval(tmp_path, monkeypatch):
    guard = _load_guard()
    monkeypatch.setattr(
        guard,
        "build_quality_gates",
        lambda package_dir: _gates(
            "review",
            soft=["package.parser_or_rule_warnings.present"],
            reasons=["parser or rule warnings present"],
        ),
    )

    enforcement = guard.enforce_quality_gates(
        _package_dir(tmp_path),
        force_review=True,
        requested_by="analyst@example.com",
        approved_by="lead@example.com",
        reason="Manual review confirmed values against source pages.",
        expires_at="2026-07-13T00:00:00Z",
        created_at="2026-07-06T12:00:00Z",
    )
    quality = guard.quality_with_gate_audit({"overall_status": "warning"}, enforcement)

    assert guard.should_write_target(enforcement, "canonical") is True
    assert guard.should_write_target(enforcement, "retrieval") is False
    audit = quality["promotion_override"]
    assert audit["gate_rule_ids"] == ["package.parser_or_rule_warnings.present"]
    assert audit["requested_by"] == "analyst@example.com"
    assert audit["approved_by"] == "lead@example.com"
    assert audit["reason"].startswith("Manual review")
    assert audit["expires_at"] == "2026-07-13T00:00:00Z"
    assert audit["package_hash"]
    assert audit["audit_log_id"].startswith("qg-audit-")


def test_quality_gate_guard_rejects_force_when_hard_gate_present(tmp_path, monkeypatch):
    guard = _load_guard()
    monkeypatch.setattr(
        guard,
        "build_quality_gates",
        lambda package_dir: _gates(
            "review",
            hard=["package.evidence.unresolvable"],
            reasons=["unresolvable evidence present"],
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        guard.enforce_quality_gates(
            _package_dir(tmp_path),
            force_review=True,
            requested_by="analyst@example.com",
            reason="Override requested.",
        )

    assert "hard gates cannot be forced" in str(excinfo.value)


def test_quality_gate_guard_allow_preserves_import_flow(tmp_path, monkeypatch):
    guard = _load_guard()
    monkeypatch.setattr(guard, "build_quality_gates", lambda package_dir: _gates("allow"))

    enforcement = guard.enforce_quality_gates(_package_dir(tmp_path))

    assert enforcement.decision == "allow"
    assert enforcement.promotion_override is None
    assert guard.should_write_target(enforcement, "canonical") is True
    assert guard.should_write_target(enforcement, "retrieval") is True


def test_quality_gate_guard_force_is_only_for_review(tmp_path, monkeypatch):
    guard = _load_guard()
    monkeypatch.setattr(guard, "build_quality_gates", lambda package_dir: _gates("allow"))

    with pytest.raises(SystemExit) as excinfo:
        guard.enforce_quality_gates(
            _package_dir(tmp_path),
            force_review=True,
            requested_by="analyst@example.com",
            reason="Not needed.",
        )

    assert "only valid when canonical decision is review" in str(excinfo.value)
