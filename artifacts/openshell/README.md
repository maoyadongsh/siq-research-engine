# OpenShell 可提交证据

本目录保存可公开审查的 OpenShell 参赛证据和脱敏日志。除真实凭据值和 SIQ 私有业务正文外，OpenShell 相关资产默认可发布。OpenShell 的源码、policy、schema、Dockerfile、测试和 runbook 分别直接保存在 `infra/openshell/`、`scripts/openshell/` 和 `docs/runbooks/openshell/`，不需要先复制到这里。

`tracked-artifacts.json` 是本目录与 `var/openshell/manifests/` 可提交文件的唯一清单。每个条目绑定相对路径、分类、字节数和 SHA-256。文件名满足 `*.sanitized.json` 或 `*.sanitized.md` 只表示它有资格进入清单，不能绕过 manifest 或内容扫描。日志固定使用同目录成对的 `logs.sanitized.json` 和 `logs.sanitized.md`，不接受手写的自由格式 `.sanitized.log`。

允许提交：

- `baseline.json` / `.md` 和 `readiness.json` / `.md`；
- 不含凭据、用户内容或机器路径的 `*.sanitized.json` / `.md` 安全与质量证据；
- 经专用导出器脱密并通过扫描的日志、日志汇总和结构化审计摘要；
- 固化 registry、service preflight、Milvus boundary、host egress boundary、host memory write、provider-independent、observe 和 wide-pilot 的当前脱敏证明；
- 后续正式 filesystem boundary、A/B、删除守卫、回滚和正式审计汇总的脱敏证明。

禁止提交原件：

- API key、密码、token、cookie、Authorization header、DSN 和 TLS/SSH 私钥；
- Prompt、用户输入、对话消息、请求/响应正文、附件正文和内部文档内容；
- 未经脱敏的 gateway/broker/Hermes 日志、原始 audit JSONL 和完整 trace；
- session/response/gateway 数据库、PID、socket、lock、nonce 和机器绑定状态；
- sandbox filesystem、数据库备份、toolchain 二进制、镜像层和构建缓存。

发布流程：

```bash
python3 scripts/openshell/build_tracked_artifact_manifest.py \
  --project-root "$PWD" \
  --artifact public_document=artifacts/openshell/README.md \
  --artifact baseline=artifacts/openshell/v0.6/baseline.json

# 已有清单中的文件内容更新后，重新绑定摘要。
python3 scripts/openshell/build_tracked_artifact_manifest.py \
  --project-root "$PWD" --refresh

git add artifacts/openshell/tracked-artifacts.json <清单中的文件>
python3 scripts/openshell/check_tracked_state.py \
  --repo-root "$PWD" --require-allowlist --json
```

完整命令和日志导出规则见 `docs/runbooks/openshell/git-publication-policy.md`。
