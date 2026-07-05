# SIQ README Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the SIQ main-repo README system so the root README explains the project’s innovation and technical difficulty clearly, while each major path README explains its engineering role, contracts, runtime behavior, and maintenance boundaries.

**Architecture:** Use a two-layer documentation architecture. The root README owns the system story, capability matrix, and repo navigation. Major module README files own role-specific engineering detail, upstream/downstream dependencies, evidence or data contracts, startup commands, and maintenance rules. Runtime/generated directories stay out of scope except for their project-level governance README files.

**Tech Stack:** Markdown, Git, FastAPI, React/Vite, Python services, Hermes profiles, PostgreSQL, Milvus, shell verification commands.

---

## File Structure

- Modify `README.md`: root narrative, innovation framing, capability matrix, architecture, startup, repo map.
- Modify `apps/api/README.md`, `apps/document-parser/README.md`, `apps/pdf-parser/README.md`, `apps/web/README.md`, `apps/web/e2e/README.md`: core control-plane, parsing-plane, and UI docs.
- Modify `services/market-report-finder/README.md`, `services/market-report-rules/README.md`, `services/market-report-rules/src/market_report_rules_service/markets/README.md`, `packages/market-contracts/README.md`: market services and shared contracts.
- Modify `agents/hermes/README.md`, `agents/hermes/profiles/siq_analysis/README.md`, `agents/hermes/profiles/siq_assistant/README.md`, `agents/hermes/profiles/siq_factchecker/README.md`, `agents/hermes/profiles/siq_tracking/README.md`, `agents/hermes/profiles/siq_legal/README.md`: Hermes platform and public profiles.
- Modify `agents/hermes/profiles/siq_ic_chairman/README.md`, `agents/hermes/profiles/siq_ic_finance_auditor/README.md`, `agents/hermes/profiles/siq_ic_legal_scanner/README.md`, `agents/hermes/profiles/siq_ic_master_coordinator/README.md`, `agents/hermes/profiles/siq_ic_risk_controller/README.md`, `agents/hermes/profiles/siq_ic_sector_expert/README.md`, `agents/hermes/profiles/siq_ic_shared/README.md`, `agents/hermes/profiles/siq_ic_shared/templates/README.md`, `agents/hermes/profiles/siq_ic_strategist/README.md`: IC governance and specialist profiles.
- Modify `scripts/README.md`, `scripts/vector-index/milvus-ingestion/README.md`, `scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md`, `db/imports/README.md`, `infra/model-services/README.md`, `data/README.md`, `datasets/README.md`, `eval_datasets/README.md`, `eval_datasets/document_parser_cases/README.md`, `artifacts/README.md`, `var/README.md`: tools, data governance, and runtime boundaries.

---

### Task 1: Rewrite The Root README Narrative Backbone

**Files:**
- Modify: `README.md`
- Reference: `docs/superpowers/specs/2026-07-05-siq-readme-overhaul-design.md`
- Reference: `docs/operations/local-development.md`
- Reference: `docs/architecture/2026-07-03-architecture-optimization-plan-v2.md`

- [ ] **Step 1: Snapshot the current root README and key architecture notes**

Run:

```bash
cd /home/maoyd/siq-research-engine
sed -n '1,260p' README.md
sed -n '1,220p' docs/superpowers/specs/2026-07-05-siq-readme-overhaul-design.md
sed -n '1,220p' docs/operations/local-development.md
```

Expected: current root README, design spec, and local-development commands are visible before editing.

- [ ] **Step 2: Replace the root README with the approved two-layer narrative structure**

Write `README.md` so it contains these exact top-level sections in this order:

