from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def load_module():
    script_root = Path(__file__).resolve().parents[1]
    if str(script_root) not in sys.path:
        sys.path.insert(0, str(script_root))
    path = script_root / "verify_agent_memory_research_identity_dual_backend.py"
    spec = importlib.util.spec_from_file_location("verify_agent_memory_dual_backend", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_fixture_covers_identity_and_acl_negative_rows():
    module = load_module()
    rows = {row["source_id"]: row for row in module._fixture()}

    assert rows["a-private-owner"]["identity"] == module.IDENTITY_A
    assert rows["b-private-owner"]["identity"] == module.IDENTITY_B
    assert rows["unscoped-owner"]["identity"] is None
    assert rows["a-private-other-user"]["owner_user_id"] == 8
    assert rows["a-project-other-group"]["agent_group"] == "primary_market"
    assert rows["a-system-other-tenant"]["tenant_id"] == "tenant-b"


def test_connection_url_conversion_preserves_driver_contract():
    module = load_module()

    plain = "postgresql://user:secret@db.example/siq"
    assert module._sync_url(plain).startswith("postgresql+psycopg://")
    assert module._async_url(plain).startswith("postgresql+asyncpg://")
    assert "+psycopg" in module._sync_url(module._async_url(plain))
    assert "+asyncpg" in module._async_url(module._sync_url(plain))


def test_run_fails_closed_redacts_url_and_cleans_both_backends(monkeypatch, tmp_path):
    module = load_module()
    calls = []

    class FakeClient:
        def has_collection(self, collection):
            calls.append(("has_collection", collection))
            return True

        def drop_collection(self, collection):
            calls.append(("drop_collection", collection))

    async def fail_searches(*_args, **_kwargs):
        raise RuntimeError("contract search failed")

    monkeypatch.setattr(module, "_create_postgres_fixture", lambda *_args: calls.append("create_pg"))
    monkeypatch.setattr(module, "_create_milvus_fixture", lambda *_args: FakeClient())
    monkeypatch.setattr(module, "_run_searches", fail_searches)
    monkeypatch.setattr(module, "_drop_postgres_schema", lambda *_args: calls.append("drop_pg"))

    output = tmp_path / "report.json"
    secret_url = "postgresql://user:super-secret@db.example/siq"
    report = module.run(postgres_url=secret_url, output=output)

    assert report["passed"] is False
    assert report["cleanup"]["postgres_schema_dropped"] is True
    assert report["cleanup"]["milvus_collection_dropped"] is True
    assert "drop_pg" in calls
    assert any(isinstance(call, tuple) and call[0] == "drop_collection" for call in calls)
    assert secret_url not in output.read_text(encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["error"]["type"] == "RuntimeError"
