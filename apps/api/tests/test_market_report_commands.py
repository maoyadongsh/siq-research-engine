from pathlib import Path

from services import market_report_commands as commands


def test_market_package_build_args_includes_metadata_parser_output_and_force():
    args = commands.market_package_build_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/build_hk.py"),
        source_path=Path("/repo/downloads/HK/report.pdf"),
        output_root=Path("/repo/data/wiki/hk_reports"),
        metadata_path=Path("/repo/downloads/HK/report.pdf.metadata.json"),
        parser_result_path=Path("/repo/parser/task-1"),
        force=True,
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/build_hk.py",
        "/repo/downloads/HK/report.pdf",
        "--metadata",
        "/repo/downloads/HK/report.pdf.metadata.json",
        "--parser-result",
        "/repo/parser/task-1",
        "--output-root",
        "/repo/data/wiki/hk_reports",
        "--force",
    ]


def test_market_package_build_args_omits_optional_flags():
    args = commands.market_package_build_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/build_us.py"),
        source_path=Path("/repo/downloads/US/report.html"),
        output_root=Path("/repo/data/wiki/us_sec"),
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/build_us.py",
        "/repo/downloads/US/report.html",
        "--output-root",
        "/repo/data/wiki/us_sec",
    ]


def test_market_package_import_args_uses_us_package_flag_and_database_url():
    args = commands.market_package_import_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/import_us.py"),
        market="US",
        package_dir=Path("/repo/data/wiki/us_sec/AAPL/package"),
        payload={"database_url": "postgres://secret", "ddl": True},
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/import_us.py",
        "--package",
        "/repo/data/wiki/us_sec/AAPL/package",
        "--database-url",
        "postgres://secret",
        "--ddl",
    ]


def test_market_package_import_args_uses_positional_package_for_non_us():
    args = commands.market_package_import_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/import_hk.py"),
        market="HK",
        package_dir=Path("/repo/data/wiki/hk_reports/00700/package"),
        payload={"run_ddl": True},
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/import_hk.py",
        "/repo/data/wiki/hk_reports/00700/package",
        "--ddl",
    ]


def test_market_vector_ingest_args_defaults_to_dry_run_and_optional_flags():
    args, dry_run = commands.market_vector_ingest_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/ingest.py"),
        package_dir=Path("/repo/data/wiki/hk_reports/00700/package"),
        payload={
            "collection": "siq_market",
            "embed_url": "http://embed.local",
            "embed_model": "text-embedding-3-small",
            "vector_dim": 1536,
        },
    )

    assert dry_run is True
    assert args == [
        "/usr/bin/python",
        "/repo/scripts/ingest.py",
        "--package",
        "/repo/data/wiki/hk_reports/00700/package",
        "--batch-tag",
        "market-evidence",
        "--collection",
        "siq_market",
        "--embed-url",
        "http://embed.local",
        "--embed-model",
        "text-embedding-3-small",
        "--vector-dim",
        "1536",
        "--dry-run",
    ]


def test_market_vector_ingest_args_can_disable_dry_run_and_override_batch():
    args, dry_run = commands.market_vector_ingest_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/ingest.py"),
        package_dir=Path("/repo/data/wiki/us_sec/AAPL/package"),
        payload={"batch_tag": "prod-load", "dry_run": False},
    )

    assert dry_run is False
    assert args == [
        "/usr/bin/python",
        "/repo/scripts/ingest.py",
        "--package",
        "/repo/data/wiki/us_sec/AAPL/package",
        "--batch-tag",
        "prod-load",
    ]


def test_market_ingestion_eval_args_resolves_relative_paths_against_repo_root():
    args, output, markdown = commands.market_ingestion_eval_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/eval.py"),
        payload={"output": "tmp/eval.json", "markdown": "tmp/eval.md"},
        repo_root=Path("/repo"),
        default_output=Path("/repo/default/eval.json"),
        default_markdown=Path("/repo/default/eval.md"),
    )

    assert output == Path("/repo/tmp/eval.json")
    assert markdown == Path("/repo/tmp/eval.md")
    assert args == [
        "/usr/bin/python",
        "/repo/scripts/eval.py",
        "--output",
        "/repo/tmp/eval.json",
        "--markdown",
        "/repo/tmp/eval.md",
    ]


def test_market_ingestion_eval_args_keeps_absolute_defaults():
    args, output, markdown = commands.market_ingestion_eval_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/eval.py"),
        payload={},
        repo_root=Path("/repo"),
        default_output=Path("/data/eval.json"),
        default_markdown=Path("/data/eval.md"),
    )

    assert output == Path("/data/eval.json")
    assert markdown == Path("/data/eval.md")
    assert args[-4:] == ["--output", "/data/eval.json", "--markdown", "/data/eval.md"]


def test_us_sec_ingest_args_defaults_to_dry_run():
    args = commands.us_sec_ingest_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/ingest_sec_case_set.py"),
        case_set_path=Path("/repo/data/wiki/us_sec/case_set.json"),
        report_path=Path("/repo/data/wiki/us_sec/ingest_report.json"),
        payload={},
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/ingest_sec_case_set.py",
        "--case-set",
        "/repo/data/wiki/us_sec/case_set.json",
        "--report",
        "/repo/data/wiki/us_sec/ingest_report.json",
        "--dry-run",
    ]


def test_us_sec_ingest_args_includes_optional_flags_and_filters():
    args = commands.us_sec_ingest_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/ingest_sec_case_set.py"),
        case_set_path=Path("/repo/data/wiki/us_sec/case_set.json"),
        report_path=Path("/repo/data/wiki/us_sec/ingest_report.json"),
        payload={
            "include_fail": True,
            "postgres": True,
            "milvus": True,
            "ddl": True,
            "dry_run": False,
        },
        tickers="AAPL,MSFT",
        batch_tag="market-evidence:2026",
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/ingest_sec_case_set.py",
        "--case-set",
        "/repo/data/wiki/us_sec/case_set.json",
        "--report",
        "/repo/data/wiki/us_sec/ingest_report.json",
        "--include-fail",
        "--postgres",
        "--milvus",
        "--ddl",
        "--tickers",
        "AAPL,MSFT",
        "--batch-tag",
        "market-evidence:2026",
    ]


def test_us_sec_rebuild_package_args_includes_force_metadata_and_output_root():
    args = commands.us_sec_rebuild_package_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/build_sec_evidence_package.py"),
        source_path=Path("/tmp/sec-rebuild/filing.htm"),
        metadata_path=Path("/tmp/sec-rebuild/filing.metadata.json"),
        output_root=Path("/repo/data/wiki/us_sec"),
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/build_sec_evidence_package.py",
        "/tmp/sec-rebuild/filing.htm",
        "--force",
        "--metadata",
        "/tmp/sec-rebuild/filing.metadata.json",
        "--output-root",
        "/repo/data/wiki/us_sec",
    ]
