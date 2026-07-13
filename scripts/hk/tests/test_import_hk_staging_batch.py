from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "import_hk_staging_batch.py"
    spec = importlib.util.spec_from_file_location("import_hk_staging_batch", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _package(root: Path, name: str) -> Path:
    package = root / "companies" / name / "reports" / "2025-annual"
    package.mkdir(parents=True)
    (package / "manifest.json").write_text("{}\n", encoding="utf-8")
    return package


def _stub_preflight(monkeypatch, module, decisions: dict[str, str]):
    monkeypatch.setattr(
        module.hk_importer,
        "validate_evidence_package",
        lambda package: SimpleNamespace(ok=True, errors=[], manifest={"market": "HK"}),
    )
    monkeypatch.setattr(
        module,
        "build_quality_gates",
        lambda package: {"canonical_decision": decisions[package.parents[1].name]},
    )


def _plan(package: Path, *, force_review: bool, **_kwargs):
    return {
        "package_path": package.as_posix(),
        "quality_gate_decision": "review" if force_review else "allow",
        "package_hash": package.parents[1].name,
    }


def _review_args(root: Path, output: Path | None = None) -> list[str]:
    args = [
        "--staging-root",
        str(root),
        "--staging-only",
        "--expected-database",
        "siq_hk_stage_20260713",
        "--force-review",
        "--force-requested-by",
        "operator",
        "--force-approved-by",
        "approver",
        "--force-reason",
        "audited isolated staging rebuild",
        "--force-expires-at",
        "2099-01-01T00:00:00Z",
    ]
    if output:
        args.extend(["--json-output", str(output)])
    return args


def test_requires_explicit_staging_only_and_rejects_production_database(tmp_path):
    module = _load_module()
    root = tmp_path / "staging"
    root.mkdir()

    with pytest.raises(SystemExit, match="--staging-only is required"):
        module.validate_staging_target(root, staging_only=False, expected_database="siq_hk_stage")
    with pytest.raises(SystemExit, match="Refusing production database"):
        module.validate_staging_target(root, staging_only=True, expected_database="siq_hk")


def test_rejects_any_root_overlapping_production_wiki(tmp_path, monkeypatch):
    module = _load_module()
    production = tmp_path / "data" / "wiki" / "hk"
    production.mkdir(parents=True)
    monkeypatch.setattr(module, "PRODUCTION_WIKI_ROOT", production.resolve())

    for unsafe in (production, production / "child", production.parent):
        unsafe.mkdir(exist_ok=True)
        with pytest.raises(SystemExit, match="overlaps"):
            module.validate_staging_target(
                unsafe,
                staging_only=True,
                expected_database="siq_hk_stage",
            )


def test_review_requires_every_override_audit_field(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "staging"
    _package(root, "00700")
    _stub_preflight(monkeypatch, module, {"00700": "review"})

    with pytest.raises(SystemExit, match="force_approved_by"):
        module.prepare_batch(
            root,
            expected_database="siq_hk_stage",
            force_review=True,
            force_requested_by="operator",
            force_reason="test",
            force_expires_at="2099-01-01T00:00:00Z",
        )


def test_mixed_batch_forces_only_review_packages(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "staging"
    _package(root, "00005")
    _package(root, "00700")
    _stub_preflight(monkeypatch, module, {"00005": "allow", "00700": "review"})
    calls = []

    def build_plan(package, **kwargs):
        calls.append((package.parents[1].name, kwargs))
        return _plan(package, **kwargs)

    monkeypatch.setattr(module.hk_importer, "build_import_plan", build_plan)
    plan, prepared = module.prepare_batch(
        root,
        expected_database="siq_hk_stage",
        force_review=True,
        force_requested_by="operator",
        force_approved_by="approver",
        force_reason="test",
        force_expires_at="2099-01-01T00:00:00Z",
    )

    by_ticker = dict(calls)
    assert by_ticker["00005"]["force_review"] is False
    assert by_ticker["00005"]["force_requested_by"] is None
    assert by_ticker["00700"]["force_review"] is True
    assert by_ticker["00700"]["force_approved_by"] == "approver"
    assert plan["quality_decisions"] == {"allow": 1, "review": 1}
    assert [entry["force_review"] for entry in prepared] == [False, True]


def test_all_packages_are_validated_before_preflight_failure(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "staging"
    first = _package(root, "00005")
    second = _package(root, "00700")
    calls = []

    def validate(package):
        calls.append(package)
        if package == first:
            return SimpleNamespace(ok=False, errors=["broken"], manifest={})
        return SimpleNamespace(ok=True, errors=[], manifest={"market": "HK"})

    monkeypatch.setattr(module.hk_importer, "validate_evidence_package", validate)
    monkeypatch.setattr(module, "build_quality_gates", lambda _package: {"canonical_decision": "allow"})

    with pytest.raises(SystemExit, match="batch validation failed"):
        module.prepare_batch(root, expected_database="siq_hk_stage", force_review=False)
    assert calls == [first, second]


def test_dry_run_writes_plan_without_connecting(tmp_path, monkeypatch, capsys):
    module = _load_module()
    root = tmp_path / "staging"
    _package(root, "00700")
    output = tmp_path / "plan.json"
    _stub_preflight(monkeypatch, module, {"00700": "review"})
    monkeypatch.setattr(module.hk_importer, "build_import_plan", _plan)
    monkeypatch.setattr(
        module.hk_importer.psycopg,
        "connect",
        lambda **_kwargs: pytest.fail("dry-run must not connect"),
    )

    assert module.main([*_review_args(root, output), "--dry-run"]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    stdout = json.loads(capsys.readouterr().out)
    assert payload == stdout
    assert payload["read_only"] is True
    assert payload["database_connected"] is False
    assert payload["execution_authorized"] is False
    assert payload["package_count"] == 1


def test_configured_database_mismatch_is_rejected_before_connect(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "staging"
    _package(root, "00700")
    _stub_preflight(monkeypatch, module, {"00700": "review"})
    monkeypatch.setattr(module.hk_importer, "build_import_plan", _plan)
    monkeypatch.setattr(
        module.hk_importer,
        "connection_kwargs",
        lambda: {"dbname": "siq_hk"},
    )
    monkeypatch.setattr(
        module.hk_importer.psycopg,
        "connect",
        lambda **_kwargs: pytest.fail("mismatch must not connect"),
    )

    with pytest.raises(SystemExit, match="refusing to connect"):
        module.main(_review_args(root))


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.transaction_entries += 1
        return self

    def __exit__(self, exc_type, _exc, _tb):
        if exc_type:
            self.connection.rolled_back = True
        else:
            self.connection.committed = True
        return False


class _Connection:
    def __init__(self):
        self.transaction_entries = 0
        self.rolled_back = False
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def transaction(self):
        return _Transaction(self)


def test_batch_uses_one_outer_transaction_and_rolls_back_on_any_failure(tmp_path, monkeypatch):
    module = _load_module()
    root = tmp_path / "staging"
    _package(root, "00005")
    _package(root, "00700")
    _stub_preflight(monkeypatch, module, {"00005": "allow", "00700": "review"})
    monkeypatch.setattr(module.hk_importer, "build_import_plan", _plan)
    monkeypatch.setattr(
        module.hk_importer,
        "connection_kwargs",
        lambda: {"dbname": "siq_hk_stage_20260713"},
    )
    connection = _Connection()
    connect_calls = []

    def connect(**kwargs):
        connect_calls.append(kwargs)
        return connection

    monkeypatch.setattr(module.hk_importer.psycopg, "connect", connect)
    monkeypatch.setattr(module.hk_importer, "validate_connection_database", lambda *_args: None)
    imports = []

    def import_package(_conn, package, _schema, **kwargs):
        imports.append((package.parents[1].name, kwargs["force_review"]))
        if package.parents[1].name == "00700":
            raise RuntimeError("synthetic failure")
        return "run-00005"

    monkeypatch.setattr(module.hk_importer, "import_package", import_package)

    with pytest.raises(SystemExit, match="RuntimeError"):
        module.main(_review_args(root))

    assert len(connect_calls) == 1
    assert connect_calls[0]["autocommit"] is True
    assert connection.transaction_entries == 1
    assert connection.rolled_back is True
    assert connection.committed is False
    assert imports == [("00005", False), ("00700", True)]


def test_parser_rejects_database_url_argument():
    module = _load_module()

    with pytest.raises(SystemExit):
        module.build_parser().parse_args(
            [
                "--staging-root",
                "/tmp/staging",
                "--staging-only",
                "--expected-database",
                "siq_hk_stage",
                "--database-url",
                "postgresql://user:secret@example.invalid/siq_hk",
            ]
        )
