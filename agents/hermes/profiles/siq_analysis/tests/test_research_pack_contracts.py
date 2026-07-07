import json
import importlib.util
from pathlib import Path


PROFILE_DIR = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = PROFILE_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


merge_research_packs = _load_script("merge_research_packs").merge_research_packs
validate_work_dir = _load_script("validate_research_packs").validate_work_dir


def _pack(**overrides):
    payload = {
        "schema_version": "1.0",
        "agent_id": "financial_modeler",
        "company_id": "CN:600000",
        "report_year": 2025,
        "generated_at": "2026-07-07T00:00:00+08:00",
        "input_files": [{"path": "evidence.md", "status": "read"}],
        "coverage": {
            "section_ids": ["executive_summary"],
            "time_periods": ["2025"],
            "source_scope": ["annual_report"],
            "known_limits": [],
        },
        "key_findings": [
            {
                "section_ids": ["executive_summary"],
                "claim": "收入增长已经由年报事实支撑。",
                "confidence": 0.91,
                "fact_status": "verified_fact",
                "evidence_refs": [{"source_file": "evidence.md", "md_line": 1, "quote": "收入增长"}],
            }
        ],
        "evidence_facts": [
            {
                "fact": "营业收入同比增长。",
                "confidence": 0.9,
                "fact_status": "verified_fact",
                "evidence_refs": [{"source_file": "evidence.md", "md_line": 1}],
            }
        ],
        "calculations": [
            {
                "name": "收入增速",
                "formula": "(current-prior)/prior",
                "inputs": {"current": 120, "prior": 100},
                "output": {"value_pct": 20},
                "confidence": 0.86,
                "fact_status": "modeled_estimate",
                "review_required": True,
                "evidence_refs": [{"source_file": "evidence.md", "md_line": 1}],
            }
        ],
        "risk_chains": [],
        "tracking_signals": [],
        "external_sources": [],
        "missing_inputs": [],
        "review_required": True,
        "prohibited_content_hits": [],
    }
    payload.update(overrides)
    return payload


def _write_work_dir(tmp_path: Path, pack: dict) -> Path:
    work_dir = tmp_path / "work"
    (work_dir / "research_packs").mkdir(parents=True)
    (work_dir / "evidence.md").write_text("收入增长来自年报。\n", encoding="utf-8")
    (work_dir / "research_packs" / "financial_modeler.json").write_text(
        json.dumps(pack, ensure_ascii=False),
        encoding="utf-8",
    )
    return work_dir


def test_validate_research_pack_resolves_evidence_and_schema(tmp_path):
    work_dir = _write_work_dir(tmp_path, _pack())

    result = validate_work_dir(work_dir, require_all_packs=False)

    assert result["ok"], result["failures"]


def test_validate_research_pack_rejects_verified_finding_without_evidence(tmp_path):
    pack = _pack(key_findings=[{"section_ids": ["executive_summary"], "claim": "缺少证据。", "confidence": 0.8}])
    work_dir = _write_work_dir(tmp_path, pack)

    result = validate_work_dir(work_dir, require_all_packs=False)

    assert not result["ok"]
    assert any("key_finding_missing_evidence_or_fact_status" in failure for failure in result["failures"])


def test_validate_research_pack_rejects_unresolvable_evidence(tmp_path):
    pack = _pack()
    pack["key_findings"][0]["evidence_refs"] = [{"source_file": "missing.md"}]
    work_dir = _write_work_dir(tmp_path, pack)

    result = validate_work_dir(work_dir, require_all_packs=False)

    assert not result["ok"]
    assert any("evidence_ref_unresolvable" in failure for failure in result["failures"])


def test_merge_research_packs_preserves_fact_status_metadata(tmp_path):
    work_dir = _write_work_dir(tmp_path, _pack())
    section_drafts = work_dir / "section_drafts.json"
    section_drafts.write_text(
        json.dumps({"sections": [{"section_id": "executive_summary", "narrative_blocks": [], "judgements": []}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = merge_research_packs(work_dir, section_drafts)
    merged = json.loads(section_drafts.read_text(encoding="utf-8"))
    blocks = merged["sections"][0]["narrative_blocks"]
    metadata = [item for block in blocks for item in block.get("item_metadata", [])]

    assert result["ok"] is True
    assert any(item.get("fact_status") == "verified_fact" for item in metadata)
    assert any(item.get("fact_status") == "modeled_estimate" and item.get("review_required") is True for item in metadata)
    assert merged["sections"][0]["review_required"] is True


def test_merge_research_packs_does_not_truncate_visible_text(tmp_path):
    long_claim = "收入规模目标值 6461.52亿元，同业中位 1640.00亿元，约处于 85.71% 分位；" * 8
    peer_output = {
        "operating_revenue_yi": {"target_value": 6461.52, "target_percentile": 85.71, "median": 1640.0, "sample_count": 7},
        "operating_revenue_yoy_pct": {"target_value": 5.22, "target_percentile": 50.0, "median": -0.04, "sample_count": 6},
        "gross_margin_pct": {"target_value": 10.09, "target_percentile": 28.57, "median": 15.54, "sample_count": 7},
        "parent_net_profit_yi": {"target_value": 101.06, "target_percentile": 85.71, "median": 40.75, "sample_count": 7},
    }
    pack = _pack(
        agent_id="industry_peer_researcher",
        key_findings=[
            {
                "section_ids": ["industry_competition"],
                "claim": long_claim,
                "confidence": 0.88,
                "fact_status": "modeled_estimate",
                "review_required": True,
                "evidence_refs": [{"source_file": "evidence.md", "md_line": 1}],
            }
        ],
        calculations=[
            {
                "section_ids": ["industry_competition"],
                "name": "peer_metrics_aggregates",
                "formula": "peer metrics aggregate output",
                "inputs": {"peer_count": 7},
                "output": peer_output,
                "confidence": 0.85,
                "fact_status": "modeled_estimate",
                "review_required": True,
                "evidence_refs": [{"source_file": "evidence.md", "md_line": 1}],
            }
        ],
    )
    work_dir = _write_work_dir(tmp_path, pack)
    section_drafts = work_dir / "section_drafts.json"
    section_drafts.write_text(
        json.dumps({"sections": [{"section_id": "industry_competition", "narrative_blocks": [], "judgements": []}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = merge_research_packs(work_dir, section_drafts)
    merged = json.loads(section_drafts.read_text(encoding="utf-8"))
    rendered_items = [item for block in merged["sections"][0]["narrative_blocks"] for item in block.get("items", [])]

    assert result["ok"] is True
    assert not any("..." in item or "…" in item for item in rendered_items)
    assert any("归母净利润" in item for item in rendered_items)
