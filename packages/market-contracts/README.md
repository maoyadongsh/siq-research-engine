# SIQ Market Contracts

## 模块定位

`packages/market-contracts` 是 SIQ 多市场 evidence package 的共享 contract 包。它不负责下载、解析或数据库写入，而是负责定义和复用 market package 的稳定文件系统合同、读取器、校验器和摘要逻辑。

这个包的价值在于：让 API、rules、importer、批处理工具和测试代码围绕同一份 package 语义协作，而不是各自靠约定俗成解析目录结构。

## 在系统中的位置

```text
market evidence package
  -> siq-market-contracts
     -> validate / summary / detail / stable ids / source map synthesis
     -> apps/api / services/market-report-rules / db/imports / batch tools / tests
```

它位于系统的“共享合同层”，作用是降低跨服务协作的歧义成本。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| package 校验 | 检查目录、必需文件、manifest 字段和 artifact hash |
| package 摘要 | 生成轻量 summary，供 API 和 UI 列表消费 |
| package 详情读取 | 读取 metrics、quality、source map、parser artifacts 等详细内容 |
| 稳定 ID 生成 | 生成 `stable_id`、`stable_parse_run_id` |
| hash 计算 | 为 package artifact 生成稳定 hash |
| source map 辅助 | 从 `financial_data` 构造基础 source map |

## 技术难点

这个包虽然代码量不大，但它是系统协作稳定性的关键点：

- contract 一旦漂移，API、rules、importer、批处理脚本和测试会一起失配。
- package summary 既要足够轻，供列表快速显示，又要足够稳定，供上层逻辑依赖。
- detail reader 需要对可选 artifact 友好，既允许市场差异，又不能让调用方失去一致消费体验。
- 依赖必须保持轻量，否则 shared contract 层会反过来拖累所有调用方环境。

## 输入输出或关键合同

### 核心文件系统合同

该包围绕以下标准文件组织 package 语义：

- `manifest.json`
- `metrics/financial_data.json`
- `metrics/financial_checks.json`
- `metrics/normalized_metrics.json`
- `qa/quality_report.json`
- `qa/source_map.json`
- `tables/table_index.json`

可选增强 artifact 还包括：

- `parser/document_full.json`
- `parser/content_list_enhanced.json`
- `parser/table_relations.json`
- `qa/footnotes.json`
- `qa/toc.json`
- `qa/financial_note_links.json`
- `qa/table_quality_signals.json`

### 对外导出的关键能力

| 导出项 | 用途 |
| --- | --- |
| `validate_evidence_package` | 校验 package 合法性 |
| `read_market_package_summary` | 生成轻量摘要 |
| `read_market_package_detail` | 生成详细视图 |
| `compute_artifact_hashes` | 计算 artifact hash |
| `stable_id` / `stable_parse_run_id` | 生成稳定标识 |
| `source_map_from_financial_data` | 辅助构建 source map |

## 启动方式

这是共享 Python 包，不以服务形式启动。常见使用方式是被其他模块通过 editable source 引用：

```bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
uv run python -m pytest tests
```

## 关键环境变量

该包本身尽量不依赖环境变量。调用方应通过参数传入 package 路径、display path 或外部上下文，而不是让 contract 包感知运行环境。

## 验证方式

```bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
uv run python -m pytest tests
```

修改 contract 结构时，应额外检查：

- `apps/api` 是否仍能读取 package summary / detail。
- `services/market-report-rules` 是否仍能对齐 manifest 和 source map 语义。
- `db/imports` 是否仍能消费相关 artifact。

## 维护原则

- contract 优先稳定，避免让 package 目录语义频繁漂移。
- 保持 dependency-light，避免 shared layer 反向放大环境复杂度。
- 任何字段或路径变更都应同步更新测试、README 和调用方适配。
- 共享包负责“定义和读取合同”，不应承担具体业务市场逻辑。
