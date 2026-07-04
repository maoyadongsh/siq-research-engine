# Task 1 Report: HK V2 Package artifact contract

## Status
DONE

## Scope
- `/home/maoyd/siq-research-engine/services/market-report-rules/tests/test_hk_evidence_package.py`
- `/home/maoyd/siq-research-engine/scripts/hk/hk_evidence_lib.py`

## Requirements handled
- 扩展 HK package 测试，使用带 `content_list_enhanced.footnotes`、`toc`、`financial_note_links`、`quality_signals`、`tables`、`pages` 的 fake `document_full.json`。
- 先验证新增断言会失败，失败点为缺少 `parser/document_full.json`。
- 在 HK builder 中补齐 V2 package 产物：
  - `parser/document_full.json`
  - `parser/content_list_enhanced.json`
  - `parser/table_relations.json`
  - `sections/report_complete.md`
  - `qa/footnotes.json`
  - `qa/toc.json`
  - `qa/financial_note_links.json`
  - `qa/table_quality_signals.json`
- 保持 `manifest.schema_version == market_evidence_package_v1`，并在计算 `artifact_hashes` 之前完成上述文件写入。
- `validate_evidence_package(package_dir).ok` 继续为 `true`。

## TDD evidence
1. 先修改测试并运行：
   - 命令：`cd /home/maoyd/siq-research-engine/services/market-report-rules && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hk_evidence_package.py`
   - 结果：失败，断言缺少 `parser/document_full.json`。
2. 再做最小实现并重跑同一命令。
3. 结果：`1 passed in 0.06s`。

## Implementation notes
- 新增 `parser` 目录创建。
- 新增 `_content_list_enhanced()`，统一读取增强结构。
- 新增 `_write_parser_artifacts()`，写入 parser 侧 V2 产物，并在缺失 parser financial/quality 文件时回退到可用契约数据。
- 新增 `_write_report_complete()`，在正文后追加“可恢复结构摘要”“目录候选”“脚注摘要”“附注关系摘要”“图片/表格摘要”。
- 新增 `_write_enhancement_qa()`，从 `content_list_enhanced` 抽取脚注、目录、附注关系、质量信号 QA 文件。

## Verification
- Focused test passed with the brief’s exact command.
- `manifest.artifact_hashes` now includes the HK V2 artifact paths asserted by the test.
- Existing `market_evidence_package_v1` validation remains green for the HK package fixture.

## Concerns
- `parser/financial_data.json` 与 `parser/financial_checks.json` 在 parser 原始文件缺失时回退为规则产出的契约数据；当前 brief 未给出更具体的原始 parser financial 文件命名规范，后续若 contract reader 对这两份 parser 侧文件有更严格要求，需要在后续任务再对齐。

## Review fix follow-up
- 修复 review finding 1：`parser/financial_data.json` 与 `parser/financial_checks.json` 仅在 parser 原始文件存在时原样保留；文件缺失时改写为空契约，不再把规则侧 `financial_data` / `financial_checks` 冒充为 parser 产物。
- 修复 review finding 2：测试新增两类回归断言：
  - parser financial 文件缺失时，parser 包内 financial artifacts 使用空契约；
  - parser financial 文件存在时，原始 parser 文件按原样保留。
- 修复 review finding 3：对 `content_list_enhanced` 中非预期字符串/非 dict/list 的 `footnotes`、`toc`、`financial_note_links`、`quality_signals`、`pages`、`tables[*].relations` 做归一化，QA/parser 产物统一落为空契约。

## Review fix TDD / verification
1. 先仅同步测试到远端并运行：
   - 命令：`cd /home/maoyd/siq-research-engine/services/market-report-rules && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hk_evidence_package.py`
   - 结果：`2 failed in 0.08s`
   - 失败点：
     - `parser/financial_data.json` 写入了规则侧抽取结果，而不是空 parser 契约；
     - malformed enhanced payload 被原样写入 `qa/footnotes.json`。
2. 再同步 `scripts/hk/hk_evidence_lib.py` 修复并重跑同一命令。
3. 结果：`2 passed in 0.07s`。
