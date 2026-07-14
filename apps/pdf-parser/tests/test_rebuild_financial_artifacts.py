import importlib.util
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rebuild_financial_artifacts.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rebuild_financial_artifacts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_task_rows_preserve_parser_lineage_fields(tmp_path):
    module = _load_module()
    db_path = tmp_path / "tasks.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table tasks (
                task_id text primary key,
                filename text,
                file_sha256 text,
                parse_config_hash text,
                pdf_page_count integer,
                upload_path text,
                status text,
                stage text,
                created_at text,
                completed_at text,
                submit_config_json text
            )
            """
        )
        conn.execute(
            "insert into tasks values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "eu-vod",
                "Vodafone_EU_VOD_2025-03-31_annual.pdf",
                "raw-sha",
                "config-sha",
                248,
                "/uploads/vodafone.pdf",
                "completed",
                "completed",
                "2026-07-14T04:41:58Z",
                "2026-07-14T05:12:28Z",
                '{"market":"EU","table_enable":true}',
            ),
        )

    task = module._task_rows(db_path)["eu-vod"]

    assert task["file_sha256"] == "raw-sha"
    assert task["parse_config_hash"] == "config-sha"
    assert task["pdf_page_count"] == 248
    assert task["upload_path"] == "/uploads/vodafone.pdf"
    assert task["submit_config_json"] == '{"market":"EU","table_enable":true}'
