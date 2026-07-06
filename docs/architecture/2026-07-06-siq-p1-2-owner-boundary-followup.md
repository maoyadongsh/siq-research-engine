# SIQ P1.2 Owner Boundary Follow-up

日期：2026-07-06

## 决策

`2026-07-06-siq-quality-hardening-taskbook.md` 中 P1.2 的剩余大文件 owner 收口后置为独立稳定化任务。

后置原因：

- P0、P1.1、P1.3、P2.1、P2.2 已形成可验证闭环。
- 当前 `./scripts/check_all.sh` 与全量 Playwright E2E 已通过。
- P1.2 剩余项属于架构债治理，不阻塞 HK 二级市场 MVP、质量门禁、安全 cookie、CI 或评测体系。
- 若继续混在同一轮改动里，会扩大 diff 和回归面，降低当前验收确定性。

## 当前已完成切片

- `PrimaryMarketMeeting.tsx` 的 hooks warning 已修复。
- `npm run check:frontend` 已无相关 warning。
- 新增逻辑没有继续扩大 `agent_chat_runtime_impl.py`。

## 后续任务

### 1. `market_reports.py` package action owner 下沉

目标：

- 将 package build/import/vector-ingest 的 plan、quality gate、command payload 组装继续下沉到 service/helper。
- Router 只保留鉴权、请求解析、异常映射和 response 返回。

建议文件：

- `apps/api/routers/market_reports.py`
- `apps/api/services/market_package_repository.py`
- 可新增 `apps/api/services/market_package_actions.py`

验收：

```bash
cd apps/api
uv run python -m pytest tests/test_market_reports_proxy.py tests/test_market_package_repository.py tests/test_market_report_commands.py
```

### 2. `pdf_parser_app_impl.py` route payload owner 下沉

目标：

- 继续把 route response payload 组装移到既有 service 或小型 helper。
- 不迁移 worker loop，不改变 API response contract。
- 优先选择 result/quality/financial/source 这类边界清楚的 route。

建议文件：

- `apps/pdf-parser/pdf_parser_app_impl.py`
- `apps/pdf-parser/pdf_parser_response_service.py`
- `apps/pdf-parser/pdf_parser_quality_service.py`
- `apps/pdf-parser/pdf_parser_financial_service.py`

验收：

```bash
cd apps/pdf-parser
uv run python -m pytest tests/test_pdf_parser_response_service.py tests/test_pdf_parser_result_route.py tests/test_pdf_parser_quality_financial_route.py tests/test_pdf_parser_ensure_wrappers.py
```

### 3. `agent_chat_runtime_impl.py` 边界审计

目标：

- 禁止新功能继续写进 `agent_chat_runtime_impl.py`。
- 将新增逻辑优先放到已有 `agent_runtime_*` service。
- 只做有测试保护的小切片，不做大规模搬迁。

建议文件：

- `apps/api/services/agent_chat_runtime_impl.py`
- `apps/api/services/agent_runtime_*.py`
- `apps/api/tests/test_agent_runtime_*.py`

验收：

```bash
cd apps/api
uv run python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_runtime_context.py tests/test_agent_runtime_tool_output.py
```

## 完成定义

每个切片独立完成：

```bash
git diff --check
相关单测
```

整轮 P1.2 follow-up 完成：

```bash
./scripts/check_all.sh
cd apps/web && npm run e2e
```

## 不做事项

- 不改变公开 API 路径、请求体或 response schema。
- 不重写 worker loop。
- 不混入 P2 产品功能、认证、安全 cookie、CI 或评测体系新需求。
- 不为了追求文件行数而做无行为收益的大搬迁。