```md
# SIQ Research Engine

一段 3-5 句的项目定位，明确 SIQ 是“从官方披露到结构化证据再到受控研究结论的可审计研究生产线”。

## 项目定位
- 强调本项目不是普通 RAG/Chat 应用。
- 强调目标是让数字、判断、风险和引用都能回到官方披露或结构化证据。

## 为什么 SIQ 难
- 解释多市场异构披露源、PDF/HTML/iXBRL/ESEF/EDINET/DART 混合解析难度。
- 解释 evidence package、source map、quality gate、database fallback、agent governance 的系统复杂度。

## 核心创新
- 官方披露直连。
- 多市场异构解析。
- 统一证据合同与可追溯引用。
- 受控多智能体协作。

## 能力矩阵
- 以市场和链路能力组织，不只按技术栈罗列。

## 系统架构
- 控制面、下载面、解析面、规则面、证据面、智能体面、运行面。

## 关键数据合同
- `document_full.json`
- `quality_report.json`
- `source_map.json`
- `financial_data.json`
- `financial_checks.json`
- market `evidence package`

## 典型工作流
- 官方披露下载。
- 文档或财报解析。
- 规则抽取与质量校验。
- Wiki/PostgreSQL/Milvus 沉淀。
- Web 与 Hermes 消费。

## 技术栈
- 按前端、控制面、解析、规则、存储、模型、运维分层描述。

## 仓库地图
- 指向 `apps/`、`services/`、`packages/`、`agents/`、`db/`、`scripts/`、`infra/`、`data/`、`var/`、`artifacts/`、`datasets/`、`eval_datasets/`。

## 快速启动
- 使用 `infra/env/local.example`、`start_all.sh`、Docker Compose 的最短路径。

## 健康检查
- 保留核心端口与 `curl` 命令。

## 关键环境变量
- 只保留最核心、最常用的一组变量，避免冗长到失去导航价值。

## 延伸阅读
- 指向子 README 和关键 `docs/` 文档。
```

- [ ] **Step 3: Verify the root README contains the required section set**

Run:

```bash
cd /home/maoyd/siq-research-engine
rg -n '^## (项目定位|为什么 SIQ 难|核心创新|能力矩阵|系统架构|关键数据合同|典型工作流|技术栈|仓库地图|快速启动|健康检查|关键环境变量|延伸阅读)$' README.md
```

Expected: thirteen matching `##` headings, one line per required section.

- [ ] **Step 4: Verify markdown formatting for the root README**

Run:

```bash
cd /home/maoyd/siq-research-engine
git diff --check -- README.md
```

Expected: no whitespace or merge-marker errors.

---

### Task 2: Rewrite The Core Application README Set

**Files:**
- Modify: `apps/api/README.md`
- Modify: `apps/document-parser/README.md`
- Modify: `apps/pdf-parser/README.md`
- Modify: `apps/web/README.md`
- Modify: `apps/web/e2e/README.md`

- [ ] **Step 1: Snapshot the current app README files**

Run:

```bash
cd /home/maoyd/siq-research-engine
for f in apps/api/README.md apps/document-parser/README.md apps/pdf-parser/README.md apps/web/README.md apps/web/e2e/README.md; do
  sed -n '1,220p' "$f"
done
```

Expected: the current app README content is visible before rewriting.

- [ ] **Step 2: Rewrite each app README with the shared application template and module-specific difficulty framing**

Use this exact section pattern for `apps/api/README.md`, `apps/document-parser/README.md`, `apps/pdf-parser/README.md`, and `apps/web/README.md`:

```md
# <模块标题>

## 模块定位
## 在系统中的位置
## 核心能力
## 技术难点
## 关键接口或标准产物
## 启动方式
## 关键环境变量
## 验证方式
## 维护原则
```

Use this exact section pattern for `apps/web/e2e/README.md`:

```md
# <模块标题>

## 测试目标
## 覆盖范围
## 运行方式
## 端口与环境变量
## mock 与真实链路边界
## 维护原则
```

While rewriting, enforce these module-specific themes:

```md
apps/api:
- 强调统一鉴权、任务编排、SSE Agent 代理、下游治理、证据访问控制。

apps/document-parser:
- 强调任意文档类型归一、统一 artifact 合同、source map、schema extraction。

apps/pdf-parser:
- 强调财报 PDF 到质量报告、表格关系、财务抽取、勾稽校验、人工修正闭环。

apps/web:
- 强调研究工作台属性，而不是前端展示层。

apps/web/e2e:
- 强调烟雾测试覆盖范围、mock API 策略、独立端口策略和对完整后端的非强依赖。
```

