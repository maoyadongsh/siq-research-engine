from pathlib import Path

import pytest

from services import market_report_postgres_service as service


def _safe_document_full_path(root: Path):
    def safe(_market: str, value: str) -> Path:
        path = Path(value)
        candidate = path if path.is_absolute() else root / path
        resolved = candidate.resolve()
        if root.resolve() not in resolved.parents:
            raise ValueError("outside root")
        return resolved

    return safe


def test_run_market_document_full_import_owns_command_env_and_identity(tmp_path):
    document_root = tmp_path / "parser-results" / "hk"
    document_full = document_root / "task-1" / "document_full.json"
    import_script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    for path in (document_full, import_script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    seen = {}
    failures = []
    durations = []

    class Completed:
        returncode = 0
        stdout = "parse-run-1\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    result = service.run_market_document_full_import(
        payload={"market": "HK", "document_full_path": "task-1/document_full.json", "ddl": True},
        market="HK",
        executable="python",
        repo_root=tmp_path,
        market_document_full_import_scripts={"HK": import_script},
        market_document_full_roots={"HK": document_root},
        market_databases={"HK": "siq_hk"},
        safe_market_document_full_path=_safe_document_full_path(document_root),
        run_command=fake_run,
        command_for_display=lambda args: " ".join(args),
        record_pipeline_failure=lambda **kwargs: failures.append(kwargs),
        record_ingestion_duration=lambda **kwargs: durations.append(kwargs),
        base_env={"DATABASE_URL": "postgresql://postgres:secret@db/siq"},
    )

    assert result["ok"] is True
    assert result["parse_run_id"] == "parse-run-1"
    assert seen["args"] == [
        "python",
        str(import_script),
        str(document_full),
        "--market",
        "HK",
        "--ddl",
    ]
    assert seen["kwargs"]["cwd"] == tmp_path
    assert seen["kwargs"]["timeout"] == 900
    assert seen["kwargs"]["env"]["SIQ_HK_PGDATABASE"] == "siq_hk"
    assert "DATABASE_URL" not in seen["kwargs"]["env"]
    assert result["selector"] == {"market": "HK", "document_full_path": str(document_full)}
    assert result["identity"]["path_keys"]
    assert failures == []
    assert durations[-1]["status"] == "success"


