from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from scripts.openshell.publish_company_index import (
    SOURCE_MODULE,
    CompanyIndexPublishError,
    _atomic_write_index,
    _load_index_module,
    publish_company_index,
    resolve_company_directory,
)

ROOT = Path(__file__).resolve().parents[3]
PROFILE_PUBLISHER_CALLERS = (
    ROOT / "agents/hermes/profiles/siq_analysis/scripts/run_analysis_report.py",
    ROOT / "agents/hermes/profiles/siq_factchecker/scripts/factcheck_cli.py",
)


def _project(tmp_path: Path, *, market: str = "cn", company_id: str = "600001-Test") -> tuple[Path, Path]:
    root = tmp_path / "repo"
    relative = Path("data/wiki/companies") if market == "cn" else Path(f"data/wiki/{market}/companies")
    company = root / relative / company_id
    (company / "analysis").mkdir(parents=True)
    (company / "company.json").write_text('{"company_id":"CN:600001"}\n', encoding="utf-8")
    return root, company


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "company_id": "CN:600001",
        "stock_code": "600001",
        "company_short_name": "Test",
        "industry": "Research",
        "generated_at": "2026-07-15T00:00:00",
        "data": {},
        "analysis": {},
        "factcheck": {},
        "tracking": {},
        "legal": {},
    }


def test_publisher_derives_path_and_writes_atomically(tmp_path: Path) -> None:
    root, company = _project(tmp_path)

    result = publish_company_index(
        project_root=root,
        market="cn",
        company_id="600001-Test",
        builder=lambda _: _payload(),
    )

    assert result["ok"] is True
    assert "600001-Test" not in json.dumps(result)
    assert json.loads((company / "_index.json").read_text(encoding="utf-8")) == _payload()
    assert stat.S_IMODE((company / "_index.json").stat().st_mode) == 0o644
    assert not list(company.glob("._index.*.tmp"))
    lock_files = list((root / "var/openshell/publisher/company-index-locks").glob("*.lock"))
    assert len(lock_files) == 1
    assert stat.S_IMODE(lock_files[0].stat().st_mode) == 0o600