- [ ] **Step 3: Verify the shared application headings landed in all target files**

Run:

```bash
cd /home/maoyd/siq-research-engine
rg -n '^## (模块定位|在系统中的位置|核心能力|技术难点|关键接口或标准产物|启动方式|关键环境变量|验证方式|维护原则)$' apps/api/README.md apps/document-parser/README.md apps/pdf-parser/README.md apps/web/README.md
rg -n '^## (测试目标|覆盖范围|运行方式|端口与环境变量|mock 与真实链路边界|维护原则)$' apps/web/e2e/README.md
```

Expected: each app file contains the required headings exactly once.

- [ ] **Step 4: Verify markdown formatting for the app README set**

Run:

```bash
cd /home/maoyd/siq-research-engine
git diff --check -- apps/api/README.md apps/document-parser/README.md apps/pdf-parser/README.md apps/web/README.md apps/web/e2e/README.md
```

Expected: no whitespace or merge-marker errors.

---

### Task 3: Rewrite The Market Services And Contract README Set

**Files:**
- Modify: `services/market-report-finder/README.md`
- Modify: `services/market-report-rules/README.md`
- Modify: `services/market-report-rules/src/market_report_rules_service/markets/README.md`
- Modify: `packages/market-contracts/README.md`

- [ ] **Step 1: Snapshot the current services and contracts README files**

Run:

```bash
cd /home/maoyd/siq-research-engine
for f in services/market-report-finder/README.md services/market-report-rules/README.md services/market-report-rules/src/market_report_rules_service/markets/README.md packages/market-contracts/README.md; do
  sed -n '1,220p' "$f"
done
```

Expected: current service and contract docs are visible before rewriting.

- [ ] **Step 2: Rewrite the service and contract README files with explicit boundary and contract sections**

Use this exact section pattern for `services/market-report-finder/README.md`, `services/market-report-rules/README.md`, and `packages/market-contracts/README.md`:

```md
# <模块标题>

## 模块定位
## 在系统中的位置
## 核心能力
## 技术难点
## 输入输出或关键合同
## 启动方式
## 关键环境变量
## 验证方式
## 维护原则
```

Use this exact section pattern for `services/market-report-rules/src/market_report_rules_service/markets/README.md`:

```md
# Market Modules

## 目录职责
## 新增市场必须提供的文件
## 可选扩展文件
## 共享层必须保持轻薄的原因
## 新增市场时的注册与约束
```

While rewriting, enforce these exact emphasis points:

```md
market-report-finder:
- 它是多市场官方披露入口抽象层，而不是“下载脚本集合”。

market-report-rules:
- 它是 evidence package、字段归一、质量门禁、load plan 和入库契约的规则中枢。

market modules README:
- 说明为什么业务差异必须沉到 `markets/<code>/`，不能堆到共享层。

market-contracts:
- 强调稳定 filesystem contract、shared validation、dependency-light 设计和跨服务一致性价值。
```

- [ ] **Step 3: Verify the service README headings landed in all target files**

Run:

```bash
cd /home/maoyd/siq-research-engine
rg -n '^## (模块定位|在系统中的位置|核心能力|技术难点|输入输出或关键合同|启动方式|关键环境变量|验证方式|维护原则)$' services/market-report-finder/README.md services/market-report-rules/README.md packages/market-contracts/README.md
rg -n '^## (目录职责|新增市场必须提供的文件|可选扩展文件|共享层必须保持轻薄的原因|新增市场时的注册与约束)$' services/market-report-rules/src/market_report_rules_service/markets/README.md
```

Expected: all required headings are present.

- [ ] **Step 4: Verify markdown formatting for the service README set**

Run:

```bash
cd /home/maoyd/siq-research-engine
git diff --check -- services/market-report-finder/README.md services/market-report-rules/README.md services/market-report-rules/src/market_report_rules_service/markets/README.md packages/market-contracts/README.md
```

Expected: no whitespace or merge-marker errors.

---