def test_run_market_document_full_import_plan_error_records_failure_without_command(tmp_path):
    failures = []
    durations = []

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    with pytest.raises(service.MarketReportPostgresError) as exc_info:
        service.run_market_document_full_import(
            payload={"market": "HK"},
            market="HK",
            executable="python",
            repo_root=tmp_path,
            market_document_full_import_scripts={"HK": tmp_path / "missing.py"},
            market_document_full_roots={"HK": tmp_path / "parser-results"},
            market_databases={"HK": "siq_hk"},
            safe_market_document_full_path=_safe_document_full_path(tmp_path),
            run_command=fail_run,
            command_for_display=lambda args: " ".join(args),
            record_pipeline_failure=lambda **kwargs: failures.append(kwargs),
            record_ingestion_duration=lambda **kwargs: durations.append(kwargs),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "document_full_path or task_id is required"
    assert failures == [{"market": "HK", "action": "postgres", "reason": "plan_error_400"}]
    assert durations[-1]["status"] == "failure"


def test_market_document_full_import_status_requires_market_for_document_path():
    with pytest.raises(service.MarketReportPostgresError) as exc_info:
        service.market_document_full_import_status(
            market=None,
            parse_run_id=None,
            filing_id=None,
            document_full_path="task-1/document_full.json",
            task_id=None,
            markets_to_search=lambda _market: ["HK"],
            document_full_path_keys=lambda _market, _path: [],
            document_full_roots={"HK": Path("/tmp/hk")},
            import_scripts={"HK": Path("/tmp/import.py")},
            market_databases={"HK": "siq_hk"},
            schemas={"HK": "pdf2md_hk"},
            rel_or_abs=lambda path: str(path),
            db_status_for_market=lambda *_args, **_kwargs: {},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "market is required when document_full_path is provided"


@pytest.mark.parametrize(
    ("selectors", "selector_names"),
    [
        (
            {"parse_run_id": "parse-hk-1", "filing_id": "HK:00700:2025-annual"},
            "parse_run_id, filing_id",
        ),
        (
            {"parse_run_id": "parse-hk-1", "document_full_path": "task-1/document_full.json"},
            "parse_run_id, document_full_path",
        ),
        (
            {"filing_id": "HK:00700:2025-annual", "task_id": "task-1"},
            "filing_id, task_id",
        ),
    ],
)
def test_market_document_full_import_status_rejects_ambiguous_selectors(selectors, selector_names):
    called = []

    with pytest.raises(service.MarketReportPostgresError) as exc_info:
        service.market_document_full_import_status(
            market="HK",
            parse_run_id=selectors.get("parse_run_id"),
            filing_id=selectors.get("filing_id"),
            document_full_path=selectors.get("document_full_path"),
            task_id=selectors.get("task_id"),
            markets_to_search=lambda market: called.append(("markets", market)) or [market or "HK"],
            document_full_path_keys=lambda market, path: called.append(("path", market, path)) or [],
            document_full_roots={"HK": Path("/tmp/hk")},
            import_scripts={"HK": Path("/tmp/import.py")},
            market_databases={"HK": "siq_hk"},
            schemas={"HK": "pdf2md_hk"},
            rel_or_abs=lambda path: str(path),
            db_status_for_market=lambda *_args, **_kwargs: called.append(("db",)) or {},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == f"document_full status selectors are mutually exclusive: {selector_names}"
    assert called == []


def test_market_document_full_import_status_rejects_filing_market_conflict():
    with pytest.raises(service.MarketReportPostgresError) as exc_info:
        service.market_document_full_import_status(
            market="US",
            parse_run_id=None,
            filing_id="HK:00700:2025-annual",
            document_full_path=None,
            task_id=None,
            markets_to_search=lambda _market: ["US"],
            document_full_path_keys=lambda _market, _path: [],
            document_full_roots={"US": Path("/tmp/us")},
            import_scripts={"US": Path("/tmp/import.py")},
            market_databases={"US": "siq_us"},
            schemas={"US": "sec_us"},
            rel_or_abs=lambda path: str(path),
            db_status_for_market=lambda *_args, **_kwargs: {},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "market US conflicts with filing_id market HK"


def test_market_document_full_import_status_keeps_market_only_request_unscoped(tmp_path):
    document_root = tmp_path / "parser-results" / "jp"
    script = tmp_path / "imports" / "import_jp_document_full_to_postgres.py"
    document_root.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    script.write_text("# import", encoding="utf-8")
    seen = []

    result = service.market_document_full_import_status(
        market="JP",
        parse_run_id=None,
        filing_id=None,
        document_full_path=None,
        task_id=None,
        markets_to_search=lambda market: [market or "JP"],
        document_full_path_keys=lambda _market, _path: [],
        document_full_roots={"JP": document_root},
        import_scripts={"JP": script},
        market_databases={"JP": "siq_jp"},
        schemas={"JP": "edinet_jp"},
        rel_or_abs=lambda path: str(path),
        db_status_for_market=lambda market, **selectors: seen.append((market, selectors)) or {},
    )

    assert result["markets"]["JP"]["database"] == "siq_jp"
    assert "postgres" not in result["markets"]["JP"]
    assert seen == [
        (
            "JP",
            {
                "parse_run_id": None,
                "filing_id": None,
                "document_full_path": None,
                "task_id": None,
            },
        )
    ]


def test_market_document_full_import_status_validates_path_and_records_counts(tmp_path):
    document_root = tmp_path / "parser-results" / "hk"
    script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    document_root.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    script.write_text("# import", encoding="utf-8")
    validated = []
    recorded = []

    result = service.market_document_full_import_status(
        market="HK",
        parse_run_id=None,
        filing_id=None,
        document_full_path="task-1/document_full.json",
        task_id=None,
        markets_to_search=lambda market: [market or "HK"],
        document_full_path_keys=lambda market, path: validated.append((market, path)) or [str(path)],
        document_full_roots={"HK": document_root},
        import_scripts={"HK": script},
        market_databases={"HK": "siq_hk"},
        schemas={"HK": "pdf2md_hk"},
        rel_or_abs=lambda path: str(path),
        db_status_for_market=lambda market, **kwargs: {
            "status": "postgres_ready",
            "selectors": {"document_full_path": kwargs["document_full_path"]},
            "parse_runs": 1,
            "facts": 2,
            "tables": 1,
            "chunks": 3,
            "evidence": 1,
            "market": market,
        },
        record_fact_counts=lambda market, counts: recorded.append((market, counts)),
    )

    assert validated == [("HK", "task-1/document_full.json")]
    assert result["markets"]["HK"]["postgres"]["status"] == "postgres_ready"
    assert result["markets"]["HK"]["postgres"]["selectors"]["document_full_path"] == "task-1/document_full.json"
    assert recorded[0][0] == "HK"
    assert recorded[0][1]["facts"] == 2
