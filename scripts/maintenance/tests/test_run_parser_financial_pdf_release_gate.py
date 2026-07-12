import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "run_parser_financial_pdf_release_gate.py"
    spec = importlib.util.spec_from_file_location("parser_financial_pdf_release_gate_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest(case: dict) -> dict:
    return {
        "schema_version": "siq_parser_financial_golden_manifest_v1",
        "cases": [case],
    }


def _case(pdf_sha256: str = "a" * 64) -> dict:
    return {
        "case_id": "icbc-pdf-test",
        "source_path": "icbc.md",
        "source_sha256": "b" * 64,
        "pdf_source_path": "CN/ICBC/icbc.pdf",
        "pdf_source_sha256": pdf_sha256,
        "pdf_min_bytes": 10,
        "pdf_page_count": 408,
        "expected_metrics": [
            {"canonical_name": "operating_revenue", "period": "2025", "value": 838270000000.0}
        ],
    }


def test_default_pdf_contract_passes():
    module = _load_module()
    manifest = json.loads(module.DEFAULT_MANIFEST.read_text(encoding="utf-8"))

    assert module.validate_pdf_contract(manifest) == []


def test_raw_parser_artifact_decoder_accepts_nested_json_strings():
    module = _load_module()

    payload = json.dumps(json.dumps({"pdf_info": [{}, {}]}))

    assert module._decode_json_payload(payload) == {"pdf_info": [{}, {}]}


def test_inspect_pdf_checks_hash_size_and_page_count(tmp_path, monkeypatch):
    module = _load_module()
    source = tmp_path / "CN/ICBC/icbc.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.5\nreal-pdf-fixture")
    case = _case(hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(module, "_pdf_page_count", lambda _path: 408)

    result = module.inspect_pdf(case, tmp_path)

    assert result["passed"] is True
    assert result["pdf_source_sha256"] == case["pdf_source_sha256"]
    assert result["pdf_page_count"] == 408


def test_preflight_blocks_http_200_when_mineru_is_not_ready(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_json_request",
        lambda *_args, **_kwargs: {
            "mineru": False,
            "mineru_detail": "timed out",
            "submit_ready": False,
            "warning": "MinerU is busy",
        },
    )

    result = module.parser_preflight("http://parser.local", 1)

    assert result["passed"] is False
    assert result["errors"] == [
        "MinerU is not ready: timed out",
        "parser submit_ready is false: MinerU is busy",
    ]


def test_report_redacts_parser_url_userinfo_path_and_query(tmp_path):
    module = _load_module()
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(_manifest(_case())), encoding="utf-8")

    report = module.run_gate(
        mode="contract",
        manifest_path=manifest_path,
        parser_url="https://user:secret@parser.local:15443/base?token=hidden#fragment",
    )

    assert report["passed"] is True
    assert report["parser_url"] == "https://parser.local:15443"
    assert "secret" not in json.dumps(report)
    assert "hidden" not in json.dumps(report)


def test_live_gate_does_not_upload_when_preflight_is_blocked(tmp_path, monkeypatch):
    module = _load_module()
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(_manifest(_case())), encoding="utf-8")
    monkeypatch.setattr(
        module,
        "inspect_pdf",
        lambda *_args, **_kwargs: {"case_id": "icbc-pdf-test", "passed": True, "errors": []},
    )
    monkeypatch.setattr(
        module,
        "parser_preflight",
        lambda *_args, **_kwargs: {"passed": False, "errors": ["MinerU unavailable"], "health": {}},
    )
    monkeypatch.setattr(
        module,
        "run_live_case",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live upload must not run")),
    )

    report = module.run_gate(mode="live-http", manifest_path=manifest_path, pdf_root=tmp_path)

    assert report["passed"] is False
    assert report["preflight"]["errors"] == ["MinerU unavailable"]
    assert report["summary"] == {"case_count": 1, "passed": 1, "failed": 0, "missing": 0}


