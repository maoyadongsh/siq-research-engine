# SIQ Hermes 智能体

`agents/hermes` 记录 SIQ Research Engine 使用的 Hermes profiles、角色边界、共享脚本、模板和前端/API 对接关系。运行态会话、日志、响应存储和用户状态默认放在 `data/hermes/home`，源码目录只维护可审阅的配置、角色规则和说明文档。

## 智能体矩阵

| Profile | 默认端口 | 前端入口 | API 前缀 | 核心职责 |
| --- | ---: | --- | --- | --- |
| `siq_assistant` | `18642` | `/chat` | `/api/chat/*` | 通用财报问答、指标查询、证据解释 |
| `siq_analysis` | `18651` | `/analysis` | `/api/analysis/*` | 年度经营诊断、财务模型、风险链条、HTML 报告 |
| `siq_factchecker` | `18649` | `/verify` | `/api/factchecker/*` | 对分析报告做事实、计算、证据和合规边界核查 |
| `siq_tracking` | `18650` | `/tracking` | `/api/tracking/*` | 持续跟踪事项、指标面板、预警和更新报告 |
| `siq_legal` | `18652` | `/legal` | `/api/legal/*` | 法规检索、合规问答和法律意见书初稿 |

## 设计原则

- 通用问答和报告型任务分离，避免入口助手承担过多职责。
- 分析、核查、跟踪、法务各自独立，形成“生成、复核、监控、合规”多角色协作。
- 所有财报数字、财务判断和风险提示必须能回到 Wiki、PostgreSQL、PDF 页码、表格编号或法规条款。
- 对外展示产物优先写入公司 Wiki 的标准目录，再由 Web 工作台读取。
- Agent 可以组织语言和推理，但不能虚构公司、指标、页码、表格、法规或数据库记录。

## 共享脚本

`profiles/shared` 保存多 profile 共用能力：

- `financial_calculator.py`：财务比率和勾稽计算。
- `financial_reconciliation_validator.py`：财务关系校验。
- `citation_schema.py`、`local_citations.py`：引用格式和本地证据映射。
- `pg_query.py`：只读 PostgreSQL 查询辅助。
- `statement_metric_lookup.py`：财务科目与指标映射。
- `update_company_index.py`：公司索引维护。

这些脚本是多 Agent 共用的证据基础，不应在各 profile 内重复造轮子。

## 运行态路径

```text
data/hermes/home/
  profiles/
    siq_assistant/
    siq_analysis/
    siq_factchecker/
    siq_tracking/
    siq_legal/
```

可用环境变量覆盖：

```bash
export SIQ_HERMES_HOME=/path/to/hermes_home
export SIQ_HERMES_PROFILES_ROOT=$SIQ_HERMES_HOME/profiles
```

`scripts/hermes/profile_dir.sh` 会根据 profile 名称解析真实目录，`start_all.sh` 会按五个默认端口启动网关。

## 启动

一键启动：

```bash
cd /home/maoyd/siq-research-engine
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start_all.sh
```

单独启动某个网关：

```bash
cd /home/maoyd/siq-research-engine
scripts/hermes/run_gateway.sh siq_analysis
```

健康检查：

```bash
curl -s http://127.0.0.1:18642/health
curl -s http://127.0.0.1:18651/health
curl -s http://127.0.0.1:18649/health
curl -s http://127.0.0.1:18650/health
curl -s http://127.0.0.1:18652/health
```

## 配置结构

每个 profile 通常包含：

| 文件 | 用途 |
| --- | --- |
| `config.yaml` | 模型 provider、fallback、工具集、超时和网关配置 |
| `SOUL.md` | 角色规则、工作边界、输出约束和质量要求 |
| `README.md` | 维护说明和使用方式 |
| `profile.yaml` | 部分 profile 的角色元信息 |
| `agent.py` / `models.py` / `schemas.py` | 需要代码化规则的 profile 辅助模块 |

## 产物目录

| 类型 | Wiki 标准目录 |
| --- | --- |
| 分析报告 | `companies/<company_id>/analysis/` |
| 事实核查 | `companies/<company_id>/factcheck/` |
| 持续跟踪 | `companies/<company_id>/tracking/` |
| 法务合规 | `companies/<company_id>/legal/` |

Web 工作台的报告页面会读取这些目录中的 HTML/JSON/Markdown 产物，并通过 API 后端生成可鉴权的溯源链接。