def test_publisher_uses_anchored_company_directory_without_changing_index_paths(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    report = company / "analysis/600001-analysis.md"
    report.write_text("stable report\n", encoding="utf-8")
    builder_paths: list[Path] = []

    def builder(company_anchor: Path) -> dict[str, object]:
        builder_paths.append(company_anchor)
        payload = _payload()
        payload["analysis"] = {"latest": {"path": str(company_anchor / "analysis/600001-analysis.md")}}
        return payload

    publish_company_index(
        project_root=root,
        market="cn",
        company_id="600001-Test",
        builder=builder,
    )

    assert len(builder_paths) == 1
    assert str(builder_paths[0]).startswith("/proc/self/fd/")
    stored = json.loads((company / "_index.json").read_text(encoding="utf-8"))
    assert stored["analysis"]["latest"]["path"] == str(report)


def test_reviewed_builder_preserves_canonical_business_output_paths(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    source = root / SOURCE_MODULE
    source.parent.mkdir(parents=True)
    for directory in (
        root,
        root / "agents",
        root / "agents/hermes",
        root / "agents/hermes/profiles",
        root / "agents/hermes/profiles/shared",
        root / "agents/hermes/profiles/shared/scripts",
    ):
        directory.chmod(0o755)
    source.write_bytes((ROOT / SOURCE_MODULE).read_bytes())
    source.chmod(0o644)
    (company / "company.json").write_text(
        json.dumps(
            {
                "company_id": "CN:600001",
                "stock_code": "600001",
                "company_short_name": "Test",
                "industry": "Research",
            }
        ),
        encoding="utf-8",
    )
    report = company / "analysis/600001-analysis.md"
    report.write_text("stable report\n", encoding="utf-8")

    publish_company_index(
        project_root=root,
        market="cn",
        company_id="600001-Test",
    )

    stored = json.loads((company / "_index.json").read_text(encoding="utf-8"))
    assert stored["company_id"] == "CN:600001"
    assert stored["stock_code"] == "600001"
    assert stored["analysis"]["md"]["path"] == str(report)
    assert "/proc/self/fd/" not in json.dumps(stored)


def test_publisher_rejects_input_changed_while_builder_runs(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    report = company / "analysis/600001-analysis.md"
    report.write_text("before\n", encoding="utf-8")

    def mutating_builder(company_anchor: Path) -> dict[str, object]:
        (company_anchor / "analysis/600001-analysis.md").write_text("after\n", encoding="utf-8")
        return _payload()

    with pytest.raises(CompanyIndexPublishError, match="publisher_input_changed"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=mutating_builder,
        )

    assert not (company / "_index.json").exists()


def test_publisher_rejects_company_directory_replaced_while_builder_runs(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    retired = company.with_name(f"{company.name}.retired")

    def replacing_builder(_: Path) -> dict[str, object]:
        company.rename(retired)
        company.mkdir()
        return _payload()

    with pytest.raises(CompanyIndexPublishError, match="company_directory_changed"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=replacing_builder,
        )

    assert not (company / "_index.json").exists()
    assert not (retired / "_index.json").exists()


def test_publisher_rejects_symlinked_output_without_touching_target(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    outside = tmp_path / "outside-index.json"
    outside.write_bytes(b'{"outside":true}\n')
    output = company / "_index.json"
    output.symlink_to(outside)

    with pytest.raises(CompanyIndexPublishError, match="publisher_output_unsafe"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=lambda _: _payload(),
        )

    assert output.is_symlink()
    assert outside.read_bytes() == b'{"outside":true}\n'


def test_publisher_rejects_hardlinked_output_without_replacing_it(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    outside = tmp_path / "outside-index.json"
    outside.write_bytes(b'{"outside":true}\n')
    output = company / "_index.json"
    output.hardlink_to(outside)

    with pytest.raises(CompanyIndexPublishError, match="publisher_output_unsafe"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=lambda _: _payload(),
        )

    assert output.read_bytes() == b'{"outside":true}\n'
    assert outside.stat().st_nlink == 2


def test_publisher_rejects_hardlinked_lock_before_changing_output(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    publish_company_index(
        project_root=root,
        market="cn",
        company_id="600001-Test",
        builder=lambda _: _payload(),
    )
    original = (company / "_index.json").read_bytes()
    lock = next((root / "var/openshell/publisher/company-index-locks").glob("*.lock"))
    (tmp_path / "outside.lock").hardlink_to(lock)

    with pytest.raises(CompanyIndexPublishError, match="publisher_lock_file_unsafe"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=lambda _: _payload(),
        )

    assert (company / "_index.json").read_bytes() == original


def test_publisher_rejects_lock_replaced_while_builder_runs(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    publish_company_index(
        project_root=root,
        market="cn",
        company_id="600001-Test",
        builder=lambda _: _payload(),
    )
    original = (company / "_index.json").read_bytes()
    lock = next((root / "var/openshell/publisher/company-index-locks").glob("*.lock"))

    def replacing_builder(_: Path) -> dict[str, object]:
        lock.unlink()
        lock.write_bytes(b"replacement\n")
        lock.chmod(0o600)
        payload = _payload()
        payload["generated_at"] = "2026-07-16T00:00:00"
        return payload

    with pytest.raises(CompanyIndexPublishError, match="publisher_lock_file_unsafe"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=replacing_builder,
        )

    assert (company / "_index.json").read_bytes() == original


def test_atomic_writer_remains_bound_to_open_company_directory(tmp_path: Path) -> None:
    _, company = _project(tmp_path)
    retired = company.with_name(f"{company.name}.retired")
    outside = tmp_path / "outside-company"
    outside.mkdir()
    descriptor = os.open(company, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        company.rename(retired)
        company.symlink_to(outside, target_is_directory=True)
        _atomic_write_index(descriptor, _payload())
    finally:
        os.close(descriptor)

    assert (retired / "_index.json").is_file()
    assert not (outside / "_index.json").exists()


@pytest.mark.parametrize(
    ("market", "company_id"),
    [("xx", "600001-Test"), ("cn", "../escape"), ("cn", "."), ("cn", " name")],
)
def test_publisher_rejects_unknown_market_and_arbitrary_paths(tmp_path: Path, market: str, company_id: str) -> None:
    root, _ = _project(tmp_path)

    with pytest.raises(CompanyIndexPublishError):
        resolve_company_directory(project_root=root, market=market, company_id=company_id)


def test_publisher_rejects_symlinked_company_directory(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    alias = root / "data/wiki/companies/600002-Alias"
    alias.symlink_to(company, target_is_directory=True)

    with pytest.raises(CompanyIndexPublishError, match="company_path_uses_symlink"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600002-Alias",
            builder=lambda _: _payload(),
        )


def test_publisher_rejects_symlink_anywhere_in_scanned_inputs(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret":"must not be read"}', encoding="utf-8")
    (company / "analysis/leak.json").symlink_to(outside)

    with pytest.raises(CompanyIndexPublishError, match="publisher_input_uses_symlink"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=lambda _: _payload(),
        )
    assert not (company / "_index.json").exists()


def test_publisher_rejects_hardlinked_inputs(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    source = company / "company.json"
    hardlink = company / "analysis" / "company-copy.json"
    hardlink.hardlink_to(source)

    with pytest.raises(CompanyIndexPublishError, match="publisher_input_hardlink"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=lambda _: _payload(),
        )


def test_publisher_rejects_invalid_builder_schema_without_replacing_index(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    original = b'{"old":true}\n'
    (company / "_index.json").write_bytes(original)

    with pytest.raises(CompanyIndexPublishError, match="index_schema_invalid"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=lambda _: {"schema_version": 1},
        )
    assert (company / "_index.json").read_bytes() == original


def test_publisher_binds_builder_identity_to_company_metadata(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    original = b'{"old":true}\n'
    (company / "_index.json").write_bytes(original)
    mismatched = _payload()
    mismatched["company_id"] = "CN:999999"

    with pytest.raises(CompanyIndexPublishError, match="index_identity_mismatch"):
        publish_company_index(
            project_root=root,
            market="cn",
            company_id="600001-Test",
            builder=lambda _: mismatched,
        )

    assert (company / "_index.json").read_bytes() == original


def test_publisher_supports_market_specific_company_roots(tmp_path: Path) -> None:
    root, company = _project(tmp_path, market="us", company_id="AAPL-Apple")

    publish_company_index(
        project_root=root,
        market="us",
        company_id="AAPL-Apple",
        builder=lambda _: _payload(),
    )

    assert (company / "_index.json").is_file()


def test_publisher_rejects_unreviewed_builder_bytes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = root / SOURCE_MODULE
    source.parent.mkdir(parents=True)
    for directory in (
        root,
        root / "agents",
        root / "agents/hermes",
        root / "agents/hermes/profiles",
        root / "agents/hermes/profiles/shared",
        root / "agents/hermes/profiles/shared/scripts",
    ):
        directory.chmod(0o755)
    source.write_bytes((ROOT / SOURCE_MODULE).read_bytes())
    source.chmod(0o644)
    # The fixture is accepted before mutation, then fails closed when the
    # reviewed builder bytes no longer match the pinned digest.
    _load_index_module(root)
    source.write_text(source.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(CompanyIndexPublishError, match="publisher_source_digest_mismatch"):
        _load_index_module(root)


def test_publisher_rejects_writable_builder_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = root / SOURCE_MODULE
    source.parent.mkdir(parents=True)
    for directory in (
        root,
        root / "agents",
        root / "agents/hermes",
        root / "agents/hermes/profiles",
        root / "agents/hermes/profiles/shared",
        root / "agents/hermes/profiles/shared/scripts",
    ):
        directory.chmod(0o755)
    source.write_bytes((ROOT / SOURCE_MODULE).read_bytes())
    source.chmod(0o664)

    with pytest.raises(CompanyIndexPublishError, match="publisher_source_unsafe"):
        _load_index_module(root)


@pytest.mark.parametrize("caller", PROFILE_PUBLISHER_CALLERS)
def test_profile_callers_defer_sandbox_writes_to_fixed_host_publisher(caller: Path) -> None:
    source = caller.read_text(encoding="utf-8")

    assert "update_company_index.py" not in source
    assert "publish_company_index_after_host_run" in source
    assert "SIQ_OPENSHELL_SANDBOX" in source
    assert "scripts/openshell/publish_company_index.py" in source
    assert "PUBLISHER_TIMEOUT_SECONDS = 30" in source
    assert "start_new_session=True" in source
    assert '"PYTHONNOUSERSITE": "1"' in source
    assert "company_index_publish_invalid" in source
