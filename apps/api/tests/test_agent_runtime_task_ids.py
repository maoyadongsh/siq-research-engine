from pathlib import Path

from services import agent_runtime_task_ids

TASK_ID = "7dbc35a7-7626-4e81-810e-5dbb764434e0"
OTHER_TASK_ID = "00000000-0000-4000-8000-000000000000"


def test_extract_task_ids_from_reply_fields_and_api_links():
    text = (
        f"source_type=wiki_document_links, task_id={TASK_ID}, pdf_page=1\n"
        f"重复引用 task_id={TASK_ID}\n"
        f"链接 /api/source/{OTHER_TASK_ID}?page=1\n"
        "bad task_id=not-a-task"
    )

    assert agent_runtime_task_ids.extract_task_ids_from_text(text) == [OTHER_TASK_ID, TASK_ID]


def test_pdf2md_task_dirs_require_expected_artifacts(tmp_path: Path):
    result_root = tmp_path / "results"
    output_root = tmp_path / "outputs"
    result_dir = result_root / TASK_ID
    output_dir = output_root / TASK_ID
    result_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    assert agent_runtime_task_ids.pdf2md_task_result_dir(TASK_ID, roots=(result_root,)) is None

    (result_dir / "document_full.json").write_text("{}", encoding="utf-8")

    assert agent_runtime_task_ids.pdf2md_task_result_dir(TASK_ID, roots=(result_root,)) == result_dir
    assert agent_runtime_task_ids.pdf2md_task_output_dir(TASK_ID, roots=(output_root,)) == output_dir
    assert agent_runtime_task_ids.pdf2md_task_result_dir("bad", roots=(result_root,)) is None


def test_wiki_task_id_exists_uses_resolved_company_dirs(tmp_path: Path):
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "000001-Alpha"
    semantic_dir = company_dir / "semantic"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "document_links.json").write_text(f'{{"task_id":"{TASK_ID}"}}', encoding="utf-8")

    def resolve_company_dirs(message: str, context, *, limit: int):
        assert message == "查一下 Alpha"
        assert context == {"company": {"code": "000001"}}
        assert limit == 6
        return [company_dir]

    assert agent_runtime_task_ids.wiki_task_id_exists(
        TASK_ID,
        "查一下 Alpha",
        {"company": {"code": "000001"}},
        wiki_root=wiki_root,
        resolve_company_dirs=resolve_company_dirs,
    )


def test_invalid_task_ids_in_reply_ignores_existing_pdf2md_task(tmp_path: Path):
    result_root = tmp_path / "results"
    result_dir = result_root / TASK_ID
    result_dir.mkdir(parents=True)
    (result_dir / "result.md").write_text("ok", encoding="utf-8")
    wiki_root = tmp_path / "wiki"

    reply = (
        f"有效来源 task_id={TASK_ID}\n"
        f"伪造来源 /api/pdf_page/{OTHER_TASK_ID}?page=1"
    )

    invalid = agent_runtime_task_ids.invalid_task_ids_in_reply(
        "普通回答",
        None,
        reply,
        pdf2md_result_roots=(result_root,),
        pdf2md_output_roots=(),
        wiki_root=wiki_root,
        resolve_company_dirs=lambda *_args, **_kwargs: [],
    )

    assert invalid == [OTHER_TASK_ID]
