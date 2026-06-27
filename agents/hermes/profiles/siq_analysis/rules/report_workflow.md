# Report Workflow

完整年度报告生成必须执行确定性流水线，不能自由拼路径、自由拼命令。

## 主入口

```bash
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或company_id> --year <年度>
```

续跑：

```bash
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或company_id> --year <年度> --reuse-checkpoint
```

推荐高质量三步：

```bash
# 1. 准备材料与检查点
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或company_id> --year <年度> --prepare-only

# 2. 基于 .work 中的 preflight/metric_snapshot/evidence_package/analysis_outline 写入高质量 section_drafts.json
# section_drafts.json 必须包含固定 14 个 section_id。

# 3. 复用检查点，执行最终渲染、溯源修复和质量验收
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或company_id> --year <年度> --reuse-checkpoint
```

## 防覆盖规则

默认情况下，最终渲染不会覆盖已有 `analysis/<stock>-<short>-<year>-analysis.md/.json/.html`。

若目标文件已存在：
- 优先使用 `--output-prefix` 写入测试前缀。
- 只有用户明确允许覆盖，或任务明确要求覆盖时，才追加 `--allow-overwrite`。
- 覆盖前脚本会自动备份旧文件到 `.work/backups/`；最终回复必须披露备份路径。

## 阶段产物

阶段产物默认写入：

```text
wiki/companies/<company_id>/analysis/.work/<report_slug>/
```

至少包括：
- `preflight.json`
- `evidence_package.json`
- `metric_snapshot.json`
- `analysis_outline.json`
- `section_drafts.json`
- `quality_report.json`
- `citation_repair.json`
- `final_validation.json`

## 阶段顺序

1. 公司与报告定位：`resolve_company.py`
2. 单公司 wiki 全量盘点：读取目标公司目录下所有可用内容清单，包括 `company.*`、`reports/`、`metrics/`、`evidence/`、`semantic/`、`graph/`、`tracking/`、`factcheck/`、既有 `analysis/` 和 `_index.json`。
3. 数据状态预检：`preflight.json`
4. 证据包构建：`evidence_package.json`
5. 指标快照构建：`metric_snapshot.json`
6. 分析主线草稿：`analysis_outline.json`
7. 14 章结构化生成：`section_drafts.json`
8. 证据绑定与引用修补：`repair_report_citations.py`
9. 模板合规与质量验收：`validate_report_quality.py`
10. 最终完成摘要：只报告路径、验收结果和复核项。

## 图表能力要求

完整年度报告进入最终渲染前，必须具备并使用本地金融图表规范：

```text
/home/maoyd/.agents/skills/finsight-chart-craft/SKILL.md
```

执行要求：
- 收支拆解、利润桥和瀑布图必须先通过三表勾稽，再进入视觉渲染。
- 图表数据优先从 `metrics/reports/<report_id>/three_statements.json` 的原始合并利润表项目名取值，避免重复 normalized key 造成口径混淆。
- `营业成本` 和 `营业总成本` 是不同项目；不得把 `营业总成本` 当作 `营业成本` 后又重复扣费用。
- `资产减值损失`、`信用减值损失`、`公允价值变动收益`、`资产处置收益` 要作为显式桥接项或显式汇总项，不得无说明并入残差。
- 首屏收支拆解图必须输出可交互 HTML/SVG 或 ECharts，支持 tooltip、键盘 focus 和点击高亮；不得只输出截图。

## 单公司 Wiki 全量读取要求

完整年度分析报告开始写作前，必须先完成目标公司 wiki 目录的全量获取和可用性判断。全量获取不是把所有大文件原样塞入正文，而是必须形成可审计的目录清单、关键文件读取状态、核心数据抽取状态和缺口清单。

最低读取范围：
- `company.json`、`company.md`、`_index.json`
- `reports/<year>-annual/report.json`、`report.md`、`document_full.json`、`artifact_manifest.json`
- `metrics/key_metrics.json`、`metrics/three_statements.json`、`metrics/validation.json`
- `evidence/evidence_index.json`、`evidence/pdf_refs.json`、`evidence/image_manifest.json`
- `semantic/facts.json`、`claims.json`、`relations.json`、`segments.json`、`evidence_semantic.json`、`semantic/llm/<year>-annual/*.json`
- `graph/report.md`、`graph/company.md`、`graph/facts/*`、`graph/claims/*`、`graph/segments/*`
- `tracking/report_manifest.json`、`tracking/tracking-items.md`、`tracking/updates/*`、`tracking/alerts/*`、`tracking/metrics/*`、`tracking/sentiment/*`
- `factcheck/*`
- 既有 `analysis/*`，尤其是高质量历史样例或人工修订版，用于学习结构和校验风格，但不得无证据复制旧结论。

全量读取后，报告生成必须说明：
- 哪些目录和文件已读取。
- 哪些文件缺失、陈旧、过大仅索引读取或解析失败。
- 当前报告采用哪些证据源作为事实底座。
- 哪些判断依赖模型推论、需要后续人工复核。

## 恢复与循环限制

- 若 `.work` 中已有阶段文件且 JSON 可解析，必须从最近有效检查点续跑。
- 若 `section_drafts.json` 已存在但不足 14 章，只生成缺失章节。
- 同一 `work_dir + output_prefix` 的恢复命令最多执行 2 次。
- 若 `run_analysis_report.py` 返回 `ok=false`，不得回复“报告已完成”；必须读取 `stage`、`validation.failures`、`validation.warnings` 和 `next_action`。
- 若恢复命令返回 `stage=output_exists`，不得直接覆盖；改用测试 `--output-prefix` 或取得覆盖授权后添加 `--allow-overwrite`。

## 应急恢复器定位

`render_report_from_checkpoint.py` 和 `recover_report_from_workdir.py` 是应急结构恢复器，不是高质量报告生成器。它们可以补齐 14 章、HTML 和质量报告，但若 `quality_report.all_key_numbers_have_evidence=false` 或验收失败，不能把产物声明为高质量最终报告。