def test_live_case_preserves_unconfirmed_upload_for_recovery(tmp_path, monkeypatch):
    module = _load_module()
    source = tmp_path / "CN/ICBC/icbc.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.5\nreal-pdf-fixture")
    case = _case(hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(
        module,
        "inspect_pdf",
        lambda *_args, **_kwargs: {"case_id": "icbc-pdf-test", "passed": True, "errors": []},
    )

    class Client:
        @staticmethod
        def stream_multipart_post(*_args, **_kwargs):
            return {"_error": True, "detail": "upload timed out"}

    calls = []
    monkeypatch.setattr(module, "_mineru_client_module", lambda: Client())
    monkeypatch.setattr(
        module,
        "_json_request",
        lambda url, **kwargs: calls.append((url, kwargs)) or {"success": True},
    )

    result = module.run_live_case(
        case,
        tmp_path,
        "http://parser.local",
        deadline_seconds=1,
        poll_interval=0,
        request_timeout=1,
    )

    assert result["passed"] is False
    assert result["live_status"] == "upload_failed"
    assert result["cleanup"] == {
        "attempted": False,
        "reason": "preserved_unconfirmed_upload_for_recovery",
    }
    assert calls == []


def _install_live_case_fakes(module, tmp_path, monkeypatch, responses):
    source = tmp_path / "CN/ICBC/icbc.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF-1.5\nreal-pdf-fixture")
    case = _case(hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(
        module,
        "inspect_pdf",
        lambda *_args, **_kwargs: {"case_id": "icbc-pdf-test", "passed": True, "errors": []},
    )
    upload_calls = []

    class Client:
        @staticmethod
        def stream_multipart_post(*args, **kwargs):
            upload_calls.append((args, kwargs))
            task_id = kwargs["fields"]["task_id"]
            return {
                "batch_count": 1,
                "task_id": task_id,
                "tasks": [{"task_id": task_id, "filename": source.name, "pdf_page_count": 408}],
            }

    request_calls = []

    def fake_json_request(url, **kwargs):
        request_calls.append((url, kwargs))
        if kwargs.get("method") == "DELETE":
            return {"success": True}
        return responses.pop(0)

    monkeypatch.setattr(module, "_mineru_client_module", lambda: Client())
    monkeypatch.setattr(module, "_json_request", fake_json_request)
    return case, upload_calls, request_calls


def test_live_upload_matches_current_api_contract_and_passes_completed_markdown(tmp_path, monkeypatch):
    module = _load_module()
    case, upload_calls, request_calls = _install_live_case_fakes(
        module,
        tmp_path,
        monkeypatch,
        [{"status": "completed"}, {"markdown": "# fresh MinerU Markdown"}],
    )
    monkeypatch.setattr(
        module,
        "_evaluate_fresh_markdown",
        lambda *_args: {
            "passed": True,
            "errors": [],
            "financial_semantics_passed": True,
            "fresh_layout_drift": {"detected": False, "details": []},
        },
    )

    result = module.run_live_case(
        case,
        tmp_path,
        "http://parser.local",
        deadline_seconds=10,
        poll_interval=0,
        request_timeout=1,
    )

    assert result["passed"] is True
    upload_kwargs = upload_calls[0][1]
    assert upload_kwargs["file_field_name"] == "files"
    assert upload_kwargs["fields"]["market"] == "CN"
    assert upload_kwargs["fields"]["task_id"] == result["task_id"]
    assert upload_kwargs["content_type"] == "application/pdf"
    assert result["upload"]["task_id"] == result["task_id"]
    assert request_calls[-1][1]["method"] == "DELETE"


def test_live_case_blocks_completed_task_without_markdown_and_cleans_up(tmp_path, monkeypatch):
    module = _load_module()
    case, _upload_calls, request_calls = _install_live_case_fakes(
        module,
        tmp_path,
        monkeypatch,
        [{"status": "completed"}, {"markdown": None}],
    )

    result = module.run_live_case(
        case,
        tmp_path,
        "http://parser.local",
        deadline_seconds=10,
        poll_interval=0,
        request_timeout=1,
    )

    assert result["passed"] is False
    assert result["errors"] == ["completed parser task returned no Markdown"]
    assert result["cleanup"] == {
        "attempted": False,
        "reason": "preserved_task_without_result_evidence_for_recovery",
    }
    assert all(call[1].get("method") != "DELETE" for call in request_calls)


def test_live_case_timeout_is_blocked_and_cleans_up(tmp_path, monkeypatch):
    module = _load_module()
    case, _upload_calls, request_calls = _install_live_case_fakes(module, tmp_path, monkeypatch, [])

    result = module.run_live_case(
        case,
        tmp_path,
        "http://parser.local",
        deadline_seconds=0,
        poll_interval=0,
        request_timeout=1,
    )

    assert result["passed"] is False
    assert result["errors"] == ["parser task timed out after 0s"]
    assert result["cleanup"] == {
        "attempted": False,
        "reason": "preserved_task_without_result_evidence_for_recovery",
    }
    assert request_calls == []


def test_live_case_blocks_fresh_financial_assertion_failure_and_cleans_up(tmp_path, monkeypatch):
    module = _load_module()
    case, _upload_calls, request_calls = _install_live_case_fakes(
        module,
        tmp_path,
        monkeypatch,
        [{"status": "completed"}, {"markdown": "# fresh MinerU Markdown"}],
    )
    monkeypatch.setattr(
        module,
        "_evaluate_fresh_markdown",
        lambda *_args: {"passed": False, "errors": ["metric value mismatch: operating_revenue"]},
    )

    result = module.run_live_case(
        case,
        tmp_path,
        "http://parser.local",
        deadline_seconds=10,
        poll_interval=0,
        request_timeout=1,
    )

    assert result["passed"] is False
    assert result["errors"] == ["metric value mismatch: operating_revenue"]
    assert result["financial_golden"]["passed"] is False
    assert result["cleanup"] == {"success": True}
    assert request_calls[-1][1]["method"] == "DELETE"


def test_recovered_result_records_hashes_and_runs_strict_financial_gate(tmp_path, monkeypatch):
    module = _load_module()
    result_path = tmp_path / "mineru-result.json"
    result_path.write_text(
        json.dumps(
            {
                "backend": "hybrid-http-client",
                "version": "3.1.2",
                "results": {"icbc": {"md_content": "# fresh MinerU Markdown"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "inspect_pdf",
        lambda *_args, **_kwargs: {"case_id": "icbc-pdf-test", "passed": True, "errors": []},
    )
    monkeypatch.setattr(
        module,
        "_evaluate_fresh_markdown",
        lambda *_args: {
            "passed": False,
            "errors": ["source_lines below minimum 8000"],
            "financial_checks_overall_status": "pass",
            "financial_semantics_passed": True,
            "fresh_layout_drift": {
                "detected": True,
                "details": ["source_lines below minimum 8000"],
            },
        },
    )

    class PageMarkers:
        @staticmethod
        def _inject_pdf_page_markers(markdown, _content_list, *, total_pages):
            assert total_pages == 408
            return markdown + "\n[PDF_PAGE: 408]"

        @staticmethod
        def _backfill_sparse_markdown_pages(markdown, _content_list):
            return markdown + "\nfinal", []

    monkeypatch.setattr(module, "_page_markers_module", lambda: PageMarkers())

    result = module.run_recovered_case(
        _case(),
        tmp_path,
        result_path,
        upstream_task_id="386c1e66-2ba0-49a6-9d1c-00965896252d",
    )

    assert result["passed"] is False
    assert result["financial_semantics_passed"] is True
    assert result["fresh_layout_drift"]["detected"] is True
    assert result["recovery_evidence"]["backend"] == "hybrid-http-client"
    assert result["recovery_evidence"]["result_sha256"] == hashlib.sha256(result_path.read_bytes()).hexdigest()
    assert result["recovery_evidence"]["artifact_stage"] == "pdf_api_final_markdown"
    assert result["recovery_evidence"]["raw_markdown_sha256"] != result["recovery_evidence"]["markdown_sha256"]
    assert result["recovery_evidence"]["restored_sparse_page_count"] == 0
    assert "fresh MinerU Markdown" not in json.dumps(result)


def test_live_checkpoint_is_redacted_and_written_atomically(tmp_path):
    module = _load_module()
    path = tmp_path / "checkpoint.json"
    item = {
        "case_id": "icbc-pdf-test",
        "task_id": "pdf-golden-123",
        "live_status": "processing",
        "upload": {"task_id": "pdf-golden-123"},
        "task_status": {
            "status": "processing",
            "mineru_task_id": "upstream-123",
            "logs": ["secret raw parser log"],
        },
        "errors": [],
    }

    module._write_json_atomic(path, module._checkpoint_view(item))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["task_id"] == "pdf-golden-123"
    assert payload["upstream_task_id"] == "upstream-123"
    assert payload["evidence_captured"] is False
    assert "secret raw parser log" not in path.read_text(encoding="utf-8")


def test_fresh_markdown_classifies_layout_drift_without_weakening_semantic_provenance(
    tmp_path, monkeypatch
):
    module = _load_module()

    class Golden:
        @staticmethod
        def file_sha256(_path):
            return "c" * 64

        @staticmethod
        def run_offline_case(_case, _root):
            return {
                "passed": False,
                "errors": [
                    "source_lines below minimum 8000",
                    "metric source line mismatch: operating_revenue 2025 expected 432, got 418",
                ],
                "observed_metrics": [
                    {
                        "canonical_name": "operating_revenue",
                        "period": "2025",
                        "value": 838270000000.0,
                        "source_line": 418,
                        "table_index": 3,
                    }
                ],
                "financial_checks_overall_status": "pass",
                "quality_flags": [],
            }

    monkeypatch.setattr(module, "_golden_module", lambda: Golden())

    result = module._evaluate_fresh_markdown(_case(), "# fresh")

    assert result["financial_semantics_passed"] is True
    assert result["fresh_layout_drift"]["detected"] is True
    assert len(result["fresh_layout_drift"]["details"]) == 2
    assert result["passed"] is False


def test_fresh_markdown_blocks_missing_metric_provenance(tmp_path, monkeypatch):
    module = _load_module()

    class Golden:
        @staticmethod
        def file_sha256(_path):
            return "d" * 64

        @staticmethod
        def run_offline_case(_case, _root):
            return {
                "passed": True,
                "errors": [],
                "observed_metrics": [
                    {
                        "canonical_name": "operating_revenue",
                        "period": "2025",
                        "value": 838270000000.0,
                        "source_line": None,
                        "table_index": None,
                    }
                ],
                "financial_checks_overall_status": "pass",
                "quality_flags": [],
            }

    monkeypatch.setattr(module, "_golden_module", lambda: Golden())

    result = module._evaluate_fresh_markdown(_case(), "# fresh")

    assert result["fresh_layout_drift"]["detected"] is False
    assert result["financial_semantics_passed"] is False
    assert result["semantic_errors"] == [
        "metric provenance missing source_line: operating_revenue 2025",
        "metric provenance missing table_index: operating_revenue 2025",
    ]
    assert result["errors"] == result["semantic_errors"]
    assert result["passed"] is False


def test_fresh_markdown_structure_drift_is_a_separate_hard_block(tmp_path, monkeypatch):
    module = _load_module()

    class Golden:
        @staticmethod
        def file_sha256(_path):
            return "e" * 64

        @staticmethod
        def run_offline_case(_case, _root):
            return {
                "passed": True,
                "errors": [],
                "observed_metrics": [],
                "financial_checks_overall_status": "pass",
                "quality_flags": [],
            }

    monkeypatch.setattr(module, "_golden_module", lambda: Golden())
    case = _case()
    case["structure"] = {
        "html_table_count": 1,
        "markdown_table_count": 1,
        "heading_count": 1,
        "image_count": 1,
        "details_count": 1,
    }

    result = module._evaluate_fresh_markdown(case, "# fresh")

    assert result["financial_semantics_passed"] is True
    assert result["fresh_layout_drift"]["detected"] is False
    assert result["fresh_structure"]["checked"] is True
    assert result["fresh_structure"]["passed"] is False
    assert len(result["fresh_structure"]["errors"]) == 4
    assert result["passed"] is False
    assert result["errors"] == result["fresh_structure"]["errors"]


def test_recovered_case_propagates_semantic_failure_to_case_status(tmp_path, monkeypatch):
    module = _load_module()
    result_path = tmp_path / "mineru-result.json"
    result_path.write_text(
        json.dumps({"results": {"icbc": {"md_content": "# fresh"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "inspect_pdf",
        lambda *_args, **_kwargs: {"case_id": "icbc-pdf-test", "passed": True, "errors": []},
    )
    monkeypatch.setattr(
        module,
        "_evaluate_fresh_markdown",
        lambda *_args: {
            "passed": False,
            "errors": ["metric provenance missing source_line: operating_revenue 2025"],
            "financial_semantics_passed": False,
            "fresh_layout_drift": {"detected": False, "details": []},
        },
    )

    result = module.run_recovered_case(
        _case(),
        tmp_path,
        result_path,
        upstream_task_id="upstream-123",
    )

    assert result["financial_semantics_passed"] is False
    assert result["fresh_layout_drift"]["detected"] is False
    assert result["passed"] is False
    assert result["errors"] == [
        "metric provenance missing source_line: operating_revenue 2025"
    ]


def test_live_case_propagates_semantic_failure_to_case_status(tmp_path, monkeypatch):
    module = _load_module()
    case, _upload_calls, _request_calls = _install_live_case_fakes(
        module,
        tmp_path,
        monkeypatch,
        [{"status": "completed"}, {"markdown": "# fresh"}],
    )
    monkeypatch.setattr(
        module,
        "_evaluate_fresh_markdown",
        lambda *_args: {
            "passed": False,
            "errors": ["metric provenance missing table_index: operating_revenue 2025"],
            "financial_semantics_passed": False,
            "fresh_layout_drift": {"detected": False, "details": []},
        },
    )

    result = module.run_live_case(
        case,
        tmp_path,
        "http://parser.local",
        deadline_seconds=10,
        poll_interval=0,
        request_timeout=1,
    )

    assert result["financial_semantics_passed"] is False
    assert result["fresh_layout_drift"]["detected"] is False
    assert result["passed"] is False
    assert result["errors"] == [
        "metric provenance missing table_index: operating_revenue 2025"
    ]


def _eligible_rebaseline_result():
    layout_errors = ["source_lines below minimum 8000"]
    return {
        "case_id": "icbc-pdf-test",
        "status": "checked",
        "pdf_source_sha256": "a" * 64,
        "pdf_page_count": 408,
        "passed": False,
        "errors": layout_errors,
        "financial_semantics_passed": True,
        "fresh_layout_drift": {"detected": True, "details": layout_errors},
        "fresh_structure": {"checked": True, "passed": True, "errors": []},
        "recovery_evidence": {
            "markdown_sha256": "f" * 64,
            "raw_pdf_page_count": 408,
            "raw_model_output_page_count": 408,
            "raw_content_list_page_count": 408,
        },
        "financial_golden": {
            "passed": False,
            "financial_checks_overall_status": "pass",
            "quality_flags": [],
            "observed_metrics": [
                {
                    "canonical_name": "operating_revenue",
                    "period": "2025",
                    "value": 838270000000.0,
                    "source_line": 418,
                    "table_index": 3,
                }
            ],
        },
    }


def test_rebaseline_candidate_never_passes_without_explicit_version_and_hash_approval(
    tmp_path, monkeypatch
):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "run_recovered_case",
        lambda *_args, **_kwargs: deepcopy(_eligible_rebaseline_result()),
    )

    result = module.run_rebaseline_candidate(
        _case(),
        tmp_path,
        tmp_path / "result.json",
        upstream_task_id="upstream-123",
        candidate_version="icbc-2025-v2",
    )

    assert result["baseline_candidate"]["eligible"] is True
    assert result["baseline_candidate"]["pdf_identity_contract_passed"] is True
    assert result["baseline_candidate"]["presentation_only"] is True
    assert result["baseline_candidate"]["raw_page_contract_passed"] is True
    assert result["baseline_candidate"]["structure_contract_passed"] is True
    assert result["baseline_candidate"]["financial_contract_passed"] is True
    assert result["baseline_candidate"]["quality_contract_passed"] is True
    assert result["baseline_candidate"]["provenance_contract_passed"] is True
    assert result["baseline_candidate"]["approval"]["approved"] is False
    assert result["baseline_candidate"]["manifest_mutated"] is False
    assert result["passed"] is False
    assert result["errors"] == ["explicit baseline version and SHA-256 approval is required"]
    assert result["presentation_findings"] == ["source_lines below minimum 8000"]


def test_rebaseline_candidate_requires_exact_version_and_hash_to_approve(tmp_path, monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "run_recovered_case",
        lambda *_args, **_kwargs: deepcopy(_eligible_rebaseline_result()),
    )

    wrong = module.run_rebaseline_candidate(
        _case(),
        tmp_path,
        tmp_path / "result.json",
        upstream_task_id="upstream-123",
        candidate_version="icbc-2025-v2",
        approved_version="icbc-2025-v2",
        approved_sha256="e" * 64,
    )
    approved = module.run_rebaseline_candidate(
        _case(),
        tmp_path,
        tmp_path / "result.json",
        upstream_task_id="upstream-123",
        candidate_version="icbc-2025-v2",
        approved_version="icbc-2025-v2",
        approved_sha256="f" * 64,
    )

    assert wrong["passed"] is False
    assert wrong["baseline_candidate"]["approval"]["approved"] is False
    assert approved["passed"] is True
    assert approved["errors"] == []
    assert approved["baseline_candidate"]["approval"]["approved"] is True


def test_rebaseline_candidate_blocks_non_presentation_drift_even_with_matching_approval(
    tmp_path, monkeypatch
):
    module = _load_module()
    unsafe = _eligible_rebaseline_result()
    unsafe["errors"].append("metric value mismatch: operating_revenue")
    unsafe["financial_semantics_passed"] = False
    monkeypatch.setattr(
        module,
        "run_recovered_case",
        lambda *_args, **_kwargs: deepcopy(unsafe),
    )

    result = module.run_rebaseline_candidate(
        _case(),
        tmp_path,
        tmp_path / "result.json",
        upstream_task_id="upstream-123",
        candidate_version="icbc-2025-v2",
        approved_version="icbc-2025-v2",
        approved_sha256="f" * 64,
    )

    assert result["baseline_candidate"]["eligible"] is False
    assert result["baseline_candidate"]["presentation_only"] is False
    assert result["baseline_candidate"]["approval"]["approved"] is False
    assert result["passed"] is False
    assert result["errors"] == ["baseline candidate is not eligible for approval"]


def _approved_case_and_evidence():
    case = _case()
    case["approved_fresh_baseline"] = {
        "version": "icbc-v2",
        "markdown_sha256": "f" * 64,
        "source_bytes": 100,
        "source_lines": 10,
        "raw_page_count": 408,
        "structure": {"html_table_count": 1},
        "expected_metrics": [
            {
                "canonical_name": "operating_revenue",
                "period": "2025",
                "value": 838270000000.0,
                "source_line": 418,
                "table_index": 3,
            }
        ],
        "expected_financial_checks_status": "pass",
        "required_quality_flags": [],
    }
    financial = {
        "source_bytes": 100,
        "source_lines": 10,
        "fresh_structure": {"observed": {"html_table_count": 1}},
        "observed_metrics": [
            {
                "canonical_name": "operating_revenue",
                "period": "2025",
                "value": 838270000000.0,
                "source_line": 418,
                "table_index": 3,
            }
        ],
        "financial_checks_overall_status": "pass",
        "quality_flags": [],
        "financial_semantics_passed": True,
    }
    evidence = {
        "markdown_sha256": "f" * 64,
        "raw_pdf_page_count": 408,
        "raw_model_output_page_count": 408,
        "raw_content_list_page_count": 408,
    }
    return case, financial, evidence


def test_approved_fresh_baseline_requires_exact_version_hash_structure_and_semantics():
    module = _load_module()
    case, financial, evidence = _approved_case_and_evidence()

    assert module._approved_fresh_baseline_validation(case, financial, evidence)["passed"] is True

    wrong_hash = deepcopy(case)
    wrong_hash["approved_fresh_baseline"]["markdown_sha256"] = "e" * 64
    assert module._approved_fresh_baseline_validation(wrong_hash, financial, evidence)["passed"] is False

    wrong_version = deepcopy(case)
    wrong_version["approved_fresh_baseline"]["version"] = ""
    assert module._approved_fresh_baseline_validation(wrong_version, financial, evidence)["passed"] is False

    wrong_structure = deepcopy(financial)
    wrong_structure["fresh_structure"]["observed"]["html_table_count"] = 0
    assert module._approved_fresh_baseline_validation(case, wrong_structure, evidence)["passed"] is False

    wrong_semantics = deepcopy(financial)
    wrong_semantics["financial_semantics_passed"] = False
    assert module._approved_fresh_baseline_validation(case, wrong_semantics, evidence)["passed"] is False
