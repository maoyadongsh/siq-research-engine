from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from services import deal_documents, deal_store, primary_market_wiki

DEAL_ID = "DEAL-WIKI-PRIMARY-001"


def test_primary_market_wiki_maps_materials_and_agent_artifacts_without_copying(tmp_path):
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Private Robotics",
        industry="Robotics",
        stage="Series B",
        wiki_root=tmp_path,
    )
    document = deal_documents.create_deal_document(
        deal_id=DEAL_ID,
        filename="business-plan.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"primary market business plan"),
        document_type="business_plan",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / DEAL_ID
    report_path = package_dir / "discussion" / "finance-review.md"
    report_path.write_text("# Finance review\n", encoding="utf-8")
    deal_store.write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "agents": [
                {
                    "agent_id": "siq_ic_finance_auditor",
                    "artifact_path": "discussion/finance-review.md",
                }
            ]
        },
    )

    tree = primary_market_wiki.rebuild_primary_market_wiki(
        DEAL_ID,
        wiki_root=tmp_path,
        append_audit=False,
    )

    assert tree["namespace"] == "primary_market"
    assert tree["namespace_guard"]["excluded_roots"] == ["data/wiki/companies"]
    assert tree["collection_bindings"]["shared_physical"] == "ic_collaboration_shared"
    assert tree["collection_bindings"]["shared_project_tag"] == DEAL_ID
    assert (
        tree["collection_bindings"]["private_by_profile"]["siq_ic_finance_auditor"]["physical"]
        == "ic_finance_auditor"
    )
    assert "01_materials/teaser_bp" in tree["directories"]
    assert "20_research/r1/finance" in tree["directories"]
    material = next(item for item in tree["entries"] if item["entry_type"] == "uploaded_material")
    assert material["document_id"] == document["document_id"]
    assert material["logical_directory"] == "company/materials/teaser_bp"
    assert material["canonical_path"].startswith("data_room/raw/")
    report = next(
        item
        for item in tree["entries"]
        if item.get("canonical_path") == "discussion/finance-review.md"
    )
    assert report["producer_profile"] == "siq_ic_finance_auditor"
    assert report["logical_directory"] == "company/research/r1/finance"
    assert not (package_dir / "wiki" / "01_materials" / "teaser_bp" / "business-plan.pdf").exists()


def test_material_category_aliases_are_stable():
    assert primary_market_wiki.normalize_material_category("bp") == "teaser_bp"
    assert primary_market_wiki.normalize_material_category("financial_model") == "finance"
    assert primary_market_wiki.normalize_material_category("legal_doc") == "legal"
    assert primary_market_wiki.normalize_material_category("meeting_note") == "interviews"
    assert primary_market_wiki.normalize_material_category("unknown-custom-type") == "other"


def test_company_wiki_projection_is_deal_scoped_and_concurrent_safe(tmp_path):
    deal_store.create_deal_package(deal_id=DEAL_ID, company_name="Private Robotics", wiki_root=tmp_path)
    documents = [
        deal_documents.create_deal_document(
            deal_id=DEAL_ID,
            filename=f"material-{index}.pdf",
            content_type="application/pdf",
            stream=BytesIO(f"raw-{index}".encode()),
            document_type="finance" if index == 1 else "legal",
            wiki_root=tmp_path,
        )
        for index in (1, 2)
    ]
    package_dir = tmp_path / "deals" / DEAL_ID
    sources = []
    for index, document in enumerate(documents, start=1):
        source = package_dir / "parsed_documents" / document["document_id"] / "runs" / f"run-{index}" / "document.md"
        source.parent.mkdir(parents=True)
        source.write_text(f"# Material {index}\n\nDeal-only evidence {index}.\n", encoding="utf-8")
        sources.append(source)

    def project(index: int):
        return primary_market_wiki.project_material_to_company_wiki(
            DEAL_ID,
            documents[index]["document_id"],
            source_path=sources[index],
            parse_run_id=f"run-{index + 1}",
            wiki_root=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        projections = list(pool.map(project, (0, 1)))

    index = deal_store.read_json(package_dir / "wiki" / "company" / "index.json", {})
    assert set(index["documents"]) == {item["document_id"] for item in documents}
    assert all(item["wiki_path"].startswith("wiki/company/materials/") for item in projections)
    assert not (tmp_path / "companies").exists()


def test_company_wiki_projection_uses_document_parser_blocks_with_evidence_anchors(tmp_path):
    deal_store.create_deal_package(deal_id=DEAL_ID, company_name="Private Robotics", wiki_root=tmp_path)
    document = deal_documents.create_deal_document(
        deal_id=DEAL_ID,
        filename="legal-pack.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"legal source"),
        document_type="legal",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / DEAL_ID
    run_dir = package_dir / "parsed_documents" / document["document_id"] / "runs" / "run-blocks"
    run_dir.mkdir(parents=True)
    (run_dir / "document.md").write_text("# Fallback text\n", encoding="utf-8")
    (run_dir / "blocks.json").write_text(
        json.dumps(
            {
                "task_id": "document-task-blocks",
                "blocks": [
                    {
                        "block_id": "legal-1",
                        "text": "Exclusive license expires in 2027.",
                        "page_number": 12,
                        "bbox": [10, 20, 300, 80],
                        "source_ref": {"evidence_id": "doc:document-task-blocks:p12:legal-1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    projection = primary_market_wiki.project_material_to_company_wiki(
        DEAL_ID,
        document["document_id"],
        source_path=run_dir / "document.md",
        structured_artifact_dir=run_dir,
        parse_task_id="document-task-blocks",
        parse_run_id="run-blocks",
        wiki_root=tmp_path,
    )

    wiki_markdown = (package_dir / projection["wiki_path"]).read_text(encoding="utf-8")
    assert (
        "DOC_BLOCK: legal-1 page=12 bbox=10,20,300,80 "
        "evidence=doc:document-task-blocks:p12:legal-1"
    ) in wiki_markdown
    assert "Exclusive license expires in 2027." in wiki_markdown
    assert "Fallback text" not in wiki_markdown
    assert projection["source_artifacts"][0]["path"].endswith("blocks.json")
