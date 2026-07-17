# `siq_analysis` OpenShell 宽松业务 Pilot

> `NOT_PRODUCTION`。该 pilot 只证明一条真实 SIQ 数据路径可以穿过正式 OpenShell 镜像和隔离资产。结果始终为 `readiness_effect=none`，不能作为正式 A/B、切流或完成门禁证据。

这是 `siq-analysis-observe` 之后的有界推进步骤。它保持宿主 Hermes endpoint `127.0.0.1:18651` 不变，只在 `127.0.0.1:28651` 暴露 pilot 入口。流程选取一家真实公司，并严格执行以下业务操作：

```text
读取 <company>/company.json
  -> Hermes /v1/runs 与 SSE
  -> 触发一次终态 tool call
  -> 写入 <company>/analysis/.work/pilot-<12hex>/result.json
  -> 校验 source digest 与 result schema
  -> 删除 result.json 及其唯一 pilot 目录
```

宿主 `data/wiki` mount 为只读。被选公司 `analysis/` 目录是正式 mount 架构所需的唯一可写 bind，但 Landlock 会把任务写权限进一步收窄到全新的空 `pilot-*` 叶子目录。agent 不能写入 `company.json`、相邻 `.work` 路径、项目代码、prompt、workflow、OpenShell 控制状态或其他公司目录。宿主 deletion guard 会在整个 pilot 周期内快照并监控被选 `analysis/` 树。

## 能力范围

pilot 只使用宿主已经具备的能力：

- `siq-minimax-cn-pool`、`siq-stepfun`、`siq-kimi-coding` 和 `siq-tavily-search` OpenShell 服务提供方；
- 严格 `18792` 外联 broker 与 `18793` 只读数据 broker，两者使用独立、audience 绑定的 HMAC 请求身份；
- 正式候选镜像、无凭据 runtime snapshot、固定七个业务 mount、编译后的 task policy、已打补丁的 Landlock supervisor、loopback forward、API authentication、已验证 sandbox identity 和 deletion guard；
- 直接 Tavily provider 校验；只输出成功状态和结果数量，不输出 query response、URL、标题或摘要。

它不声明 Exa、本地 `8004/8006` 端口、Milvus 正式证明、fallback parity、报告质量或公开切流已经就绪。当前宿主 Clash Meta TUN 会把公网 DNS 映射到 `198.18.0.0/15`；通用 egress-guard 请求可能因此触发 SSRF 公网 IP 检查失败。这个兼容问题仍是显式 blocker，pilot 不会掩盖它。Tavily 通过 OpenShell provider route 测试。

## 隔离合同

- 显式启动确认：`--acknowledge-not-production-wide-pilot`；
- 状态根目录：`var/openshell/poc/siq-analysis-wide/`；
- sandbox：`siq-analysis-wide-pilot-<12hex>`；
- 生命周期标签：`siq-analysis-wide-pilot-not-production-v1`；
- 固定 endpoint：`127.0.0.1:28651`；
- 不创建、不修改正式 transaction 与 `var/openshell/siq-analysis/active.json`；
- 宿主 runtime 始终保持默认流量路径；
- secret 与原始日志只保存在被 Git 忽略的 owner-only `var/openshell/` 下；
- manifest 记录 `result_is_formal_evidence=false` 以及尚未解决的正式 blocker。

运行与失败恢复流程见 `docs/runbooks/openshell/siq-analysis-wide-pilot.md`。