### Task 4: Rewrite The Hermes Platform And Public Profile README Set

**Files:**
- Modify: `agents/hermes/README.md`
- Modify: `agents/hermes/profiles/siq_analysis/README.md`
- Modify: `agents/hermes/profiles/siq_assistant/README.md`
- Modify: `agents/hermes/profiles/siq_factchecker/README.md`
- Modify: `agents/hermes/profiles/siq_tracking/README.md`
- Modify: `agents/hermes/profiles/siq_legal/README.md`

- [ ] **Step 1: Snapshot the current Hermes platform and public profile README files**

Run:

```bash
cd /home/maoyd/siq-research-engine
for f in agents/hermes/README.md agents/hermes/profiles/siq_analysis/README.md agents/hermes/profiles/siq_assistant/README.md agents/hermes/profiles/siq_factchecker/README.md agents/hermes/profiles/siq_tracking/README.md agents/hermes/profiles/siq_legal/README.md; do
  sed -n '1,220p' "$f"
done
```

Expected: current Hermes README content is visible before rewriting.

- [ ] **Step 2: Rewrite the Hermes platform README and the five public profiles with a controlled-agent template**

Use this exact section pattern for `agents/hermes/README.md`:

```md
# <模块标题>

## 平台定位
## 智能体矩阵
## 协作原则
## 共享脚本与共用能力
## 运行入口与端口
## 运行态目录
## 产物目录与前端/API 对接
## 维护原则
```

Use this exact section pattern for each of the five public profile README files:

```md
# <模块标题>

## 角色定位
## 职责边界
## 依赖证据
## 输出产物
## 与其他 Agent 的协同关系
## 禁止行为
## 运行入口
## 维护原则
```

Apply these exact profile distinctions:

```md
siq_analysis:
- 年度经营诊断、财务模型、风险链条、可回溯分析报告。

siq_assistant:
- 轻量查询与解释，不替代专题报告。

siq_factchecker:
- 对分析报告做独立事实、计算、证据和边界复核。

siq_tracking:
- 把一次性分析转成持续事项、指标、预警和更新。

siq_legal:
- 基于法规库检索与引用的合规初筛和意见书草拟，不替代正式律师意见。
```

- [ ] **Step 3: Verify the Hermes platform and public profile headings landed in all target files**

Run:

```bash
cd /home/maoyd/siq-research-engine
rg -n '^## (平台定位|智能体矩阵|协作原则|共享脚本与共用能力|运行入口与端口|运行态目录|产物目录与前端/API 对接|维护原则)$' agents/hermes/README.md
rg -n '^## (角色定位|职责边界|依赖证据|输出产物|与其他 Agent 的协同关系|禁止行为|运行入口|维护原则)$' agents/hermes/profiles/siq_analysis/README.md agents/hermes/profiles/siq_assistant/README.md agents/hermes/profiles/siq_factchecker/README.md agents/hermes/profiles/siq_tracking/README.md agents/hermes/profiles/siq_legal/README.md
```

Expected: all required headings are present.

- [ ] **Step 4: Verify markdown formatting for the Hermes public README set**

Run:

```bash
cd /home/maoyd/siq-research-engine
git diff --check -- agents/hermes/README.md agents/hermes/profiles/siq_analysis/README.md agents/hermes/profiles/siq_assistant/README.md agents/hermes/profiles/siq_factchecker/README.md agents/hermes/profiles/siq_tracking/README.md agents/hermes/profiles/siq_legal/README.md
```

Expected: no whitespace or merge-marker errors.

---

### Task 5: Rewrite The IC Profile And Shared Governance README Set

**Files:**
- Modify: `agents/hermes/profiles/siq_ic_chairman/README.md`
- Modify: `agents/hermes/profiles/siq_ic_finance_auditor/README.md`
- Modify: `agents/hermes/profiles/siq_ic_legal_scanner/README.md`
- Modify: `agents/hermes/profiles/siq_ic_master_coordinator/README.md`
- Modify: `agents/hermes/profiles/siq_ic_risk_controller/README.md`
- Modify: `agents/hermes/profiles/siq_ic_sector_expert/README.md`
- Modify: `agents/hermes/profiles/siq_ic_shared/README.md`
- Modify: `agents/hermes/profiles/siq_ic_shared/templates/README.md`
- Modify: `agents/hermes/profiles/siq_ic_strategist/README.md`

