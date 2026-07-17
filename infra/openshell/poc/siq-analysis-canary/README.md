# SIQ 分析 OpenShell 灰度

本目录记录独立的 `NOT_PRODUCTION_CANARY` 生命周期。实现刻意复用已经审阅过的 wide-pilot 生命周期机制，避免维护第二套 sandbox 创建栈。

## 合同

- 状态目录：`var/openshell/canary/siq-analysis/`；
- 显式确认参数：`--acknowledge-not-production-canary`；
- run ID：`canary-<12hex>`；
- endpoint：`127.0.0.1:28651`；
- 服务提供方：MiniMax、StepFun、Kimi、Tavily；
- mounts：七个业务 mount，加五个只读 OpenShell 控制 mount；
- 写入范围：被选公司既有 `analysis/` 根目录；
- 不可变范围：公司事实、报告、其他公司、代码、配置、prompt、workflow 与 OpenShell 控制状态；
- `analysis/` 内允许正常 create、modify、rename、delete；只有根目录删除或批量破坏性删除会越过 deletion-guard 阈值；
- 宿主 runtime 与正式 readiness 不变。

操作命令和失败恢复流程见 `docs/runbooks/openshell/siq-analysis-canary.md`。
