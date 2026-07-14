from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = (
    PROJECT_ROOT
    / "eval_datasets"
    / "primary_market_ic_real_smoke"
    / "generate_evidence_complete_fixture.py"
)
FIXTURE = (
    PROJECT_ROOT
    / "eval_datasets"
    / "primary_market_ic_real_smoke"
    / "DEAL-PMIC-POSITIVE-COND-2026"
)


def _load_generator():
    spec = importlib.util.spec_from_file_location("pmic_evidence_complete_fixture", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _items() -> list[dict]:
    return [
        json.loads(line)
        for line in (FIXTURE / "evidence/evidence_items.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _detector_probe_report(
    *,
    agent_id: str,
    phase: str,
    recommendation: str,
    score: int,
    evidence_ids: list[str],
    conclusion: str,
    snapshot_hash: str,
    source_ids: list[str],
    known_evidence: dict[str, dict],
) -> dict:
    from services import ic_profile_contract, ic_report_contracts

    code = agent_id.removeprefix("siq_ic_").replace("_", "-").upper()
    claim_id = f"CLM-PMIC-POS-{code}-001"
    contract = ic_profile_contract.get_ic_profile_contract(agent_id)
    role_fields = {
        field: {"result": "verified by the deterministic detector probe"}
        for field in ic_report_contracts.ROLE_REQUIRED_FIELDS[agent_id]
    }
    if agent_id == "siq_ic_finance_auditor":
        role_fields["calculation_trace_ids"] = ["CALC-PMIC-POS-DCF-001"]
    if agent_id == "siq_ic_legal_scanner":
        role_fields["closing_conditions"] = [
            {"condition": "Reconfirm already-verified permits and order confirmations at closing"}
        ]
        role_fields["unresolved_legal_questions"] = []
    if agent_id == "siq_ic_risk_controller":
        role_fields["warning_thresholds"] = [
            {"metric": "next_twelve_month_order_coverage", "threshold": 1.2}
        ]
        role_fields["stop_loss_thresholds"] = [
            {"metric": "downside_capacity_utilization", "threshold": 0.622}
        ]
        role_fields["veto_flags"] = []
    if agent_id == "siq_ic_chairman":
        role_fields.update(
            {
                "disputes": [],
                "rulings": [],
                "six_dimension_scorecard": [
                    {"dimension": f"detector_probe_dimension_{index}"} for index in range(1, 7)
                ],
                "weighted_agent_score": score,
                "chairman_dimension_score": score,
                "chairman_qualitative_decision": "conditional support requires R1.5 adjudication",
                "conditions": [
                    {"condition": "valuation cap and staged capacity release"}
                ],
                "monitoring_metrics": [
                    {"metric": "order coverage, utilization, DSO and component qualification"}
                ],
                "decision": "review",
            }
        )

    report = {
        "schema_version": "siq_ic_expert_report_v2",
        "report_id": f"ICRPT-PMIC-POS-{code}-001",
        "workflow_run_id": "ICRUN-PMICPOSITIVE2026",
        "deal_id": FIXTURE.name,
        "phase": phase,
        "agent_id": agent_id,
        "research_identity": {
            "deal_id": FIXTURE.name,
            "source_ids": source_ids,
            "probe_only": True,
        },
        "evidence_snapshot_hash": snapshot_hash,
        "recommendation": recommendation,
        "score": score,
        "confidence": "high",
        "claims": [
            {
                "claim_id": claim_id,
                "topic": "capacity_valuation_conditionality",
                "conclusion": conclusion,
                "status": "verified",
                "evidence_ids": evidence_ids,
                "counter_evidence_ids": [],
                "calculation_trace_ids": [],
                "background_knowledge_ref_ids": [],
                "methodology_ref_ids": [],
                "confidence": "high",
                "decision_impact": "critical",
                "period": "2025-2029",
                "currency": "CNY",
                "unit": "million",
            }
        ],
        "scorecard": [
            {
                "dimension": "capacity_valuation_conditionality",
                "score": score,
                "weight": 1,
                "rationale": "The detector probe cites only committed project Evidence.",
                "claim_ids": [claim_id],
                "evidence_ids": evidence_ids,
                "confidence": "high",
            }
        ],
        "evidence_ids": evidence_ids,
        "red_flags": [],
        "open_questions": [],
        "required_followups": [],
        "executive_summary": conclusion,
        "methodology": ["contract-only deterministic detector probe"],
        "background_knowledge_refs": [],
        "methodology_refs": [],
        "startup_receipt_id": f"detector-probe-{agent_id}-{phase}",
        "startup_retrieval_gate": {
            "receipt_id": f"detector-probe-{agent_id}-{phase}",
            "allowed_to_speak": True,
            "project_evidence_ready": True,
            "private_background_ready": True,
            "shared_collection": contract["shared_collection"],
            "private_collection": contract["private_knowledge_collection"],
            "blocking_reasons": [],
        },
        "limitations": [
            "Human-authored detector probe only; this is not Hermes output or a live workflow artifact."
        ],
        "generation_mode": "human_authored",
        "revision": 1,
        "parent_report_id": None,
        "created_at": "2026-07-14T00:00:00Z",
        **role_fields,
    }
    return ic_report_contracts.validate_expert_report(
        report,
        expected_deal_id=FIXTURE.name,
        expected_agent_id=agent_id,
        expected_snapshot_hash=snapshot_hash,
        known_evidence=known_evidence,
    )


def test_fixture_is_deterministic_evidence_complete_and_source_traceable():
    generator = _load_generator()
    rendered = generator.build_files()
    generator._validate_rendered_files(rendered)
    generator.check_fixture(rendered)

    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    contract = json.loads((FIXTURE / "fixture_contract.json").read_text(encoding="utf-8"))
    quality = json.loads(
        (FIXTURE / "evidence/evidence_quality_report.json").read_text(encoding="utf-8")
    )
    source = json.loads(
        (
            FIXTURE
            / "parsed_documents/DOC-PMICPOSCOND2026A1/runs/PRUN-20260714-PMICPOSCOND001/content_list_enhanced.json"
        ).read_text(encoding="utf-8")
    )
    blocks = {item["id"]: item for item in source["blocks"]}
    items = _items()

    assert manifest["synthetic_evaluation_only"] is True
    assert contract["synthetic_evaluation_only"] is True
    assert contract["real_entity_or_transaction"] is False
    assert contract["critical_fact_completeness"] == {
        **contract["critical_fact_completeness"],
        "status": "complete",
        "missing_critical_facts": [],
        "open_questions": [],
    }
    assert quality["status"] == "pass"
    assert quality["critical_fact_status"] == "complete"
    assert quality["known_critical_fact_gaps"] == []
    assert len(items) == 40
    assert Counter(item["dimension"] for item in items) == {
        "business": 10,
        "finance": 10,
        "legal": 10,
        "risk": 10,
    }
    assert all(item["synthetic_evaluation_only"] is True for item in items)
    assert all(item["source_class"] == "project_evidence" for item in items)
    assert all(item["evidence_type"] == "verified" for item in items)
    assert all(
        blocks[item["source_anchor"]["block_id"]]["text"] == item["quote"] for item in items
    )

    tension = contract["material_expert_tensions"][0]
    evidence_ids = {item["evidence_id"] for item in items}
    assert tension["materiality"] == "high"
    assert tension["evidence_complete"] is True
    assert set(tension["supporting_evidence_ids"]) <= evidence_ids


def test_fixture_snapshot_hash_matches_the_runtime_algorithm():
    snapshot = json.loads(
        (FIXTURE / "evidence/evidence_snapshot.json").read_text(encoding="utf-8")
    )
    index_hash = hashlib.sha256((FIXTURE / "evidence/evidence_index.json").read_bytes()).hexdigest()
    assert snapshot["evidence_index_sha256"] == index_hash
    active = snapshot["active_sources"][0]
    digest = "\n".join(
        [
            "siq_deal_evidence_item_v1",
            f"{active['source_id']}:{active['archive_manifest_sha256']}",
            f"evidence_index:{index_hash}",
        ]
    ).encode()
    assert snapshot["snapshot_hash"] == hashlib.sha256(digest).hexdigest()
    assert snapshot["source_ids"] == [active["source_id"]]


def test_fixture_passes_preflight_and_default_role_retrieval(tmp_path: Path):
    from services import (
        deal_contracts,
        deal_evidence,
        deal_retrieval,
        ic_startup_retrieval,
    )

    wiki_root = tmp_path / "wiki"
    package = wiki_root / "deals" / FIXTURE.name
    package.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, package)

    before = json.loads((package / "evidence/evidence_snapshot.json").read_text(encoding="utf-8"))
    refreshed = deal_evidence.refresh_evidence_snapshot(FIXTURE.name, wiki_root=wiki_root)
    assert refreshed["snapshot_hash"] == before["snapshot_hash"]
    assert refreshed["evidence_index_sha256"] == before["evidence_index_sha256"]
    identity = ic_startup_retrieval.current_evidence_identity(
        FIXTURE.name, wiki_root=wiki_root
    )
    assert identity == {
        "source_ids": before["source_ids"],
        "evidence_snapshot_hash": before["snapshot_hash"],
        "capability_restrictions": {},
        "research_identities": [
            {
                "domain": "primary_market",
                "market": "CN",
                "company_id": f"PRIMARY:{FIXTURE.name}",
                "filing_id": "PROSPECTUS:DOC-PMICPOSCOND2026A1",
                "document_id": "DOC-PMICPOSCOND2026A1",
                "parse_run_id": "PRUN-20260714-PMICPOSCOND001",
                "source_id": before["source_ids"][0],
            }
        ],
    }
    evidence_package = deal_evidence.read_deal_evidence_package(
        FIXTURE.name, wiki_root=wiki_root, preview_limit=50
    )
    assert evidence_package["status"] == "pass"
    assert evidence_package["total_item_count"] == 40
    assert evidence_package["matched_count"] == 40
    assert evidence_package["quality_report"]["critical_fact_status"] == "complete"
    assert evidence_package["quality_report"]["known_critical_fact_gaps"] == []

    preflight = deal_contracts.run_deal_preflight(FIXTURE.name, wiki_root=wiki_root)
    assert not [check for check in preflight["checks"] if check["status"] == "fail"]
    checks = {check["id"]: check for check in preflight["checks"]}
    assert checks["identity.deal_id"]["status"] == "pass"
    assert checks["retrieval.evidence_snapshot"]["status"] == "pass"
    assert checks["evidence.gate"]["status"] == "pass"

    role_dimensions = {
        "siq_ic_strategist": "business",
        "siq_ic_sector_expert": "business",
        "siq_ic_finance_auditor": "finance",
        "siq_ic_legal_scanner": "legal",
        "siq_ic_risk_controller": "risk",
    }
    for profile_id, dimension in role_dimensions.items():
        result = deal_retrieval.retrieve_for_agent(
            FIXTURE.name,
            profile_id,
            limit=10,
            include_vector=False,
            wiki_root=wiki_root,
        )
        assert result["evidence_hit_count"] == 10
        assert result["matched_evidence_count"] == 10
        assert {item["dimension"] for item in result["evidence_hits"]} == {dimension}
        assert {item["source_class"] for item in result["evidence_hits"]} == {
            "project_evidence"
        }
        assert not result["gaps"]

    for profile_id in ("siq_ic_master_coordinator", "siq_ic_chairman"):
        result = deal_retrieval.retrieve_for_agent(
            FIXTURE.name,
            profile_id,
            limit=10,
            include_vector=False,
            wiki_root=wiki_root,
        )
        assert result["evidence_hit_count"] == 10
        assert result["matched_evidence_count"] == 40
        assert {item["dimension"] for item in result["evidence_hits"]} == {
            "business",
            "finance",
            "legal",
            "risk",
        }
        assert {item["source_class"] for item in result["evidence_hits"]} == {
            "project_evidence"
        }
        assert not result["gaps"]


def test_contract_valid_r1_probe_creates_only_an_evidence_closable_tension(tmp_path: Path):
    from services import deal_disputes, deal_store

    wiki_root = tmp_path / "wiki"
    package = wiki_root / "deals" / FIXTURE.name
    package.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, package)
    snapshot = json.loads(
        (package / "evidence/evidence_snapshot.json").read_text(encoding="utf-8")
    )
    items = _items()
    known_evidence = {item["evidence_id"]: item for item in items}
    report_specs = {
        "siq_ic_strategist": (
            "R1A",
            "support",
            82,
            ["EVID-PMIC-POS-BUS-002", "EVID-PMIC-POS-BUS-003", "EVID-PMIC-POS-BUS-008"],
            "Bottom-up market demand and firm orders support investment and staged expansion.",
        ),
        "siq_ic_sector_expert": (
            "R1A",
            "support",
            84,
            ["EVID-PMIC-POS-BUS-005", "EVID-PMIC-POS-BUS-006", "EVID-PMIC-POS-BUS-009"],
            "Share, verified performance and unit delivery economics support the sector thesis.",
        ),
        "siq_ic_finance_auditor": (
            "R1A",
            "conditional_support",
            76,
            ["EVID-PMIC-POS-FIN-008", "EVID-PMIC-POS-FIN-009", "EVID-PMIC-POS-FIN-010"],
            "Forecast and valuation traces support only the stated valuation cap and staged capital release.",
        ),
        "siq_ic_legal_scanner": (
            "R1A",
            "conditional_support",
            79,
            ["EVID-PMIC-POS-LEG-002", "EVID-PMIC-POS-LEG-004", "EVID-PMIC-POS-LEG-010"],
            "Approvals, FTO and investment terms are verified and support a conditional closing.",
        ),
        "siq_ic_risk_controller": (
            "R1B",
            "reject",
            68,
            ["EVID-PMIC-POS-RSK-004", "EVID-PMIC-POS-RSK-005", "EVID-PMIC-POS-RSK-010"],
            "Unconditional investment is rejected because the evidenced downside requires the valuation cap and staged expansion trigger.",
        ),
        "siq_ic_chairman": (
            "R1B",
            "conditional_support",
            74,
            [
                "EVID-PMIC-POS-BUS-008",
                "EVID-PMIC-POS-FIN-009",
                "EVID-PMIC-POS-FIN-010",
                "EVID-PMIC-POS-LEG-010",
                "EVID-PMIC-POS-RSK-004",
                "EVID-PMIC-POS-RSK-010",
            ],
            "The complete Evidence supports R1.5 adjudication of a valuation-cap and staged-capacity condition.",
        ),
    }
    reports = {
        agent_id: _detector_probe_report(
            agent_id=agent_id,
            phase=phase,
            recommendation=recommendation,
            score=score,
            evidence_ids=evidence_ids,
            conclusion=conclusion,
            snapshot_hash=snapshot["snapshot_hash"],
            source_ids=snapshot["source_ids"],
            known_evidence=known_evidence,
        )
        for agent_id, (phase, recommendation, score, evidence_ids, conclusion) in report_specs.items()
    }
    deal_store.write_json(package / "phases/r1_reports.json", reports)

    result = deal_disputes.identify_deal_disputes(
        FIXTURE.name,
        dry_run=True,
        preserve_rulings=False,
        wiki_root=wiki_root,
    )

    assert result["payload"]["source_reports_count"] == 6
    assert result["warnings"] == []
    assert result["dispute_count"] == 1
    dispute = result["payload"]["disputes"][0]
    assert dispute["dimension"] == "committee_alignment"
    assert dispute["severity"] == "high"
    assert dispute["detection_rules"] == ["recommendation_bucket_divergence"]
    assert dispute["required_followups"] == [
        "Chairman ruling on divergent R1 recommendations"
    ]
    assert not [
        item
        for item in result["payload"]["disputes"]
        if item["dimension"] == "evidence_sufficiency"
    ]
    assert {
        "EVID-PMIC-POS-BUS-008",
        "EVID-PMIC-POS-FIN-009",
        "EVID-PMIC-POS-FIN-010",
        "EVID-PMIC-POS-LEG-010",
        "EVID-PMIC-POS-RSK-004",
        "EVID-PMIC-POS-RSK-010",
    } <= set(dispute["evidence_ids"])
    assert set(dispute["evidence_ids"]) <= set(known_evidence)
    assert all(not position["open_questions"] for position in dispute["positions"])
    assert all(not position["red_flags"] for position in dispute["positions"])
