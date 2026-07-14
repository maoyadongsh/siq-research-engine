import json
from pathlib import Path

import pytest

from services import market_report_package_service as service


class Completed:
    returncode = 0
    stdout = "ok\n"
    stderr = ""


def _safe_under(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("outside root") from exc
    return resolved_path


def test_us_sec_company_dirs_from_payload_resolves_latest_package_dirs(tmp_path):
    root = tmp_path / "wiki" / "us_sec"
    apple_package = root / "AAPL-Apple-Inc" / "reports" / "2025-10-K"
    msft_package = root / "MSFT-Microsoft" / "reports" / "2025-10-K"
    apple_package.mkdir(parents=True)
    msft_package.mkdir(parents=True)
    cases = {
        "AAPL": {"package_path": str(apple_package)},
        "MSFT": {"package_path": str(msft_package)},
        "BAD": {"package_path": str(root / "missing" / "reports" / "package")},
    }

    def latest_case_item(ticker: str):
        return cases.get(ticker)

    def safe_package_path(value: str) -> Path:
        path = Path(value)
        if not path.exists():
            raise ValueError("missing package")
        return path

    assert service.us_sec_company_dirs_from_payload(
        {"tickers": "msft,AAPL,AAPL,BAD"},
        latest_case_item=latest_case_item,
        safe_package_path=safe_package_path,
    ) == ["AAPL-Apple-Inc", "MSFT-Microsoft"]


def test_market_package_list_payload_collects_roots_and_filters(tmp_path):
    roots = {"HK": tmp_path / "wiki" / "hk", "US": tmp_path / "wiki" / "us"}
    package_dirs = {
        "HK": [roots["HK"] / "00700" / "2025" / "annual"],
        "US": [roots["US"] / "AAPL" / "2025" / "10-K"],
    }

    result = service.market_package_list_payload(
        market=None,
        query="tencent",
        limit=10,
        market_wiki_roots=roots,
        markets_to_search=lambda market: [market] if market else ["HK", "US"],
        iter_market_packages=lambda code: package_dirs[code],
        read_market_package_summary=lambda path: {
            "package_path": str(path),
            "market": "HK" if "00700" in str(path) else "US",
            "ticker": "00700" if "00700" in str(path) else "AAPL",
            "company_name": "Tencent" if "00700" in str(path) else "Apple",
            "published_at": "2026-04-01" if "00700" in str(path) else "2026-03-01",
        },
        rel_or_abs=lambda path: str(path.relative_to(tmp_path)),
    )

    assert result["markets"] == ["HK", "US"]
    assert result["roots"] == {"HK": "wiki/hk", "US": "wiki/us"}
    assert result["count"] == 1
    assert result["packages"][0]["company_name"] == "Tencent"


def test_package_file_target_validates_relative_file_path(tmp_path):
    package_dir = tmp_path / "package"
    section_file = package_dir / "sections" / "report.md"
    section_file.parent.mkdir(parents=True)
    section_file.write_text("# Report", encoding="utf-8")

    assert service.package_file_target(
        package_dir=package_dir,
        file_path="sections/report.md",
        safe_under=_safe_under,
    ) == section_file

    with pytest.raises(service.MarketReportPackageError) as escape_error:
        service.package_file_target(
            package_dir=package_dir,
            file_path="../manifest.json",
            safe_under=_safe_under,
        )
    assert escape_error.value.status_code == 400

    with pytest.raises(service.MarketReportPackageError) as missing_error:
        service.package_file_target(
            package_dir=package_dir,
            file_path="sections/missing.md",
            safe_under=_safe_under,
        )
    assert missing_error.value.status_code == 404


def test_market_evidence_detail_payload_builds_package_file_url(tmp_path):
    package_dir = tmp_path / "wiki" / "hk package"
    entry = {
        "evidence_id": "ev-1",
        "local_path": "sections/report.md",
    }

    result = service.market_evidence_detail_payload(
        evidence_id="ev-1",
        market="HK",
        package_path=str(package_dir),
        market_code=lambda market: str(market or "").upper(),
        safe_market_package_path=lambda _market, value: Path(value or ""),
        find_market_evidence=lambda evidence_id, **_kwargs: ("HK", package_dir, {**entry, "seen": evidence_id}),
        rel_or_abs=lambda path: str(path.relative_to(tmp_path)),
    )

    assert result["ok"] is True
    assert result["market"] == "HK"
    assert result["package_path"] == "wiki/hk package"
    assert result["evidence"]["seen"] == "ev-1"
    assert result["file_url"] == (
        "/api/market-reports/package-file?market=HK&"
        "package_path=wiki%2Fhk+package&file=sections%2Freport.md"
    )


def test_package_from_us_sec_selector_prefers_package_path_and_maps_missing_ticker(tmp_path):
    package_dir = tmp_path / "wiki" / "us_sec" / "AAPL" / "2025" / "10-K"
    package_dir.mkdir(parents=True)

    assert service.package_from_us_sec_selector(
        {"package_path": str(package_dir), "ticker": "MSFT"},
        latest_case_item_for_ticker=lambda _ticker: {"package_path": "unused"},
        safe_package_path=lambda value: Path(value or ""),
    ) == package_dir

    with pytest.raises(service.MarketReportPackageError) as missing_selector:
        service.package_from_us_sec_selector(
            {},
            latest_case_item_for_ticker=lambda _ticker: None,
            safe_package_path=lambda value: Path(value or ""),
        )
    assert missing_selector.value.status_code == 400
    assert missing_selector.value.detail == "ticker or package_path is required"

    with pytest.raises(service.MarketReportPackageError) as missing_ticker:
        service.package_from_us_sec_selector(
            {"ticker": "TSLA"},
            latest_case_item_for_ticker=lambda _ticker: None,
            safe_package_path=lambda value: Path(value or ""),
        )
    assert missing_ticker.value.status_code == 404
    assert missing_ticker.value.detail == "No package for ticker TSLA"


def test_us_sec_case_set_status_payload_reads_files_and_attaches_semantic_status(tmp_path):
    case_set_path = tmp_path / "case_set.json"
    ingest_report_path = tmp_path / "ingest_report.json"
    case_set_path.write_text(
        json.dumps({
            "items": [
                {
                    "ticker": "AAPL",
                    "quality_status": "pass",
                    "quality_summary": {"xbrl_fact_count": 2},
                    "package_path": "data/wiki/us_sec/AAPL/package",
                }
            ]
        }),
        encoding="utf-8",
    )
    ingest_report_path.write_text(json.dumps({"package_count": 1}), encoding="utf-8")

    result = service.us_sec_case_set_status_payload(
        case_set_path=case_set_path,
        ingest_report_path=ingest_report_path,
        read_json_file=lambda path, default: json.loads(path.read_text(encoding="utf-8")) if path.exists() else default,
        semantic_status_for_item=lambda item: {"status": "ready", "ticker": item["ticker"]},
    )

    assert result["case_set_path"] == str(case_set_path)
    assert result["ingest_report_path"] == str(ingest_report_path)
    assert result["company_count"] == 1
    assert result["quality"] == {"pass": 1}
    assert result["counts"]["xbrl_fact_count"] == 2
    assert result["items"][0]["semantic_status"] == {"status": "ready", "ticker": "AAPL"}
    assert result["ingest_report"]["package_count"] == 1


def test_us_sec_package_detail_by_ticker_payload_resolves_latest_package(tmp_path):
    package_dir = tmp_path / "wiki" / "us_sec" / "AAPL" / "2025" / "10-K"
    package_dir.mkdir(parents=True)

    result = service.us_sec_package_detail_by_ticker_payload(
        "aapl",
        latest_case_item_for_ticker=lambda ticker: {"package_path": str(package_dir), "ticker": ticker},
        safe_package_path=lambda value: Path(value or ""),
        read_package_detail=lambda package: {"package_path": str(package)},
    )

    assert result == {"package_path": str(package_dir)}

    with pytest.raises(service.MarketReportPackageError) as missing:
        service.us_sec_package_detail_by_ticker_payload(
            "tsla",
            latest_case_item_for_ticker=lambda _ticker: None,
            safe_package_path=lambda value: Path(value or ""),
            read_package_detail=lambda package: {"package_path": str(package)},
        )
    assert missing.value.status_code == 404
    assert missing.value.detail == "No package for ticker tsla"


def test_us_sec_package_detail_by_path_payload_uses_safe_path_and_us_reader(tmp_path):
    package_dir = tmp_path / "wiki" / "us_sec" / "AAPL" / "2025" / "10-K"
    package_dir.mkdir(parents=True)
    calls = []

    result = service.us_sec_package_detail_by_path_payload(
        str(package_dir),
        safe_package_path=lambda value: calls.append(("safe", value)) or Path(value or ""),
        read_package_detail=lambda package: calls.append(("read", package)) or {
            "package_path": str(package),
            "sections": [{"file": "financials.md"}],
        },
    )

    assert result == {
        "package_path": str(package_dir),
        "sections": [{"file": "financials.md"}],
    }
    assert calls == [("safe", str(package_dir)), ("read", package_dir)]


def test_run_us_sec_semantic_prestep_builds_rule_and_llm_commands(tmp_path):
    rule_script = tmp_path / "run_market_rule_semantics.py"
    llm_script = tmp_path / "run_market_llm_semantics.py"
    rule_script.write_text("# rule", encoding="utf-8")
    llm_script.write_text("# llm", encoding="utf-8")
    calls = []

    def fake_run(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return Completed()

    result = service.run_us_sec_semantic_prestep(
        {"semantic": True, "dry_run": False},
        executable="python",
        repo_root=tmp_path,
        rule_semantic_script=rule_script,
        llm_semantic_script=llm_script,
        company_dirs_from_payload=lambda _payload: ["AAPL-Apple-Inc"],
        llm_semantic_env=lambda: {"SIQ_LLM_SEMANTIC_PROVIDER": "local"},
        run_command=fake_run,
        command_for_display=lambda args: " ".join(args),
    )

    assert result[0]["companyDir"] == "AAPL-Apple-Inc"
    assert calls[0]["args"] == [
        "python",
        str(rule_script),
        "--market",
        "US",
        "--company",
        "AAPL-Apple-Inc",
        "--skip-existing",
    ]
    assert calls[1]["args"] == [
        "python",
        str(llm_script),
        "--market",
        "US",
        "--company",
        "AAPL-Apple-Inc",
        "--skip-existing",
        "--allow-failures",
    ]
    assert calls[1]["kwargs"]["env"] == {"SIQ_LLM_SEMANTIC_PROVIDER": "local"}


def test_run_us_sec_case_set_ingest_handles_semantic_only(tmp_path):
    report_path = tmp_path / "ingest_report.json"
    report_path.write_text(json.dumps({"package_count": 1}), encoding="utf-8")

    result = service.run_us_sec_case_set_ingest(
        {"semantic": True, "dry_run": False},
        executable="python",
        repo_root=tmp_path,
        ingest_script=tmp_path / "missing_ingest.py",
        case_set_path=tmp_path / "case_set.json",
        report_path=report_path,
        semantic_prestep=lambda _payload: [{
            "companyDir": "AAPL-Apple-Inc",
            "rule": {"returncode": 0},
            "llm": {"returncode": 0},
        }],
        run_command=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run ingest")),
        command_for_display=lambda args: " ".join(args),
        read_json_file=lambda path, default: json.loads(path.read_text(encoding="utf-8")) if path.exists() else default,
    )

    assert result["ok"] is True
    assert result["semantic_only"] is True
    assert result["report"] == {"package_count": 1}


def test_run_us_sec_case_set_ingest_ignores_milvus_flag(tmp_path):
    ingest_script = tmp_path / "ingest_sec_case_set.py"
    ingest_script.write_text("# ingest", encoding="utf-8")
    report_path = tmp_path / "ingest_report.json"
    report_path.write_text("{}", encoding="utf-8")
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    result = service.run_us_sec_case_set_ingest(
        {"milvus": True, "postgres": False, "dry_run": False, "tickers": "AAPL"},
        executable="python",
        repo_root=tmp_path,
        ingest_script=ingest_script,
        case_set_path=tmp_path / "case_set.json",
        report_path=report_path,
        semantic_prestep=lambda _payload: [],
        run_command=fake_run,
        command_for_display=lambda args: " ".join(args),
        read_json_file=lambda path, default: json.loads(path.read_text(encoding="utf-8")) if path.exists() else default,
    )

    assert result["ok"] is True
    assert "--milvus" not in seen["args"]
    assert "--postgres" not in seen["args"]
    assert seen["kwargs"] == {"cwd": tmp_path, "timeout": 1800}


def test_run_us_sec_rebuild_package_uses_temp_source_and_metadata(tmp_path):
    package_dir = tmp_path / "wiki" / "us_sec" / "AAPL" / "2025" / "10-K_demo"
    raw_dir = package_dir / "raw"
    raw_dir.mkdir(parents=True)
    source_path = raw_dir / "filing.htm"
    source_path.write_text("<html><body>10-K</body></html>", encoding="utf-8")
    metadata_path = raw_dir / "filing.metadata.json"
    metadata_path.write_text('{"ticker":"AAPL"}', encoding="utf-8")
    (package_dir / "manifest.json").write_text(json.dumps({"local_source_path": "raw/filing.htm"}), encoding="utf-8")
    build_script = tmp_path / "build_sec_evidence_package.py"
    build_script.write_text("# build", encoding="utf-8")
    seen = {}

    class RebuildCompleted:
        returncode = 0
        stdout = f"{package_dir}\n"
        stderr = "warn\n"

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        temp_source = Path(args[2])
        temp_metadata = Path(args[args.index("--metadata") + 1])
        assert temp_source.name == "filing.htm"
        assert temp_source.read_text(encoding="utf-8") == "<html><body>10-K</body></html>"
        assert temp_metadata.name == "filing.metadata.json"
        assert temp_metadata.read_text(encoding="utf-8") == '{"ticker":"AAPL"}'
        return RebuildCompleted()

    result = service.run_us_sec_rebuild_package(
        "aapl",
        {"force": True},
        executable="python",
        repo_root=tmp_path,
        latest_case_item=lambda ticker: {"package_path": str(package_dir)} if ticker == "AAPL" else None,
        safe_package_path=lambda value: Path(value),
        read_json_file=lambda path, default: json.loads(path.read_text(encoding="utf-8")) if path.exists() else default,
        safe_under=lambda root, path: (
            path
            if root.resolve() in path.resolve().parents or path.resolve() == root.resolve()
            else (_ for _ in ()).throw(ValueError("outside"))
        ),
        package_build_script=build_script,
        output_root=tmp_path / "wiki" / "us_sec",
        run_command=fake_run,
        read_package_detail=lambda package: {"package_path": str(package)},
    )

    assert result["ok"] is True
    assert result["ticker"] == "AAPL"
    assert result["package"] == {"package_path": str(package_dir)}
    assert seen["args"][:2] == ["python", str(build_script)]
    assert seen["args"][3] == "--force"
    assert seen["kwargs"] == {"cwd": tmp_path, "timeout": 900}