- [ ] **Step 1: Snapshot the current IC README files**

Run:

```bash
cd /home/maoyd/siq-research-engine
for f in agents/hermes/profiles/siq_ic_chairman/README.md agents/hermes/profiles/siq_ic_finance_auditor/README.md agents/hermes/profiles/siq_ic_legal_scanner/README.md agents/hermes/profiles/siq_ic_master_coordinator/README.md agents/hermes/profiles/siq_ic_risk_controller/README.md agents/hermes/profiles/siq_ic_sector_expert/README.md agents/hermes/profiles/siq_ic_shared/README.md agents/hermes/profiles/siq_ic_shared/templates/README.md agents/hermes/profiles/siq_ic_strategist/README.md; do
  sed -n '1,220p' "$f"
done
```

Expected: current IC docs are visible before rewriting.

- [ ] **Step 2: Rewrite the IC profile README files as governed specialist roles rather than identity cards**

Use this exact section pattern for each specialist IC profile README file:

```md
# <模块标题>

## 角色定位
## 身份与可执行 Profile ID
## 职责边界
## 依赖证据
## 协作关系
## 禁止行为
## 运行入口
## 维护原则
```

Use this exact section pattern for `agents/hermes/profiles/siq_ic_shared/README.md`:

```md
# <模块标题>

## 目录定位
## 共享合同与政策文件
## 对可执行 Profile 的约束
## 与 `data/wiki/deals` 的关系
## 不应放入本目录的内容
## 维护原则
```

Use this exact section pattern for `agents/hermes/profiles/siq_ic_shared/templates/README.md`:

```md
# <模块标题>

## 目录定位
## 当前模板范围
## 模板使用规则
## 后续扩展边界
```

Preserve these role-specific distinctions:

```md
siq_ic_chairman:
- 最终综合、分歧裁决、条件化投决。

siq_ic_master_coordinator:
- 流程编排、证据门禁、专家输出收口。

siq_ic_strategist:
- 基金 thesis、时点、宏观与组合配置。

siq_ic_sector_expert:
- 行业格局、产品客户、技术路线与市场验证。

siq_ic_finance_auditor:
- 财务一致性、预测、估值、压力测试。

siq_ic_legal_scanner:
- 法务尽调、条款风险、监管暴露。

siq_ic_risk_controller:
- 下行情景、红黄线、保护条款与投后监控。
```

- [ ] **Step 3: Verify the IC headings landed in all target files**

Run:

```bash
cd /home/maoyd/siq-research-engine
rg -n '^## (角色定位|身份与可执行 Profile ID|职责边界|依赖证据|协作关系|禁止行为|运行入口|维护原则)$' agents/hermes/profiles/siq_ic_chairman/README.md agents/hermes/profiles/siq_ic_finance_auditor/README.md agents/hermes/profiles/siq_ic_legal_scanner/README.md agents/hermes/profiles/siq_ic_master_coordinator/README.md agents/hermes/profiles/siq_ic_risk_controller/README.md agents/hermes/profiles/siq_ic_sector_expert/README.md agents/hermes/profiles/siq_ic_strategist/README.md
rg -n '^## (目录定位|共享合同与政策文件|对可执行 Profile 的约束|与 `data/wiki/deals` 的关系|不应放入本目录的内容|维护原则)$' agents/hermes/profiles/siq_ic_shared/README.md
rg -n '^## (目录定位|当前模板范围|模板使用规则|后续扩展边界)$' agents/hermes/profiles/siq_ic_shared/templates/README.md
```

Expected: all required headings are present.

- [ ] **Step 4: Verify markdown formatting for the IC README set**

Run:

```bash
cd /home/maoyd/siq-research-engine
git diff --check -- agents/hermes/profiles/siq_ic_chairman/README.md agents/hermes/profiles/siq_ic_finance_auditor/README.md agents/hermes/profiles/siq_ic_legal_scanner/README.md agents/hermes/profiles/siq_ic_master_coordinator/README.md agents/hermes/profiles/siq_ic_risk_controller/README.md agents/hermes/profiles/siq_ic_sector_expert/README.md agents/hermes/profiles/siq_ic_shared/README.md agents/hermes/profiles/siq_ic_shared/templates/README.md agents/hermes/profiles/siq_ic_strategist/README.md
```

Expected: no whitespace or merge-marker errors.

---

### Task 6: Rewrite The Tools, Data Governance, And Runtime Boundary README Set

**Files:**
- Modify: `scripts/README.md`
- Modify: `scripts/vector-index/milvus-ingestion/README.md`
- Modify: `scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md`
- Modify: `db/imports/README.md`
- Modify: `infra/model-services/README.md`
- Modify: `data/README.md`
- Modify: `datasets/README.md`
- Modify: `eval_datasets/README.md`
- Modify: `eval_datasets/document_parser_cases/README.md`
- Modify: `artifacts/README.md`
- Modify: `var/README.md`

- [ ] **Step 1: Snapshot the current tools and data-governance README files**

Run:

```bash
cd /home/maoyd/siq-research-engine
for f in scripts/README.md scripts/vector-index/milvus-ingestion/README.md scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md db/imports/README.md infra/model-services/README.md data/README.md datasets/README.md eval_datasets/README.md eval_datasets/document_parser_cases/README.md artifacts/README.md var/README.md; do
  sed -n '1,220p' "$f"
done
```

Expected: current tool and data docs are visible before rewriting.

- [ ] **Step 2: Rewrite the tools and governance README files with a strict boundary template**

Use this exact section pattern for `scripts/README.md`, `scripts/vector-index/milvus-ingestion/README.md`, `scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md`, `db/imports/README.md`, and `infra/model-services/README.md`:

```md
# <模块标题>

## 目录职责
## 在系统中的位置
## 核心内容
## 典型用法
## 关键边界或治理规则
## 维护建议
```

Use this exact section pattern for `data/README.md`, `datasets/README.md`, `eval_datasets/README.md`, `eval_datasets/document_parser_cases/README.md`, `artifacts/README.md`, and `var/README.md`:

```md
# <模块标题>

## 目录定位
## 主要内容
## 与其他数据目录的边界
## 可提交与不可提交内容
## 运行或使用建议
## 维护原则
```

Enforce these exact governance distinctions:

```md
data:
- 历史兼容运行态目录。

var:
- 新增本地运行态推荐目录。

artifacts:
- 构建、测试、评测和批处理生成产物。

datasets:
- 可版本化稳定样本、fixtures、小型样本。

eval_datasets:
- 历史评测语料和回归集，不是单次运行输出目录。
```

- [ ] **Step 3: Verify the tools and governance headings landed in all target files**

Run:

```bash
cd /home/maoyd/siq-research-engine
rg -n '^## (目录职责|在系统中的位置|核心内容|典型用法|关键边界或治理规则|维护建议)$' scripts/README.md scripts/vector-index/milvus-ingestion/README.md scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md db/imports/README.md infra/model-services/README.md
rg -n '^## (目录定位|主要内容|与其他数据目录的边界|可提交与不可提交内容|运行或使用建议|维护原则)$' data/README.md datasets/README.md eval_datasets/README.md eval_datasets/document_parser_cases/README.md artifacts/README.md var/README.md
```

Expected: all required headings are present.

- [ ] **Step 4: Verify markdown formatting for the tools and governance README set**

Run:

```bash
cd /home/maoyd/siq-research-engine
git diff --check -- scripts/README.md scripts/vector-index/milvus-ingestion/README.md scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md db/imports/README.md infra/model-services/README.md data/README.md datasets/README.md eval_datasets/README.md eval_datasets/document_parser_cases/README.md artifacts/README.md var/README.md
```

Expected: no whitespace or merge-marker errors.

---

