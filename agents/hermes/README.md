# SIQ Hermes 智能体体系

## 平台定位

`agents/hermes` 保存 SIQ 的智能体配置、协作边界、共享脚本和角色说明。这里维护的是“可审阅的协作规则层”，而不是运行态会话或模型缓存。它把不同研究角色组织成一套受控协作系统，让智能体围绕同一份证据层工作，而不是围绕模型记忆自由发挥。

## 智能体矩阵

| Profile | 默认端口 | 前端入口 | API 前缀 | 核心职责 |
| --- | ---: | --- | --- | --- |
| `siq_assistant` | `18642` | `/chat` | `/api/chat/*` | 通用问答、指标解释、证据定位 |
| `siq_analysis` | `18651` | `/analysis` | `/api/analysis/*` | 年度经营分析、风险链条和研究报告 |
| `siq_factchecker` | `18649` | `/verify` | `/api/factchecker/*` | 对分析报告做事实、计算和证据核查 |
| `siq_tracking` | `18650` | `/tracking` | `/api/tracking/*` | 持续跟踪、预警、更新记录 |
| `siq_legal` | `18652` | `/legal` | `/api/legal/*` | 法规检索、合规分析、意见书草稿 |
| `siq_ic_master_coordinator` | `18660` | `/deals` | `/api/deals/*` | 投委会流程编排、证据门禁、专家材料收口 |
| `siq_ic_chairman` | `18661` | `/deals` | `/api/deals/*` | 投委会最终裁决、条件化投决与分歧处理 |
| `siq_ic_strategist` | `18662` | `/deals` | `/api/deals/*` | 战略适配、时点、宏观与基金 thesis |
| `siq_ic_sector_expert` | `18663` | `/deals` | `/api/deals/*` | 行业格局、产品验证、竞争与市场判断 |
| `siq_ic_finance_auditor` | `18664` | `/deals` | `/api/deals/*` | 财务一致性、预测、估值和压力测试 |
| `siq_ic_legal_scanner` | `18665` | `/deals` | `/api/deals/*` | 法务尽调、条款风险和监管暴露 |
| `siq_ic_risk_controller` | `18666` | `/deals` | `/api/deals/*` | 下行情景、红黄线、交易保护条款 |

## 协作原则

Hermes 在 SIQ 中不是“一个万能助手”，而是一组分工清晰的受控角色。它们共享证据底座，但不共享越权权限。

- 证据优先：所有关键结论都必须能回到 Wiki、PostgreSQL、PDF 页码、表格编号或法规条款。
- 角色分工：分析负责形成研究结论，核查负责拆穿错误，跟踪负责持续观察，法务负责依据和合规，投委会角色负责一级市场尽调与决策流程。
- 边界明确：任何角色都不能凭模型记忆伪造公司、指标、页码、法规或数据库记录。
- 产物可回放：报告优先写入标准目录，由 Web 和 API 再统一读取和展示。

## 当前最新状态

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| 二级市场 profiles | assistant / analysis / factchecker / tracking / legal 形成基础闭环 | 面向财报、证据包、报告、核查和持续跟踪 |
| 一级市场 IC profiles | chairman / strategist / sector / finance / legal / risk / coordinator 并行维护 | 面向 Deal OS、R1-R4 尽调、分歧裁决和投委会会议 |
| 长期记忆 | PostgreSQL 权威记忆 + Milvus 语义召回 + reranker | 支持 user_private、project_shared、system_shared 三类可见性 |
| 共享脚本 | 财务计算、勾稽校验、引用 schema、PostgreSQL query 等能力集中维护 | 减少各 profile 自行实现导致的结果漂移 |
| API / Web 接入 | 二级市场 profile 已有稳定前端入口；IC profile 通过 `/deals` 和会议室逐步产品化 | 保持模型协作与产品工作流一致 |

Hermes 的商业价值是“可治理的专家协作”。它不是把一个聊天框包装成多个角色，而是让每个角色围绕同一证据层、同一项目权限和同一产物目录工作，从而让研究过程能被复核、交接和审计。

## 共享脚本与共用能力

`profiles/shared` 保存多 profile 共用的底层能力：

| 脚本 | 作用 |
| --- | --- |
| `financial_calculator.py` | 财务比率和派生指标计算 |
| `financial_reconciliation_validator.py` | 财务勾稽校验 |
| `citation_schema.py` | 引用格式和 schema |
| `local_citations.py` | 本地证据映射与引用修复 |
| `pg_query.py` | 只读 PostgreSQL 查询辅助 |
| `statement_metric_lookup.py` | 财务科目与指标映射 |
| `update_company_index.py` | 公司索引与目录维护 |

