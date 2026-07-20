# 多市场财报规则服务

## 模块定位

`services/market-report-rules` 是 SIQ 的多市场规则中枢。它处理的不是“原始披露文件”，而是已经过下载与解析后的结构化产物，并把这些产物进一步转换为 `financial_data`、`financial_checks`、`load_plan` 和可供 Agent / importer 消费的 evidence targets。

它在系统中的角色不是“后处理脚本集合”，而是多市场 evidence package 与结构化入库契约之间的规则层。

## 产品归属与业务边界

Rules 服务是二级市场投研分析智能体集群的质量和口径中枢，也为一级市场可比公司研究提供结构化财务事实。

| 产品面 | 作用 | 边界 |
| --- | --- | --- |
| 二级市场 | 多市场财务事实、勾稽校验、load plan 和 package gate | 不下载、不 OCR、不写库、不输出目标价或交易建议 |
| 一级市场 | 将可比公司和公开标的披露转成可查询财务事实 | 不替代项目尽调、专家判断或投委会决策 |
| 应用中心 | 为 PostgreSQL 入库、Milvus dry-run 和 Web package 面板提供规则化 artifact | 规则输出必须表达缺口和风险，不允许模型猜测补齐 |

## 在系统中的位置

```text
finder / parser 产物
  -> services/market-report-rules
     -> financial_data / financial_checks / load_plan / market profiles
     -> Wiki / PostgreSQL importer / Agent recall / frontend package views
```

它的职责边界非常明确：

- 不负责下载。
- 不负责原始 PDF / HTML 解析。
- 不直接给出投资建议或评分。
- 负责把“解析后的事实”组织成市场隔离、可验证、可导入的规则化结果。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 多市场 profile 管理 | 管理 CN / HK / US / EU / JP / KR 的规则与存储 profile |
| 结构化抽取 | 把 parser 或 package 产物转换为 `financial_data` |
| 财务校验 | 生成 `financial_checks` 并暴露风险、缺口和告警 |
| load plan 生成 | 为 PostgreSQL / 市场隔离 schema 提供导入计划 |
| 规则注册表 | 对前端和 API 暴露 markets、profiles、storage profiles 和 rule counts |
| 市场差异隔离 | 把市场逻辑沉到 `markets/<code>/`，避免共享层膨胀 |

## 当前最新状态

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| HK MVP | 支撑港股 evidence package 的财务抽取、校验和 load plan | 与 `/parse-hk` 的质量门禁、PostgreSQL import 和 vector dry-run 对齐 |
| 多市场 profile | CN / HK / US / EU / JP / KR 均保留市场隔离规则 | 不同 schema、report path、evidence target 和财务口径分开治理 |
| 质量信号 | `financial_checks`、statement coverage、bridge checks 与 parser warnings 共同进入 package gate | 上层 API 可据此阻断 warning/fail package |
| 入库计划 | `load_plan` 描述写入目标，不直接执行数据库写入 | 保持规则层可测试、可审计、可 dry-run |
| 架构治理 | 市场逻辑继续下沉到 `markets/<code>` | 避免把 JP/KR/EU/HK 差异堆进共享 extractor |

规则服务的商业价值在于把“解析出来的东西看起来像数据”提升为“可以负责任地进入研究数据库的数据”。它明确记录缺口、风险和入库计划，让质控负责人可以管理而不是事后追查污染源。

## 财务勾稽校验引擎

规则服务不只做字段映射。`validation.py` 对每个期间建立 source-aware 的财务桥，并把每条检查转换成可执行 gate contract：

| 检查族 | 典型公式或约束 | 处理策略 |
| --- | --- | --- |
| 报表完整性 | 资产负债表、利润表、现金流量表；行业必需指标 | 年报缺失通常升级为 warning/fail，摘要类报告按 form 调整 |
| 资产负债桥 | 资产=负债+可赎回/临时权益+权益；资产=负债及权益合计 | 支持替代总额桥，避免特殊资本结构被简单误判 |
| 分项桥 | 总资产=流动+非流动资产；总负债=流动+非流动负债 | 可选桥缺项可 skipped，错误保留 source evidence |
| 权益与利润桥 | 权益=归母+少数股东；毛利=收入-成本；净利润=税前利润-所得税 | 使用 `Decimal` 与 value polarity，避免符号二次翻转 |
| 现金流桥 | 净现金变动=经营+投资+融资+汇率影响；期末现金=期初+净变动+汇率影响 | 对未披露 FX 项按明确规则处理，不靠模型补值 |
| 证据/维度检查 | source locator、accounting standard、dimension scope | 维度事实与 consolidated total 分开，防止 segment fact 顶替主表总额 |

非 A 股来源可能同时存在 PDF 表格、XBRL fact 和 API fact。source-aware bridge 会优先选择来源一致的一组输入，不把不同来源族中“看起来最像”的高置信值任意拼成公式。检查结果带 rule ID、left/right、evidence refs、reason 和 promotion target，可被 API、importer、Milvus 和人工 review 共同消费。

### 风险分级与发布目标