### Task 7: Run Global Consistency Verification Across The README Set

**Files:**
- Modify: `README.md`
- Modify: `apps/api/README.md`
- Modify: `apps/document-parser/README.md`
- Modify: `apps/pdf-parser/README.md`
- Modify: `apps/web/README.md`
- Modify: `apps/web/e2e/README.md`
- Modify: `services/market-report-finder/README.md`
- Modify: `services/market-report-rules/README.md`
- Modify: `services/market-report-rules/src/market_report_rules_service/markets/README.md`
- Modify: `packages/market-contracts/README.md`
- Modify: `agents/hermes/README.md`
- Modify: `agents/hermes/profiles/siq_analysis/README.md`
- Modify: `agents/hermes/profiles/siq_assistant/README.md`
- Modify: `agents/hermes/profiles/siq_factchecker/README.md`
- Modify: `agents/hermes/profiles/siq_tracking/README.md`
- Modify: `agents/hermes/profiles/siq_legal/README.md`
- Modify: `agents/hermes/profiles/siq_ic_chairman/README.md`
- Modify: `agents/hermes/profiles/siq_ic_finance_auditor/README.md`
- Modify: `agents/hermes/profiles/siq_ic_legal_scanner/README.md`
- Modify: `agents/hermes/profiles/siq_ic_master_coordinator/README.md`
- Modify: `agents/hermes/profiles/siq_ic_risk_controller/README.md`
- Modify: `agents/hermes/profiles/siq_ic_sector_expert/README.md`
- Modify: `agents/hermes/profiles/siq_ic_shared/README.md`
- Modify: `agents/hermes/profiles/siq_ic_shared/templates/README.md`
- Modify: `agents/hermes/profiles/siq_ic_strategist/README.md`
- Modify: `scripts/README.md`
- Modify: `scripts/vector-index/milvus-ingestion/README.md`
- Modify: `scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md`
- Modify: `db/imports/README.md`
- Modify: `infra/model-services/README.md`
- Modify: `data/README.md`
- Modify: `datasets/README.md`
- Modify: `eval_datasets/README.md`
- Modify: `eval_datasets/document_parser_cases/README.md`
- Modify: `artifacts/README.md`
- Modify: `var/README.md`

