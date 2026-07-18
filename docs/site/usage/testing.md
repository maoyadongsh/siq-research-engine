# 测试与验证

SIQ Research Engine 采用多层级测试体系，覆盖全仓基础门禁、控制面、前端、解析器、市场服务、PostgreSQL 入库以及 OpenShell 专项回归。

## 全仓基础门禁

执行全仓聚合检查（包含 Python、前端、脚本、market contract 和工程 hygiene）：

```bash
cd /home/maoyd/siq-research-engine
scripts/check_all.sh
git diff --check
```

## 局部验证

针对各子系统单独执行验证：

```bash
cd apps/api && uv run python -m pytest tests
cd apps/web && npm run check:frontend
cd apps/pdf-parser && pytest -q tests
cd apps/document-parser && pytest -q tests
cd services/market-report-finder && uv run pytest
cd services/market-report-rules && uv run pytest
cd packages/market-contracts && uv run python -m pytest tests
```

## 测试体系层级

| 层级 | 命令 | 用途 |
| --- | --- | --- |
| 全仓基础门禁 | scripts/check_all.sh | 聚合 Python、前端、脚本、market contract 和工程 hygiene 检查 |
| 控制面 | cd apps/api && uv run python -m pytest tests | 鉴权、Agent runtime、Deal OS、会议、market package、source access |
| 前端 | cd apps/web && npm run check:frontend | ESLint、TypeScript build、Vite build |
| 前端 E2E | cd apps/web && npm run e2e | Playwright smoke，默认使用 mock API |
| PDF/文档解析 | pytest -q apps/pdf-parser/tests apps/document-parser/tests | parser artifact、source map、quality、table relation、bridge |
| 市场服务 | uv run pytest in services/* and packages/market-contracts | 官方披露入口、规则服务、package contract |
| PostgreSQL 入库 | pytest -q db/imports/tests | 多市场 schema、quality gate、幂等写入、持久化校验 |
| OpenShell 专项回归 | 见 docs/siq-openshell-hermes-integration-status.md | 最近记录 78 passed |
| OpenShell 发布门禁 | python3 scripts/openshell/check_v06_completion.py --json | 当前正式生产门禁仍为 NO_GO |

## 测试资产规模

| 测试资产 | 当前数量 | 覆盖重点 |
| --- | --- | --- |
| Python 测试文件 | 469 | API、parser、market services、contracts、db imports、Hermes、OpenShell、model-services |
| TypeScript/Playwright/Node 测试文件 | 115 | Web 路由、工作台交互、meeting 前端协议、E2E smoke、iOS capture 合同 |
| Shell 脚本 | 69 | 启动、运维、OpenShell、Hermes、模型服务和 smoke 入口 |