# 二级市场全市场研究智能体发布与回滚

## 1. 适用范围

本手册只适用于智能分析、事实核查、持续跟踪的六市场入口和工作流。法务合规保持 CN-only；发布与回滚不得修改 `siq_legal` profile、法规库、法务工作流或境外 legal 目录。

两个开关在开发模板和容器编排中均默认关闭：

| 开关 | 默认值 | 作用 |
| --- | --- | --- |
| `SIQ_MULTI_MARKET_RESEARCH_ENABLED` | `0` | 同时控制前三个页面的全市场入口和 API/工作流的境外能力 |
| `SIQ_US_SEC_ANALYSIS_ENABLED` | `0` | 在总开关开启后，独立控制美国 SEC HTML/iXBRL 分析适配器 |

浏览器端不接受 localStorage 或 sessionStorage 覆盖。前端使用构建期 `VITE_SIQ_MULTI_MARKET_RESEARCH_ENABLED`，由 `start_all.sh` 或 Docker Compose 从同一个 `SIQ_MULTI_MARKET_RESEARCH_ENABLED` 映射，禁止单独维护第二份发布值。

## 2. 发布前门禁

发布负责人必须确认：

1. 六市场黄金样本、10-K、10-Q、warning、非自然财年和原币种用例通过。
2. API、market-contracts、三个 profile、前端单元测试和目标 E2E 通过。
3. `reports/`、`metrics/`、`evidence/`、`semantic/`、`graph/`、`company.json` 与事实索引哈希在工作流前后不变。
4. 旧 CN `?company=&result=` 链接仍可展示；法务页面忽略境外 URL 参数且无市场选择器。
5. `/api/research-universe` 在 Vite 和生产 Nginx 均代理到聚合 API，不进入报告下载服务回退路由。
6. 指标端点受现有服务令牌保护，日志采集端可以按 `request_id` 和 ResearchIdentity 查询。

目标前端 E2E 必须显式打开部署开关，默认测试环境仍保持关闭：

```bash
cd /home/maoyd/siq-research-engine/apps/web
SIQ_MULTI_MARKET_RESEARCH_ENABLED=1 npm run e2e -- e2e/tests/secondary-market-multi-market-agents.spec.ts
```

## 3. 灰度启用

### 3.1 非美国市场

在受控环境文件中设置：

```text
SIQ_MULTI_MARKET_RESEARCH_ENABLED=1
SIQ_US_SEC_ANALYSIS_ENABLED=0
```

本地 `start_all.sh` 会在同一进程环境中把总开关映射给 Vite。Docker 发布必须重新构建 Web 静态资源，并用同一环境文件重启 Web 与 API：

```bash
docker compose -f infra/docker/docker-compose.yml --env-file <受控环境文件> build web
docker compose -f infra/docker/docker-compose.yml --env-file <受控环境文件> up -d web api
```

先验证 CN、HK、EU、KR、JP 的市场、公司、源报告和生成结果级联，再观察至少一个完整任务周期。美国市场此时应显示 `source_adapter_unavailable` capability，不得回退到 PDF 或 CN 链路。

### 3.2 美国 SEC 适配器

非美国市场稳定且 10-K/10-Q 门禁通过后，将：

```text
SIQ_US_SEC_ANALYSIS_ENABLED=1
```

只需重启 API；总开关必须继续为 `1`。验证 SEC 报告只使用 HTML/iXBRL/XBRL locator，不产生伪造 PDF 页码。

## 4. 观测与告警

结构化日志事件固定包含：

- `request_id`
- `agent_type`
- `market`
- `company_key_summary`（仅 SHA-256 摘要）
- `research_identity`（只含 market、company_id、filing_id、parse_run_id）
- `source_family`
- `adapter_version`
- `artifact_id`
- `status`

日志不得包含报告正文、完整 Prompt、浏览器提交的文件路径或服务端敏感本地路径。

Prometheus 指标：

| 指标 | 说明 |
| --- | --- |
| `siq_research_readiness_total` | 固定 market、agent_type、ready/degraded/unavailable 标签 |
| `siq_research_workflow_terminal_total` | 固定 market、agent_type、success/degraded/failed 标签 |
| `siq_research_identity_mismatch_total` | 身份不一致计数 |
| `siq_research_citation_failure_total` | 引用缺失或不可回溯计数 |

以下任一情况应暂停扩大灰度：

- 任一市场 failed 比例持续上升；
- identity mismatch 非零且无法用失效页面状态解释；
- citation failure 增长或 SEC 结果出现 PDF locator；
- readiness 从 ready 变为 unavailable；
- 法务页面出现境外公司或市场选择器。

## 5. 回滚

### 5.1 全市场回滚

将总开关恢复为 `0`，重新构建 Web 并重启 Web 与 API：

```text
SIQ_MULTI_MARKET_RESEARCH_ENABLED=0
SIQ_US_SEC_ANALYSIS_ENABLED=0
```

```bash
docker compose -f infra/docker/docker-compose.yml --env-file <受控环境文件> build web
docker compose -f infra/docker/docker-compose.yml --env-file <受控环境文件> up -d web api
```

回滚后的前三个页面必须立即恢复原 CN 页面和工作流。禁止删除、移动或重写已有 analysis、factcheck、tracking 产物；禁止重建多市场 Wiki；禁止触碰源报告和事实目录。已生成的 v2 sidecar 保留，待重新启用后仍按精确身份索引。

### 5.2 仅回滚美国适配器

总开关保持 `1`，仅设置：

```text
SIQ_US_SEC_ANALYSIS_ENABLED=0
```

重启 API 后，美国市场返回明确 capability 降级，其余五个市场继续运行。不得自动将美国报告转给 PDF 或 CN 链路。

## 6. 回滚后核验

1. 前三页无市场选择器且只请求旧 CN API。
2. 旧 CN 分享链接和历史 HTML 可展示。
3. 法务合规仍只使用 CN 公司和既有 legal API。
4. 新旧派生产物数量未因回滚减少。
5. `/metrics` 可继续看到回滚前的失败与降级计数，日志可按 ResearchIdentity 还原故障范围。