- [ ] **Step 1: Verify all planned README files still exist and are readable**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 - <<'PY'
from pathlib import Path
paths = [
    'README.md',
    'apps/api/README.md',
    'apps/document-parser/README.md',
    'apps/pdf-parser/README.md',
    'apps/web/README.md',
    'apps/web/e2e/README.md',
    'services/market-report-finder/README.md',
    'services/market-report-rules/README.md',
    'services/market-report-rules/src/market_report_rules_service/markets/README.md',
    'packages/market-contracts/README.md',
    'agents/hermes/README.md',
    'agents/hermes/profiles/siq_analysis/README.md',
    'agents/hermes/profiles/siq_assistant/README.md',
    'agents/hermes/profiles/siq_factchecker/README.md',
    'agents/hermes/profiles/siq_tracking/README.md',
    'agents/hermes/profiles/siq_legal/README.md',
    'agents/hermes/profiles/siq_ic_chairman/README.md',
    'agents/hermes/profiles/siq_ic_finance_auditor/README.md',
    'agents/hermes/profiles/siq_ic_legal_scanner/README.md',
    'agents/hermes/profiles/siq_ic_master_coordinator/README.md',
    'agents/hermes/profiles/siq_ic_risk_controller/README.md',
    'agents/hermes/profiles/siq_ic_sector_expert/README.md',
    'agents/hermes/profiles/siq_ic_shared/README.md',
    'agents/hermes/profiles/siq_ic_shared/templates/README.md',
    'agents/hermes/profiles/siq_ic_strategist/README.md',
    'scripts/README.md',
    'scripts/vector-index/milvus-ingestion/README.md',
    'scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md',
    'db/imports/README.md',
    'infra/model-services/README.md',
    'data/README.md',
    'datasets/README.md',
    'eval_datasets/README.md',
    'eval_datasets/document_parser_cases/README.md',
    'artifacts/README.md',
    'var/README.md',
]
missing = [p for p in paths if not Path(p).exists()]
assert not missing, missing
print(len(paths))
PY
```

Expected: prints `36` and exits successfully.

- [ ] **Step 2: Verify no placeholder language remains in the rewritten README set**

Run:

```bash
cd /home/maoyd/siq-research-engine
rg -n 'TODO|TBD|待补|待定|Placeholder|稍后补充|后续补充' README.md apps/api/README.md apps/document-parser/README.md apps/pdf-parser/README.md apps/web/README.md apps/web/e2e/README.md services/market-report-finder/README.md services/market-report-rules/README.md services/market-report-rules/src/market_report_rules_service/markets/README.md packages/market-contracts/README.md agents/hermes/README.md agents/hermes/profiles/siq_analysis/README.md agents/hermes/profiles/siq_assistant/README.md agents/hermes/profiles/siq_factchecker/README.md agents/hermes/profiles/siq_tracking/README.md agents/hermes/profiles/siq_legal/README.md agents/hermes/profiles/siq_ic_chairman/README.md agents/hermes/profiles/siq_ic_finance_auditor/README.md agents/hermes/profiles/siq_ic_legal_scanner/README.md agents/hermes/profiles/siq_ic_master_coordinator/README.md agents/hermes/profiles/siq_ic_risk_controller/README.md agents/hermes/profiles/siq_ic_sector_expert/README.md agents/hermes/profiles/siq_ic_shared/README.md agents/hermes/profiles/siq_ic_shared/templates/README.md agents/hermes/profiles/siq_ic_strategist/README.md scripts/README.md scripts/vector-index/milvus-ingestion/README.md scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md db/imports/README.md infra/model-services/README.md data/README.md datasets/README.md eval_datasets/README.md eval_datasets/document_parser_cases/README.md artifacts/README.md var/README.md
```

Expected: no output and exit code `1` because no placeholder matches remain.

- [ ] **Step 3: Verify runtime/governance directory distinctions remain explicit**

Run:

```bash
cd /home/maoyd/siq-research-engine
rg -n '历史兼容运行态|新增本地运行态推荐目录|构建、测试、评测和批处理生成产物|可版本化稳定样本|历史评测语料和回归集' data/README.md var/README.md artifacts/README.md datasets/README.md eval_datasets/README.md
```

Expected: all five governance phrases appear in the expected files.

- [ ] **Step 4: Verify all rewritten README files are clean in git diff**

Run:

```bash
cd /home/maoyd/siq-research-engine
git diff --check -- README.md apps/api/README.md apps/document-parser/README.md apps/pdf-parser/README.md apps/web/README.md apps/web/e2e/README.md services/market-report-finder/README.md services/market-report-rules/README.md services/market-report-rules/src/market_report_rules_service/markets/README.md packages/market-contracts/README.md agents/hermes/README.md agents/hermes/profiles/siq_analysis/README.md agents/hermes/profiles/siq_assistant/README.md agents/hermes/profiles/siq_factchecker/README.md agents/hermes/profiles/siq_tracking/README.md agents/hermes/profiles/siq_legal/README.md agents/hermes/profiles/siq_ic_chairman/README.md agents/hermes/profiles/siq_ic_finance_auditor/README.md agents/hermes/profiles/siq_ic_legal_scanner/README.md agents/hermes/profiles/siq_ic_master_coordinator/README.md agents/hermes/profiles/siq_ic_risk_controller/README.md agents/hermes/profiles/siq_ic_sector_expert/README.md agents/hermes/profiles/siq_ic_shared/README.md agents/hermes/profiles/siq_ic_shared/templates/README.md agents/hermes/profiles/siq_ic_strategist/README.md scripts/README.md scripts/vector-index/milvus-ingestion/README.md scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md db/imports/README.md infra/model-services/README.md data/README.md datasets/README.md eval_datasets/README.md eval_datasets/document_parser_cases/README.md artifacts/README.md var/README.md
```

Expected: no whitespace or merge-marker errors.