这些能力属于共享证据基础设施，应该复用而不是在各 profile 中各自复制一份逻辑。

## 运行入口与端口

### 一键启动

```bash
cd /home/maoyd/siq-research-engine
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start_all.sh
```

### 单独启动某个 profile

```bash
cd /home/maoyd/siq-research-engine
scripts/hermes/run_gateway.sh siq_analysis
```

### 基础健康检查

```bash
curl -s http://127.0.0.1:18642/health
curl -s http://127.0.0.1:18651/health
curl -s http://127.0.0.1:18649/health
curl -s http://127.0.0.1:18650/health
curl -s http://127.0.0.1:18652/health
curl -s http://127.0.0.1:18660/health
curl -s http://127.0.0.1:18661/health
curl -s http://127.0.0.1:18662/health
curl -s http://127.0.0.1:18663/health
curl -s http://127.0.0.1:18664/health
curl -s http://127.0.0.1:18665/health
curl -s http://127.0.0.1:18666/health
```

IC profiles 默认随主链路启动，并配合 `/deals` 相关链路使用。资源受限或仅运行公开市场流程时，可设置 `SIQ_ENABLE_IC_HERMES=0` 显式关闭这 7 个网关。

使用 systemd user 部署时，7 个 IC profiles 由 `hermes-gateway-siq-ic@.service` 模板分别托管；`siq-research-engine.service` 不重复拉起 Hermes，以避免与已有网关端口冲突。

## 运行态目录

默认 runtime home：

```text
data/hermes/home/
```

常见结构：

```text
data/hermes/home/
  profiles/
  sessions/
  logs/
  responses/
```

常用环境变量：

| 变量 | 用途 |
| --- | --- |
| `SIQ_HERMES_HOME` | Hermes runtime 根目录 |
| `SIQ_HERMES_PROFILES_ROOT` | profiles 根目录 |
| `SIQ_ENABLE_IC_HERMES` | 是否启用 IC profiles 网关 |

## 产物目录与前端/API 对接

Hermes 的报告型产物最终会进入标准 Wiki 路径，再由 `apps/api` 聚合并交给前端展示。

| 类型 | 标准目录 |
| --- | --- |
| 分析报告 | `companies/<company_id>/analysis/` |
| 事实核查 | `companies/<company_id>/factcheck/` |
| 持续跟踪 | `companies/<company_id>/tracking/` |
| 法务意见 | `companies/<company_id>/legal/` |
| 一级市场流程 | `data/wiki/deals/...` |

前端与 API 的典型对接方式：

- `/chat` + `/api/chat/*` 对应 `siq_assistant`
- `/analysis` + `/api/analysis/*` 对应 `siq_analysis`
- `/verify` + `/api/factchecker/*` 对应 `siq_factchecker`
- `/tracking` + `/api/tracking/*` 对应 `siq_tracking`
- `/legal` + `/api/legal/*` 对应 `siq_legal`

## 维护原则

- 把角色边界写清楚，优先于把提示词写得“像真人”。
- 共用能力沉入 shared scripts，不在各 profile 中重复实现。
- 产物路径、profile ID 和端口要保持稳定，避免前端 / API / 网关三方漂移。
- 所有对外结论都必须和证据层绑定，允许保守、不允许编造。
- 这里保存的是协作规则和配置，不保存会话、缓存、向量索引或运行日志。

## 创新性与商业价值

Hermes 采用“职责型智能体 + 共享证据层 + 阶段状态机”，区别于让多个人格自由讨论的多智能体演示。每个 profile 的输入、禁止行为、交付物和升级条件都可检查，模型更换不会改变业务责任边界。

| 机制 | 技术难点 | 商业价值 |
| --- | --- | --- |
| Profile 即岗位合同 | AGENTS、skills、templates、tasks 和 gateway 共同约束 | 将机构研究方法论固化为可迭代资产 |
| 证据绑定输出 | evidence id、source map、审计 trace 与报告 manifest | 降低无来源结论进入正式材料的风险 |
| IC R0-R4 分阶段 | intake、专家报告、争议裁决、汇总复核、人工确认 | 投委会过程可回放、可签核、可定位责任 |
| 双层记忆 | PostgreSQL 权威账本 + Milvus 语义索引，带 scope/ACL | 支持经验复用并避免跨用户或项目泄漏 |
| 可替换模型网关 | profile 与具体模型服务解耦 | 客户可按成本、隐私和效果选择模型 |

商业壁垒来自“组织流程可执行化”：SIQ 不只提高单次写作速度，还把分析、核查、法务、风控和主席裁决变成可治理的协作系统。
