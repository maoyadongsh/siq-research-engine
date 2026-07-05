# 多市场财报规则服务

## 模块定位

`services/market-report-rules` 是 SIQ 的多市场规则中枢。它处理的不是“原始披露文件”，而是已经过下载与解析后的结构化产物，并把这些产物进一步转换为 `financial_data`、`financial_checks`、`load_plan` 和可供 Agent / importer 消费的 evidence targets。

它在系统中的角色不是“后处理脚本集合”，而是多市场 evidence package 与结构化入库契约之间的规则层。

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
