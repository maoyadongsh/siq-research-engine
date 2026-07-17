# OpenShell Egress Boundary Proof

该证明用于确认当前宿主 `siq-egress-guard` 的宽松门禁真实生效，不改变 Hermes、模型、
Prompt、工具、输出路径或默认 runtime。

## 前置条件

- `siq-openshell-dev` bridge 和两个 broker 健康；
- broker request identity 强制启用；
- egress health 的 resolver mode 为 `mihomo_fake_ip_verified`；
- identity key 仅存在于 ignored、owner-only 的项目运行目录。

运行：

```bash
python3 scripts/openshell/run_egress_boundary_proof.py --project-root "$PWD"
```

成功后生成：

- `artifacts/openshell/v0.6/egress-boundary.sanitized.json`
- `artifacts/openshell/v0.6/egress-boundary.sanitized.md`

输出只包含用例类别、决策、稳定规则、HTTP 状态、审计记录摘要和当前源码、Schema、
allowlist 摘要。目标 URL、请求/响应内容、短期身份、Key 和 raw audit 均不进入发布物。
发布前仍必须加入 `tracked-artifacts.json` 并运行 tracked-state 与 staged secret scan。

## 覆盖范围

真实覆盖：

- 公网 GET/HEAD 放行；
- 未知小 JSON POST 为 audit-only；
- 未知 multipart、octet-stream 和 PUT 拒绝；
- 云 metadata endpoint 拒绝；
- 每项决策写入无正文的结构化 audit。

不构成以下结论：

- 正式业务 sandbox 已通过；
- `scp`、`sftp`、`rsync`、`rclone` 等客户端已在正式 sandbox 实跑；
- 对合法小 JSON、搜索或模型请求中的内部语义实现了 DLP。

因此该证明可以作为 T6 的宿主真实边界证据，但不能单独解除正式 sandbox、A/B 或质量门。
