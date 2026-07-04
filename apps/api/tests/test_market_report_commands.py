from pathlib import Path

from services import market_report_commands as commands


class Completed:
    def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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


def test_market_build_script_and_parser_result_helpers_cover_market_matrix():
    market_scripts = {
        "US": Path("/repo/scripts/build_us.py"),
        "HK": Path("/repo/scripts/build_hk.py"),
        "JP": Path("/repo/scripts/build_jp.py"),
        "KR": Path("/repo/scripts/build_kr.py"),
        "EU": Path("/repo/scripts/build_eu_pdf.py"),
    }
    esef_script = Path("/repo/scripts/build_eu_esef.py")
    eu_pdf = Path("/tmp/report.pdf")
    eu_xhtml = Path("/tmp/report.xhtml")
    eu_zip_upper = Path("/tmp/report.ZIP")

    assert commands.select_market_build_script(
        market="EU",
        source_path=eu_pdf,
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) == market_scripts["EU"]
    assert commands.select_market_build_script(
        market="EU",
        source_path=eu_xhtml,
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) == esef_script
    assert commands.select_market_build_script(
        market="EU",
        source_path=eu_zip_upper,
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) == esef_script
    assert commands.select_market_build_script(
        market="HK",
        source_path=Path("/tmp/report.pdf"),
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) == market_scripts["HK"]

    assert commands.market_build_requires_parser_result(
        market="HK",
        source_path=Path("/tmp/report.pdf"),
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) is True
    assert commands.market_build_requires_parser_result(
        market="KR",
        source_path=Path("/tmp/report.pdf"),
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) is True
    assert commands.market_build_requires_parser_result(
        market="EU",
        source_path=eu_pdf,
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) is True
    assert commands.market_build_requires_parser_result(
        market="EU",
        source_path=eu_xhtml,
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) is False
    assert commands.market_build_requires_parser_result(
        market="US",
        source_path=Path("/tmp/report.html"),
        market_build_scripts=market_scripts,
        eu_esef_package_build_script=esef_script,
    ) is False

    assert commands.market_build_accepts_parser_result(
        market="JP",
        script=market_scripts["JP"],
        eu_esef_package_build_script=esef_script,
    ) is True
    assert commands.market_build_accepts_parser_result(
        market="KR",
        script=market_scripts["KR"],
        eu_esef_package_build_script=esef_script,
    ) is True
    assert commands.market_build_accepts_parser_result(
        market="EU",
        script=market_scripts["EU"],
        eu_esef_package_build_script=esef_script,
    ) is True
    assert commands.market_build_accepts_parser_result(
        market="EU",
        script=esef_script,
        eu_esef_package_build_script=esef_script,
    ) is False
    assert commands.market_build_accepts_parser_result(
        market="US",
        script=market_scripts["US"],
        eu_esef_package_build_script=esef_script,
    ) is False


def test_market_package_build_plan_prefers_download_path_and_adjacent_metadata(tmp_path):
    repo_root = tmp_path / "repo"
    downloads_root = tmp_path / "downloads"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    build_script = repo_root / "scripts" / "build_us.py"
    source_from_download = downloads_root / "US" / "Apple" / "2025" / "report.html"
    ignored_source = repo_root / "ignored.html"
    metadata = source_from_download.with_suffix(source_from_download.suffix + ".metadata.json")
    for path in (build_script, source_from_download, ignored_source, metadata):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    plan = commands.build_market_package_build_plan(
        payload={
            "download_relative_path": "US/Apple/2025/report.html",
            "source_path": "ignored.html",
            "force": True,
        },
        market="US",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"US": build_script},
        eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
        safe_download_path=lambda value: source_from_download,
        adjacent_metadata_path=lambda path: metadata if path == source_from_download else None,
    )

    assert plan.market == "US"
    assert plan.source_path == source_from_download
    assert plan.metadata_path == metadata
    assert plan.parser_result_path is None
    assert plan.script == build_script
    assert plan.output_root == wiki_roots["US"]
    assert plan.force is True


def test_market_package_build_plan_resolves_relative_source_and_metadata(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    build_script = repo_root / "scripts" / "build_us.py"
    source = repo_root / "downloads" / "US" / "report.html"
    metadata = repo_root / "downloads" / "US" / "report.metadata.json"
    for path in (build_script, source, metadata):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    plan = commands.build_market_package_build_plan(
        payload={
            "source_path": "downloads/US/report.html",
            "metadata_path": "downloads/US/report.metadata.json",
        },
        market="US",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"US": build_script},
        eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
        safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        adjacent_metadata_path=lambda path: (_ for _ in ()).throw(AssertionError("explicit metadata should win")),
    )

    assert plan.source_path == source
    assert plan.metadata_path == metadata


