from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


def load_market_module(market: str):
    script = SCRIPT_ROOT / f"ingest_{market}_pdf_wiki.py"
    spec = importlib.util.spec_from_file_location(f"test_ingest_{market}_pdf_wiki", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("market", "inspect_name"),
    [
        ("jp", "inspect_jp_result"),
        ("kr", "inspect_kr_result"),
        ("eu", "inspect_eu_result"),
    ],
)
def test_pdf_market_build_plan_scopes_to_selected_task(monkeypatch, tmp_path, market, inspect_name):
    module = load_market_module(market)

    for task_id in ("task-a", "task-b"):
        (tmp_path / task_id).mkdir()
    monkeypatch.setattr(module, inspect_name, lambda result_dir: {"task_id": result_dir.name})
    monkeypatch.setattr(module, "select_active", lambda rows: (rows, {"selected": len(rows)}))

    rows, selection = module.build_plan(tmp_path, task_id="task-b")

    assert rows == [{"task_id": "task-b"}]
    assert selection == {"selected": 1}


@pytest.mark.parametrize("market", ["hk", "jp", "kr", "eu"])
def test_pdf_market_root_catalogs_preserve_unselected_companies(monkeypatch, tmp_path, market):
    module = load_market_module(market)
    written = {}

    def fake_read_json(path, default=None):
        if path.name == "company_catalog.json":
            return {"companies": [{"company_wiki_id": "OLD", "ticker": "OLD"}]}
        if path.name == "report_catalog.json":
            return {"reports": [{"company_wiki_id": "OLD", "report_id": "2024-annual"}]}
        if path == tmp_path / "derived" / "three_statements_latest.json":
            return {"OLD": {"status": "ready"}}
        return default if default is not None else {}

    monkeypatch.setattr(module, "read_json", fake_read_json)
    monkeypatch.setattr(module, "write_json", lambda path, payload: written.__setitem__(path.name, payload))
    monkeypatch.setattr(module, "write_text", lambda *_args: None)

    module.write_market_root(
        tmp_path,
        [{"company": {"company_wiki_id": "NEW", "ticker": "NEW", "company_name": "New"}, "reports": []}],
        {},
        tmp_path / "results",
    )

    assert {item["company_wiki_id"] for item in written["company_catalog.json"]["companies"]} == {"OLD", "NEW"}
    assert written["report_catalog.json"]["reports"] == [
        {"company_wiki_id": "OLD", "report_id": "2024-annual"}
    ]
    assert written["three_statements_latest.json"]["OLD"] == {"status": "ready"}
