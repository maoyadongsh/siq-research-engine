# SIQ 市场合同包

## 模块定位

`packages/market-contracts` 是 SIQ 多市场 evidence package 的共享 contract 包。它不负责下载、解析或数据库写入，而是负责定义和复用 market package 的稳定文件系统合同、读取器、校验器和摘要逻辑。

这个包的价值在于：让 API、rules、importer、批处理工具和测试代码围绕同一份 package 语义协作，而不是各自靠约定俗成解析目录结构。

## 产品归属与业务边界

Market contracts 是二级市场投研分析智能体集群的共享合同层，也让应用中心的入库和向量化动作有稳定输入。

| 产品面 | 作用 | 边界 |
| --- | --- | --- |
| 二级市场 | 定义多市场 evidence package 的目录、hash、summary/detail、source map 和 quality gate 语义 | 不承担市场抓取、解析、规则抽取或数据库副作用 |
| 一级市场 | 可作为可比公司公开披露证据包的只读合同 | 不表达私有 deal evidence 或 IC 阶段状态 |
| 应用中心 | 让 Web、API、db/imports、Milvus ingest 和评测共享同一 package 读法 | contract 漂移必须显式测试和版本治理 |

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
| 财务值极性 | 声明市场 canonical 是保留符号还是采用费用正幅值 |

## 当前最新状态

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| `market_evidence_package_v1` | 作为多市场 package 的主合同 | API、Web、rules、importer、eval 都围绕该结构消费 |
| 质量门禁 | summary/detail 暴露 quality gates、warning/fail、hash 和 coverage 信号 | 上层可据此阻断 PostgreSQL import 与 vector dry-run |
| HK MVP | 港股 package 面板和 API gate 直接依赖本包的 summary/detail 语义 | contract 漂移会直接影响商业样板 |
| 稳定标识 | `stable_id` / `stable_parse_run_id` 保证重跑可对齐 | 支撑评测、缓存、入库幂等和审计回放 |
| 轻依赖共享层 | 不绑定具体服务进程或数据库连接 | 允许 apps/api、rules、db/imports、scripts 在不同环境中复用 |

这个包的商业价值不在代码量，而在它把“文件夹约定”升级为正式合同。只有合同稳定，质量门禁、导入、检索和智能体引用才能被客户信任。

## 高精度证据语义

本包把“值”和“证据”设计为不可随意拆开的组合。一个可被智能体使用的事实至少要能回答：它属于哪个市场/公司/报告/parse run，原值与规范值是什么，单位与币种是什么，来自 PDF、HTML 还是 XBRL，以及如何回到原位置。

| 合同能力 | 代表实现 | 精度价值 |
| --- | --- | --- |
| package 身份 | `evidence_package.py`、stable ID、manifest/hash | 防止文件移动、重跑或多市场目录导致对象串线 |
| evidence gate | `evidence_gates.py` | 把 source completeness 和 quality status 转成可执行消费门禁 |
| evidence resolver | `evidence_resolver.py` | 统一解析 PDF page/table/bbox、HTML anchor、XBRL concept/context/unit |
| value verification | `evidence_value_verification.py` | 验证回答/报告引用的数值确实存在于绑定证据，而非只有链接 |
| value polarity | `financial_value_polarity.py` | 区分自然借贷/流入流出、括号负数和展示符号，避免重复取反 |
| normalized fact | `normalized_fact.py` | 保留 raw value、canonical name、period、unit/currency 与 provenance |
| agent artifact | `agent_artifact.py` | 让报告 claim、引用、质量状态与 ResearchIdentity 可机器校验 |

这套共享语义允许 Milvus 索引被删除重建、PostgreSQL schema 被迁移、renderer 被替换，而不会丢失“哪一个事实支撑哪一句结论”的关系。

## 合同演进原则

- 新字段优先增量兼容；已有 `artifact_id`、evidence ID 和 stable ID 不因展示需求改变。
- normalized value 永远不能覆盖 raw value；修正需要产生可追踪的新版本或 correction artifact。
- 市场差异在 adapter/rules 层表达，共享包只保存跨市场都能解释的语义。
- reader 必须同时支持 summary 和 detail，避免前端为性能读取摘要后误以为证据已完整加载。
- 合同验证失败时应 fail closed；不能因某个调用方“只想先展示”而跳过 hash 或身份校验。

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
| `canonical_value_polarity` | 查询市场 canonical 的符号语义 |

### 财务值极性合同

`siq_financial_value_polarity_v1` 默认要求 canonical value 与原始证据严格同号。HK、EU 的
`cost_of_sales`、`finance_costs`、`income_tax_expense` 是唯一显式例外：PDF extractor 将报表中
以括号列示的扣减项规范为正费用额，因此 evidence verifier 允许“canonical 非负值、原文负值”
这一种单向的符号归一化。未声明市场、US 等其他市场，以及 revenue、profit 等其他 canonical
继续保留严格符号比较，不能用绝对值掩盖真实符号错误。

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

## 创新性与商业价值

`market-contracts` 把文件型知识包当作正式领域 API，而不是临时目录约定。稳定 ID、artifact hash、summary/detail reader、source map 和财务极性规则共同构成跨进程、跨版本的兼容层。

这带来三项直接价值：任何索引都可从证据包重建；package 可以离线交付、版本比较和独立验收；API、规则、导入和评测团队可以并行演进。技术难点是兼顾严格性与向后兼容，新增字段必须可渐进消费，关键语义变更则必须显式升级 schema version。