def test_market_package_build_plan_supports_legacy_pdf_path_alias(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    build_script = repo_root / "scripts" / "build_us.py"
    source = repo_root / "downloads" / "US" / "legacy.pdf"
    ignored_pdf = repo_root / "downloads" / "US" / "ignored.pdf"
    for path in (build_script, source, ignored_pdf):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    alias_plan = commands.build_market_package_build_plan(
        payload={"pdf_path": "downloads/US/legacy.pdf"},
        market="US",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"US": build_script},
        eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
        safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        adjacent_metadata_path=lambda path: None,
    )
    source_wins_plan = commands.build_market_package_build_plan(
        payload={"source_path": source, "pdf_path": ignored_pdf},
        market="US",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"US": build_script},
        eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
        safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        adjacent_metadata_path=lambda path: None,
    )

    assert alias_plan.source_path == source
    assert source_wins_plan.source_path == source


def test_market_package_build_plan_preserves_absolute_source_path(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    build_script = repo_root / "scripts" / "build_us.py"
    source = tmp_path / "external-downloads" / "US" / "report.html"
    for path in (build_script, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    plan = commands.build_market_package_build_plan(
        payload={"source_path": source},
        market="US",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"US": build_script},
        eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
        safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        adjacent_metadata_path=lambda path: None,
    )

    assert plan.source_path == source
    assert plan.output_root == wiki_roots["US"]


def test_market_package_build_plan_uses_absolute_metadata_path(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    build_script = repo_root / "scripts" / "build_us.py"
    source = repo_root / "downloads" / "US" / "report.html"
    metadata = tmp_path / "metadata" / "report.metadata.json"
    for path in (build_script, source, metadata):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    plan = commands.build_market_package_build_plan(
        payload={"source_path": source, "metadata_path": metadata},
        market="US",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"US": build_script},
        eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
        safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        adjacent_metadata_path=lambda path: (_ for _ in ()).throw(AssertionError("explicit metadata should win")),
    )

    assert plan.metadata_path == metadata


def test_market_package_build_plan_requires_source_or_download_path(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    build_script = repo_root / "scripts" / "build_us.py"
    build_script.parent.mkdir(parents=True, exist_ok=True)
    build_script.write_text("x", encoding="utf-8")

    try:
        commands.build_market_package_build_plan(
            payload={},
            market="US",
            repo_root=repo_root,
            market_wiki_roots=wiki_roots,
            market_build_scripts={"US": build_script},
            eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
            safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
            adjacent_metadata_path=lambda path: None,
        )
    except commands.MarketPackageBuildPlanError as exc:
        assert exc.status_code == 400
        assert exc.detail == "source_path or download_relative_path is required"
    else:
        raise AssertionError("expected missing source/download error")


def test_market_package_build_plan_reports_missing_selected_source(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    build_script = repo_root / "scripts" / "build_us.py"
    missing_download = tmp_path / "downloads" / "US" / "missing.html"
    build_script.parent.mkdir(parents=True, exist_ok=True)
    build_script.write_text("x", encoding="utf-8")

    cases = [
        {"source_path": "downloads/US/missing.html"},
        {"download_relative_path": "US/missing.html"},
    ]
    for payload in cases:
        try:
            commands.build_market_package_build_plan(
                payload=payload,
                market="US",
                repo_root=repo_root,
                market_wiki_roots=wiki_roots,
                market_build_scripts={"US": build_script},
                eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
                safe_download_path=lambda value: missing_download,
                adjacent_metadata_path=lambda path: None,
            )
        except commands.MarketPackageBuildPlanError as exc:
            assert exc.status_code == 404
            assert exc.detail == "source_path not found"
        else:
            raise AssertionError("expected source_path not found error")


def test_market_package_build_plan_reports_missing_selected_script(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    missing_script = repo_root / "scripts" / "missing_build_us.py"
    source = repo_root / "downloads" / "US" / "report.html"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("x", encoding="utf-8")

    try:
        commands.build_market_package_build_plan(
            payload={"source_path": source},
            market="US",
            repo_root=repo_root,
            market_wiki_roots=wiki_roots,
            market_build_scripts={"US": missing_script},
            eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
            safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
            adjacent_metadata_path=lambda path: None,
        )
    except commands.MarketPackageBuildPlanError as exc:
        assert exc.status_code == 404
        assert exc.detail == f"Missing package build script: {missing_script}"
    else:
        raise AssertionError("expected missing build script error")


def test_market_package_build_plan_requires_parser_result_for_hk_kr_and_eu_pdf(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"HK": tmp_path / "wiki" / "hk", "KR": tmp_path / "wiki" / "kr", "EU": tmp_path / "wiki" / "eu"}
    hk_script = repo_root / "scripts" / "build_hk.py"
    kr_script = repo_root / "scripts" / "build_kr.py"
    eu_pdf_script = repo_root / "scripts" / "build_eu_pdf.py"
    eu_esef_script = repo_root / "scripts" / "build_eu_esef.py"
    hk_source = repo_root / "downloads" / "HK" / "report.pdf"
    kr_source = repo_root / "downloads" / "KR" / "report.pdf"
    eu_pdf_source = repo_root / "downloads" / "EU" / "report.pdf"
    parser_result = repo_root / "parser" / "task-1"
    for path in (hk_script, kr_script, eu_pdf_script, eu_esef_script, hk_source, kr_source, eu_pdf_source, parser_result):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    common = {
        "repo_root": repo_root,
        "market_wiki_roots": wiki_roots,
        "market_build_scripts": {"HK": hk_script, "KR": kr_script, "EU": eu_pdf_script},
        "eu_esef_package_build_script": eu_esef_script,
        "safe_download_path": lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        "adjacent_metadata_path": lambda path: None,
    }
    for market, source in (("HK", hk_source), ("KR", kr_source), ("EU", eu_pdf_source)):
        try:
            commands.build_market_package_build_plan(
                payload={"source_path": source},
                market=market,
                **common,
            )
        except commands.MarketPackageBuildPlanError as exc:
            assert exc.status_code == 400
            assert exc.detail == f"parser_result is required for {market} package builds"
        else:
            raise AssertionError("expected missing parser_result error")

    plan = commands.build_market_package_build_plan(
        payload={"source_path": hk_source, "parser_result": parser_result},
        market="HK",
        **common,
    )

    assert plan.parser_result_path == parser_result


def test_market_package_build_plan_accepts_eu_pdf_with_parser_result_directory(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"EU": tmp_path / "wiki" / "eu"}
    eu_pdf_script = repo_root / "scripts" / "build_eu_pdf.py"
    eu_esef_script = repo_root / "scripts" / "build_eu_esef.py"
    source = repo_root / "downloads" / "EU" / "report.pdf"
    parser_result = repo_root / "parser" / "task-1"
    for path in (eu_pdf_script, eu_esef_script, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    parser_result.mkdir(parents=True)

    plan = commands.build_market_package_build_plan(
        payload={"source_path": source, "parser_result": parser_result},
        market="EU",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"EU": eu_pdf_script},
        eu_esef_package_build_script=eu_esef_script,
        safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        adjacent_metadata_path=lambda path: None,
    )

    assert plan.script == eu_pdf_script
    assert plan.parser_result_path == parser_result


def test_market_package_build_plan_ignores_parser_result_for_us(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"US": tmp_path / "wiki" / "us_sec"}
    build_script = repo_root / "scripts" / "build_us.py"
    source = repo_root / "downloads" / "US" / "report.html"
    for path in (build_script, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    plan = commands.build_market_package_build_plan(
        payload={"source_path": source, "parser_result": "missing-parser-result"},
        market="US",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"US": build_script},
        eu_esef_package_build_script=repo_root / "scripts" / "build_eu_esef.py",
        safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        adjacent_metadata_path=lambda path: None,
    )

    assert plan.parser_result_path is None


def test_market_package_build_plan_accepts_parser_result_for_jp_and_kr(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"JP": tmp_path / "wiki" / "jp", "KR": tmp_path / "wiki" / "kr"}
    jp_script = repo_root / "scripts" / "build_jp.py"
    kr_script = repo_root / "scripts" / "build_kr.py"
    jp_source = repo_root / "downloads" / "JP" / "report.pdf"
    kr_source = repo_root / "downloads" / "KR" / "report.pdf"
    parser_result = repo_root / "parser" / "task-1"
    for path in (jp_script, kr_script, jp_source, kr_source, parser_result):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    common = {
        "repo_root": repo_root,
        "market_wiki_roots": wiki_roots,
        "market_build_scripts": {"JP": jp_script, "KR": kr_script},
        "eu_esef_package_build_script": repo_root / "scripts" / "build_eu_esef.py",
        "safe_download_path": lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        "adjacent_metadata_path": lambda path: None,
    }

    jp_plan = commands.build_market_package_build_plan(
        payload={"source_path": jp_source, "parser_result": parser_result},
        market="JP",
        **common,
    )
    kr_plan = commands.build_market_package_build_plan(
        payload={"source_path": kr_source, "parser_result": parser_result},
        market="KR",
        **common,
    )

    assert jp_plan.parser_result_path == parser_result
    assert kr_plan.parser_result_path == parser_result


def test_market_package_build_plan_routes_all_esef_suffixes_without_parser_result(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"EU": tmp_path / "wiki" / "eu"}
    eu_pdf_script = repo_root / "scripts" / "build_eu_pdf.py"
    eu_esef_script = repo_root / "scripts" / "build_eu_esef.py"
    for path in (eu_pdf_script, eu_esef_script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    for suffix in (".zip", ".xhtml", ".html", ".htm", ".xml", ".xbrl"):
        source = repo_root / "downloads" / "EU" / f"report{suffix}"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("x", encoding="utf-8")

        plan = commands.build_market_package_build_plan(
            payload={"source_path": source},
            market="EU",
            repo_root=repo_root,
            market_wiki_roots=wiki_roots,
            market_build_scripts={"EU": eu_pdf_script},
            eu_esef_package_build_script=eu_esef_script,
            safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
            adjacent_metadata_path=lambda path: None,
        )

        assert plan.script == eu_esef_script
        assert plan.parser_result_path is None


def test_market_package_build_plan_ignores_parser_result_for_eu_esef(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"EU": tmp_path / "wiki" / "eu"}
    eu_pdf_script = repo_root / "scripts" / "build_eu_pdf.py"
    eu_esef_script = repo_root / "scripts" / "build_eu_esef.py"
    source = repo_root / "downloads" / "EU" / "report.xhtml"
    for path in (eu_pdf_script, eu_esef_script, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    plan = commands.build_market_package_build_plan(
        payload={"source_path": source, "parser_result": "missing-parser-result"},
        market="EU",
        repo_root=repo_root,
        market_wiki_roots=wiki_roots,
        market_build_scripts={"EU": eu_pdf_script},
        eu_esef_package_build_script=eu_esef_script,
        safe_download_path=lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        adjacent_metadata_path=lambda path: None,
    )

    assert plan.script == eu_esef_script
    assert plan.parser_result_path is None


def test_market_package_build_plan_reports_missing_metadata_and_parser_result(tmp_path):
    repo_root = tmp_path / "repo"
    wiki_roots = {"HK": tmp_path / "wiki" / "hk"}
    build_script = repo_root / "scripts" / "build_hk.py"
    source = repo_root / "downloads" / "HK" / "report.pdf"
    for path in (build_script, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    common = {
        "market": "HK",
        "repo_root": repo_root,
        "market_wiki_roots": wiki_roots,
        "market_build_scripts": {"HK": build_script},
        "eu_esef_package_build_script": repo_root / "scripts" / "build_eu_esef.py",
        "safe_download_path": lambda value: (_ for _ in ()).throw(AssertionError("download path should not be used")),
        "adjacent_metadata_path": lambda path: None,
    }
    cases = [
        ({"source_path": source, "metadata_path": "missing.json", "parser_result": source}, 404, "metadata_path not found"),
        ({"source_path": source, "parser_result": "missing-parser-result"}, 404, "parser_result not found"),
    ]
    for payload, status_code, detail in cases:
        try:
            commands.build_market_package_build_plan(payload=payload, **common)
        except commands.MarketPackageBuildPlanError as exc:
            assert exc.status_code == status_code
            assert exc.detail == detail
        else:
            raise AssertionError("expected plan error")


def test_market_package_import_args_uses_us_package_flag_without_database_url():
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
        "--ddl",
    ]
    assert "postgres://secret" not in args
    assert "--database-url" not in args


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


def test_market_package_import_env_defaults_market_database():
    market_databases = {
        "US": "siq_us",
        "HK": "siq_hk",
        "JP": "siq_jp",
        "KR": "siq_kr",
        "EU": "siq_eu",
    }

    for market, database in market_databases.items():
        env = commands.market_package_import_env(market, market_databases)
        assert env[f"SIQ_{market}_PGDATABASE"] == database
        assert "DATABASE_URL" not in env


def test_market_package_import_env_sanitizes_inherited_database_url_for_hk():
    hk_env = commands.market_package_import_env(
        "HK",
        {"HK": "siq_hk"},
        base_env={
            "DATABASE_URL": "postgresql://postgres:secret@db/siq",
            "PATH": "/usr/bin",
        },
    )

    assert hk_env["SIQ_HK_PGDATABASE"] == "siq_hk"
    assert hk_env["PATH"] == "/usr/bin"
    assert "DATABASE_URL" not in hk_env


def test_market_package_import_env_uses_explicit_database_url_over_inherited_and_hk_default():
    hk_env = commands.market_package_import_env(
        "HK",
        {"HK": "siq_hk"},
        base_env={
            "DATABASE_URL": "postgresql://postgres:inherited@db/siq",
            "PATH": "/usr/bin",
        },
        database_url="postgresql://postgres:explicit@db/siq_private",
    )

    assert hk_env["DATABASE_URL"] == "postgresql://postgres:explicit@db/siq_private"
    assert hk_env["SIQ_HK_PGDATABASE"] == "siq_hk"
    assert hk_env["PATH"] == "/usr/bin"


def test_market_package_import_plan_selects_script_and_package_dir(tmp_path):
    package_dir = tmp_path / "wiki" / "hk_reports" / "00700" / "2025" / "annual_demo"
    script = tmp_path / "scripts" / "import_hk.py"
    script.parent.mkdir(parents=True)
    script.write_text("# import", encoding="utf-8")
    seen = {}

    def safe_package(market: str, value: str) -> Path:
        seen["market"] = market
        seen["value"] = value
        return package_dir

    plan = commands.build_market_package_import_plan(
        payload={"package_path": "data/wiki/hk_reports/00700/2025/annual_demo"},
        market="HK",
        market_import_scripts={"HK": script},
        safe_market_package_path=safe_package,
    )

    assert plan.market == "HK"
    assert plan.package_dir == package_dir
    assert plan.script == script
    assert seen == {
        "market": "HK",
        "value": "data/wiki/hk_reports/00700/2025/annual_demo",
    }


def test_market_package_import_plan_reports_missing_script(tmp_path):
    package_dir = tmp_path / "wiki" / "us_sec" / "AAPL" / "2025" / "10-K_demo"
    missing_script = tmp_path / "scripts" / "missing_import_us.py"

    try:
        commands.build_market_package_import_plan(
            payload={"package_path": str(package_dir)},
            market="US",
            market_import_scripts={"US": missing_script},
            safe_market_package_path=lambda market, value: package_dir,
        )
    except commands.MarketPackagePlanError as exc:
        assert exc.status_code == 404
        assert exc.detail == f"Missing package import script: {missing_script}"
    else:
        raise AssertionError("expected missing import script error")


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


def test_market_vector_ingest_args_defaults_hk_collection():
    args, dry_run = commands.market_vector_ingest_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/ingest.py"),
        package_dir=Path("/repo/data/wiki/hk_reports/00700/package"),
        payload={},
        market="HK",
        market_vector_collections={"HK": "siq_hk_reports"},
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
        "siq_hk_reports",
        "--dry-run",
    ]


def test_market_vector_ingest_args_explicit_hk_collection_wins_over_default():
    args, _dry_run = commands.market_vector_ingest_args(
        executable="/usr/bin/python",
        script=Path("/repo/scripts/ingest.py"),
        package_dir=Path("/repo/data/wiki/hk_reports/00700/package"),
        payload={"collection": "explicit_hk_collection"},
        market="HK",
        market_vector_collections={"HK": "siq_hk_reports"},
    )

    collection_index = args.index("--collection")
    assert args[collection_index + 1] == "explicit_hk_collection"
    assert "siq_hk_reports" not in args


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


def test_market_vector_ingest_plan_selects_script_package_and_dry_run(tmp_path):
    package_dir = tmp_path / "wiki" / "hk_reports" / "00700" / "2025" / "annual_demo"
    script = tmp_path / "scripts" / "ingest_market_package.py"
    script.parent.mkdir(parents=True)
    script.write_text("# ingest", encoding="utf-8")

    dry_run_plan = commands.build_market_vector_ingest_plan(
        payload={"package_path": str(package_dir)},
        market="HK",
        vector_ingest_script=script,
        safe_market_package_path=lambda market, value: package_dir,
    )
    prod_plan = commands.build_market_vector_ingest_plan(
        payload={"package_path": str(package_dir), "dry_run": False},
        market="HK",
        vector_ingest_script=script,
        safe_market_package_path=lambda market, value: package_dir,
    )

    assert dry_run_plan.market == "HK"
    assert dry_run_plan.package_dir == package_dir
    assert dry_run_plan.script == script
    assert dry_run_plan.dry_run is True
    assert prod_plan.dry_run is False


def test_market_vector_ingest_plan_reports_missing_script(tmp_path):
    package_dir = tmp_path / "wiki" / "hk_reports" / "00700" / "2025" / "annual_demo"
    missing_script = tmp_path / "scripts" / "missing_ingest.py"

    try:
        commands.build_market_vector_ingest_plan(
            payload={"package_path": str(package_dir)},
            market="HK",
            vector_ingest_script=missing_script,
            safe_market_package_path=lambda market, value: package_dir,
        )
    except commands.MarketPackagePlanError as exc:
        assert exc.status_code == 404
        assert exc.detail == f"Missing vector ingest script: {missing_script}"
    else:
        raise AssertionError("expected missing vector ingest script error")


def test_market_ingestion_eval_plan_resolves_outputs_and_keeps_script(tmp_path):
    repo_root = tmp_path / "repo"
    eval_script = repo_root / "scripts" / "maintenance" / "run_market_ingestion_eval.py"
    eval_script.parent.mkdir(parents=True)
    eval_script.write_text("# eval", encoding="utf-8")

    plan = commands.build_market_ingestion_eval_plan(
        payload={"output": "tmp/eval.json", "markdown": tmp_path / "reports" / "eval.md"},
        eval_script=eval_script,
        repo_root=repo_root,
        default_output=repo_root / "default" / "eval.json",
        default_markdown=repo_root / "default" / "eval.md",
    )

    assert plan.script == eval_script
    assert plan.output_path == repo_root / "tmp" / "eval.json"
    assert plan.markdown_path == tmp_path / "reports" / "eval.md"


def test_market_ingestion_eval_plan_uses_absolute_defaults(tmp_path):
    repo_root = tmp_path / "repo"
    eval_script = repo_root / "scripts" / "maintenance" / "run_market_ingestion_eval.py"
    eval_script.parent.mkdir(parents=True)
    eval_script.write_text("# eval", encoding="utf-8")
    default_output = tmp_path / "reports" / "default_eval.json"
    default_markdown = tmp_path / "reports" / "default_eval.md"

    plan = commands.build_market_ingestion_eval_plan(
        payload={},
        eval_script=eval_script,
        repo_root=repo_root,
        default_output=default_output,
        default_markdown=default_markdown,
    )

    assert plan.output_path == default_output
    assert plan.markdown_path == default_markdown


def test_market_ingestion_eval_plan_reports_missing_script(tmp_path):
    missing_script = tmp_path / "scripts" / "maintenance" / "run_market_ingestion_eval.py"

    try:
        commands.build_market_ingestion_eval_plan(
            payload={},
            eval_script=missing_script,
            repo_root=tmp_path / "repo",
            default_output=tmp_path / "reports" / "eval.json",
            default_markdown=tmp_path / "reports" / "eval.md",
        )
    except commands.MarketPackagePlanError as exc:
        assert exc.status_code == 404
        assert exc.detail == f"Missing eval script: {missing_script}"
    else:
        raise AssertionError("expected missing eval script error")


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


def test_us_sec_rebuild_package_plan_selects_source_metadata_and_script(tmp_path):
    package_dir = tmp_path / "wiki" / "us_sec" / "AAPL" / "2025" / "10-K_demo"
    raw_dir = package_dir / "raw"
    raw_dir.mkdir(parents=True)
    source = raw_dir / "filing.htm"
    source.write_text("<html>10-K</html>", encoding="utf-8")
    metadata = raw_dir / "filing.metadata.json"
    metadata.write_text('{"ticker":"AAPL"}', encoding="utf-8")
    (package_dir / "manifest.json").write_text(
        '{"local_source_path":"raw/filing.htm"}',
        encoding="utf-8",
    )
    script = tmp_path / "scripts" / "build_sec_evidence_package.py"
    script.parent.mkdir(parents=True)
    script.write_text("# build", encoding="utf-8")

    plan = commands.build_us_sec_rebuild_package_plan(
        ticker="aapl",
        latest_case_item=lambda ticker: {"ticker": ticker, "package_path": str(package_dir)},
        safe_package_path=lambda value: package_dir if value == str(package_dir) else None,
        read_json_file=lambda path, default: {"local_source_path": "raw/filing.htm"},
        safe_under=lambda root, path: path,
        package_build_script=script,
        output_root=tmp_path / "wiki" / "us_sec",
    )

    assert plan.ticker == "AAPL"
    assert plan.package_dir == package_dir
    assert plan.source_path == source
    assert plan.metadata_path == metadata
    assert plan.script == script
    assert plan.output_root == tmp_path / "wiki" / "us_sec"


def test_us_sec_rebuild_package_plan_uses_default_source_and_optional_metadata(tmp_path):
    package_dir = tmp_path / "wiki" / "us_sec" / "MSFT" / "2025" / "10-K_demo"
    source = package_dir / "raw" / "filing.htm"
    source.parent.mkdir(parents=True)
    source.write_text("<html>10-K</html>", encoding="utf-8")
    script = tmp_path / "scripts" / "build_sec_evidence_package.py"
    script.parent.mkdir(parents=True)
    script.write_text("# build", encoding="utf-8")

    plan = commands.build_us_sec_rebuild_package_plan(
        ticker="MSFT",
        latest_case_item=lambda ticker: {"ticker": ticker, "package_path": str(package_dir)},
        safe_package_path=lambda value: package_dir,
        read_json_file=lambda path, default: {},
        safe_under=lambda root, path: path,
        package_build_script=script,
        output_root=tmp_path / "wiki" / "us_sec",
    )

    assert plan.source_path == source
    assert plan.metadata_path is None


def test_us_sec_rebuild_package_plan_reports_missing_case_source_and_script(tmp_path):
    package_dir = tmp_path / "wiki" / "us_sec" / "AAPL" / "2025" / "10-K_demo"
    script = tmp_path / "scripts" / "build_sec_evidence_package.py"
    source = package_dir / "raw" / "filing.htm"
    source.parent.mkdir(parents=True)

    cases = [
        (
            {"latest_case_item": lambda ticker: None, "package_build_script": script},
            404,
            "No package for ticker AAPL",
        ),
        (
            {"latest_case_item": lambda ticker: {"package_path": str(package_dir)}, "package_build_script": script},
            404,
            "Raw SEC filing source not found in package",
        ),
    ]
    for overrides, status_code, detail in cases:
        try:
            commands.build_us_sec_rebuild_package_plan(
                ticker="aapl",
                safe_package_path=lambda value: package_dir,
                read_json_file=lambda path, default: {},
                safe_under=lambda root, path: path,
                output_root=tmp_path / "wiki" / "us_sec",
                **overrides,
            )
        except commands.MarketPackagePlanError as exc:
            assert exc.status_code == status_code
            assert exc.detail == detail
        else:
            raise AssertionError("expected US SEC rebuild plan error")

    source.write_text("<html>10-K</html>", encoding="utf-8")
    try:
        commands.build_us_sec_rebuild_package_plan(
            ticker="aapl",
            latest_case_item=lambda ticker: {"package_path": str(package_dir)},
            safe_package_path=lambda value: package_dir,
            read_json_file=lambda path, default: {},
            safe_under=lambda root, path: path,
            package_build_script=script,
            output_root=tmp_path / "wiki" / "us_sec",
        )
    except commands.MarketPackagePlanError as exc:
        assert exc.status_code == 404
        assert exc.detail == f"Missing package build script: {script}"
    else:
        raise AssertionError("expected missing package build script error")


def test_market_package_build_result_payload_handles_failure_missing_path_and_success():
    failed = commands.market_package_build_result_payload(
        completed=Completed(returncode=2, stdout="x" * 5000, stderr="bad"),
        command="python build.py",
    )
    missing_path = commands.market_package_build_result_payload(
        completed=Completed(returncode=0, stdout="", stderr=""),
        command="python build.py",
    )
    succeeded = commands.market_package_build_result_payload(
        completed=Completed(returncode=0, stdout="log\n/tmp/package\n", stderr="warn"),
        package={"package_path": "/tmp/package"},
        command="python build.py",
    )

    assert failed["ok"] is False
    assert failed["returncode"] == 2
    assert failed["stdout"] == "x" * 4000
    assert missing_path == {
        "ok": False,
        "returncode": 0,
        "stdout": "",
        "stderr": "Package build did not print a package path",
        "command": "python build.py",
    }
    assert succeeded == {
        "ok": True,
        "package": {"package_path": "/tmp/package"},
        "stdout": "log\n/tmp/package\n",
        "stderr": "warn",
        "command": "python build.py",
    }


def test_market_package_import_result_payload_extracts_parse_run_id_only_on_success():
    ok = commands.market_package_import_result_payload(
        completed=Completed(returncode=0, stdout="log\nparse-run-1\n", stderr=""),
        command="python import.py --database-url ***",
    )
    failed = commands.market_package_import_result_payload(
        completed=Completed(returncode=1, stdout="parse-run-should-not-leak\n", stderr="failed"),
        command="python import.py",
    )

    assert ok["ok"] is True
    assert ok["parse_run_id"] == "parse-run-1"
    assert ok["command"] == "python import.py --database-url ***"
    assert failed["ok"] is False
    assert failed["parse_run_id"] is None


def test_market_vector_ingest_result_payload_parses_summary_and_tolerates_bad_stdout():
    parsed = commands.market_vector_ingest_result_payload(
        completed=Completed(returncode=0, stdout='log\n{"inserted": 3}\n', stderr="warn"),
        dry_run=True,
        command="python ingest.py --dry-run",
    )
    malformed = commands.market_vector_ingest_result_payload(
        completed=Completed(returncode=0, stdout="log\n{bad json}\n", stderr=""),
        dry_run=False,
        command="python ingest.py",
    )
    non_object = commands.market_vector_ingest_result_payload(
        completed=Completed(returncode=0, stdout='["not", "object"]', stderr=""),
        dry_run=True,
        command="python ingest.py",
    )

    assert parsed["summary"] == {"inserted": 3}
    assert parsed["dry_run"] is True
    assert parsed["stderr"] == "warn"
    assert malformed["summary"] is None
    assert malformed["dry_run"] is False
    assert non_object["summary"] is None


def test_market_vector_ingest_result_payload_uses_last_complete_json_object_line():
    payload = commands.market_vector_ingest_result_payload(
        completed=Completed(
            returncode=0,
            stdout='{"inserted": 1}\nprogress {bad json}\n{"inserted": 3, "collection": "siq_market"}\n',
        ),
        dry_run=False,
        command="python ingest.py",
    )
    trailing_noise = commands.market_vector_ingest_result_payload(
        completed=Completed(returncode=0, stdout='{"inserted": 3} trailing text\n'),
        dry_run=True,
        command="python ingest.py",
    )

    assert payload["summary"] == {"inserted": 3, "collection": "siq_market"}
    assert trailing_noise["summary"] is None


def test_market_vector_ingest_result_payload_accepts_pretty_json_summary_before_trailing_log():
    payload = commands.market_vector_ingest_result_payload(
        completed=Completed(
            returncode=0,
            stdout=(
                'progress\n'
                '{\n'
                '  "collection": "siq_market",\n'
                '  "chunk_count": 3,\n'
                '  "first": {\n'
                '    "ticker": "AAPL"\n'
                '  }\n'
                '}\n'
                'chunks=3\n'
            ),
        ),
        dry_run=True,
        command="python ingest.py --dry-run",
    )
    trailing_same_line = commands.market_vector_ingest_result_payload(
        completed=Completed(
            returncode=0,
            stdout='progress\n{\n  "chunk_count": 3\n} chunks=3\n',
        ),
        dry_run=True,
        command="python ingest.py --dry-run",
    )

    assert payload["summary"] == {"collection": "siq_market", "chunk_count": 3, "first": {"ticker": "AAPL"}}
    assert trailing_same_line["summary"] is None


def test_eval_and_us_sec_ingest_result_payloads_keep_reports_and_truncate_logs():
    eval_payload = commands.market_ingestion_eval_result_payload(
        completed=Completed(returncode=0, stdout="eval ok\n", stderr=""),
        report={"score": 0.98},
        markdown_path="tmp/eval.md",
        command="python eval.py",
    )
    ingest_payload = commands.us_sec_case_set_ingest_result_payload(
        completed=Completed(returncode=1, stdout="x" * 9000, stderr="e" * 9000),
        report={"inserted": 0},
        command="python ingest.py",
    )

    assert eval_payload == {
        "ok": True,
        "returncode": 0,
        "stdout": "eval ok\n",
        "stderr": "",
        "report": {"score": 0.98},
        "markdown_path": "tmp/eval.md",
        "command": "python eval.py",
    }
    assert ingest_payload["ok"] is False
    assert ingest_payload["report"] == {"inserted": 0}
    assert ingest_payload["stdout"] == "x" * 8000
    assert ingest_payload["stderr"] == "e" * 8000


def test_us_sec_rebuild_package_result_payload_normalizes_ticker_and_truncates_logs():
    payload = commands.us_sec_rebuild_package_result_payload(
        completed=Completed(returncode=0, stdout="x" * 5000, stderr="e" * 5000),
        ticker="aapl",
        package={"package_path": "data/wiki/us_sec/AAPL/package"},
    )

    assert payload == {
        "ok": True,
        "ticker": "AAPL",
        "stdout": "x" * 4000,
        "stderr": "e" * 4000,
        "package": {"package_path": "data/wiki/us_sec/AAPL/package"},
    }
