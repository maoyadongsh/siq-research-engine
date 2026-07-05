## Task 1: 锁定 HK V2 Package 产物契约

**Files:**
- `services/market-report-rules/tests/test_hk_evidence_package.py`
- `scripts/hk/hk_evidence_lib.py`

**Behavior:**
HK 构建器在现有最小包基础上输出完整 V2 结构：`parser/document_full.json`、`parser/content_list_enhanced.json`、`parser/table_relations.json`、`sections/report_complete.md`、`qa/footnotes.json`、`qa/toc.json`、`qa/financial_note_links.json`、`qa/table_quality_signals.json`，并继续保留现有 `manifest.json`、`tables/*`、`metrics/*`、`qa/source_map.json`。

**TDD Steps:**

- [ ] 扩展 `test_build_hk_evidence_package_from_parser_result` 的 fake `document_full.json`，加入 `content_list_enhanced.footnotes`、`toc`、`financial_note_links`、`quality_signals`、`tables`、`pages`。
- [ ] 增加断言：上述 V2 文件均存在。
- [ ] 增加断言：`manifest.artifact_hashes` 包含上述 V2 文件，`validate_evidence_package(package_dir).ok` 仍为 true。
- [ ] 运行测试，确认新增断言先失败。

**Implementation Steps:**

- [ ] 在 `write_hk_evidence_package()` 中创建 `parser` 目录。
- [ ] 新增 `_write_parser_artifacts(package_dir, parser_result_dir, document_full, financial_data, financial_checks)`：写入 `document_full`、`content_list_enhanced`、`table_relations`、原始 parser quality/financial 文件；缺失时写入空契约。
- [ ] 新增 `_write_report_complete(package_dir, markdown, document_full, quality)`，正文后追加“可恢复结构摘要”“目录候选”“脚注摘要”“附注关系摘要”“图片/表格摘要”。
- [ ] 新增 `_write_enhancement_qa(package_dir, document_full)`，从 `content_list_enhanced` 抽取 footnotes、toc、financial_note_links、quality_signals。
- [ ] 在计算 `artifact_hashes` 前写完所有 V2 文件。

**Code Sketch:**

```python
def _content_list_enhanced(document_full: dict[str, Any]) -> dict[str, Any]:
    enhanced = document_full.get("content_list_enhanced")
    return enhanced if isinstance(enhanced, dict) else {}


def _write_enhancement_qa(package_dir: Path, document_full: dict[str, Any]) -> None:
    enhanced = _content_list_enhanced(document_full)
    write_json(package_dir / "qa" / "footnotes.json", {
        "schema_version": "hk_footnotes_v1",
        "payload": enhanced.get("footnotes") or {"references": [], "definitions": [], "bindings": [], "summary": {}},
    })
    write_json(package_dir / "qa" / "toc.json", {
        "schema_version": "hk_toc_v1",
        "payload": enhanced.get("toc") or {"headings": [], "toc_candidates": [], "content_headings": [], "summary": {}},
    })
```

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hk_evidence_package.py
```

Expected: HK package test passes and generated package validates under `market_evidence_package_v1`.

---