| 严重度 | draft | review | canonical / retrieval / production |
| --- | --- | --- | --- |
| hard / fail | allow | review | block |
| soft / warning | allow | review | review |
| observe / skipped | allow | allow | allow |

因此 `overall_status=pass` 不是简单的“代码没报错”，而是必需报表、关键事实、财务桥、证据与会计口径共同通过后的可消费状态。

## 技术难点

规则层的难点不在“写 if/else”，而在于把不同市场的结构化语义隔离且统一表达：

- 披露逻辑不同：美股更接近 XBRL / iXBRL，港股更依赖 PDF 表格与标题语义，日股与韩股还经常叠加 XML / ZIP 包结构。
- 会计准则不同：US GAAP、IFRS、HKFRS、J-GAAP、K-IFRS 和本地口径的命名与映射差异大。
- 证据载体不同：有的指标能直接回到 facts / context_ref，有的必须回到 PDF 表格、页码、row / column 或 Markdown 行。
- 数据隔离要求高：不同市场必须保持明确 schema / namespace / package 路径隔离，避免错误混用。
- 输出边界严格：规则服务可以产出结构化事实和质量判断，但不能把缺失字段包装成已确认事实。

## 输入输出或关键合同

### 输入

- parser 产物或 market evidence package。
- 市场标识、解析后的结构化结果、质量产物和可选规则 profile。

### 输出

- `financial_data`
- `financial_checks`
- `load_plan`
- `markets` / `profiles` / `storage_profiles` 元数据

### 核心接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/healthz` | 健康状态、市场列表、profile 列表 |
| `GET` | `/profiles` | rule profile、storage profile、industry profile |
| `GET` | `/markets` | 当前注册市场模块 |
| `GET` | `/rules` | 规则数量和各市场统计 |
| `POST` | `/extract` | 产物转 `financial_data` |
| `POST` | `/validate` | `financial_data` 转 `financial_checks` |
| `POST` | `/process` | 一次性执行 extract + validate + load plan |
| `POST` | `/load-plan` | 单独生成入库计划 |

### 市场隔离约定

| 市场 | 默认 schema / 命名空间 | 典型 Wiki 根路径 |
| --- | --- | --- |
| A 股 | `pdf2md` | `data/wiki/companies/...` |
| 港股 | `pdf2md_hk` | `data/wiki/hk/companies/...` |
| 美股 | `sec_us` | `data/wiki/us/companies/...` |
| 欧股 | `eu_ifrs` | `data/wiki/eu/companies/...` |
| 日股 | `edinet_jp` | `data/wiki/jp/...` |
| 韩股 | `dart_kr` | `data/wiki/kr/...` |

## 启动方式

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv sync --extra dev
uv run python -m uvicorn market_report_rules_service.app:app --host 0.0.0.0 --port 18020
```

默认端口：`18020`。

## 关键环境变量

该服务主要依赖外层 `SIQ_*` 路径配置和调用方传入产物。常见环境配置关注点包括：

| 变量 | 用途 |
| --- | --- |
| `SIQ_PROJECT_ROOT` | 项目根路径 |
| `SIQ_WIKI_ROOT` | Wiki / evidence package 根目录 |
| `SIQ_DATA_ROOT` | 历史兼容运行态目录 |
| `SIQ_RUNTIME_ROOT` | 新增运行态推荐目录 |
| `DATABASE_URL` 或对应 importer 配置 | 下游导入侧数据库连接 |

规则服务本身尽量保持轻状态，把路径和数据库写入动作交给调用方或 importer。

## 验证方式

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv run pytest
curl -s http://127.0.0.1:18020/healthz
curl -s http://127.0.0.1:18020/markets
```

如果改动了某个市场 profile，至少补跑该市场对应的 tests，并确认 `/profiles` 与 `/rules` 的结果仍和模块注册一致。

## 维护原则

- 市场差异沉到市场模块，不把业务分支堆入共享层。
- 规则输出优先表达事实、证据与缺口，不输出评分、目标价或交易建议。
- `load_plan` 负责描述如何写入，不应在规则层里偷偷执行实际数据库写入。
- 市场隔离优先于代码复用；错误复用会比少量重复更危险。
- 所有高价值规则变更都应同步更新测试、README 和 contract 说明。

## 创新性与商业价值

规则服务采用“统一合同、市场隔离、证据不丢失”的方式处理全球披露差异。它允许各市场保留独立 extractor、definition、rules 和 storage profile，再通过共享接口向上输出。

| 创新点 | 技术难度 | 商业价值 |
| --- | --- | --- |
| 市场插件化 | 六市场模块独立注册且共享处理协议 | 新市场扩展不会破坏已验证市场 |
| 财务事实与校验并产 | 数值、口径、来源和勾稽检查共同输出 | 数据交付不仅有结果，还有可信度说明 |
| 声明式 load plan | 规则层描述目标写入，不执行副作用 | 支持 dry-run、审批、审计和多存储适配 |
| 缺失即缺失 | 不用模型猜测补齐关键财务事实 | 降低“合理幻觉”的高成本风险 |

该层是 SIQ 全球化能力的关键护城河：官方源和模型可以替换，但市场会计语义、证据规则和质量经验会持续积累。
