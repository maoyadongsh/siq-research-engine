# 二级市场多市场验收证据

本目录只保存 CN、HK、US、EU、KR、JP 分析、核查和跟踪链路的脱敏发布门禁证据。

- `real-smoke.sanitized.json` 记录权威 identity 字段、adapter family/version、终态状态、artifact ID、内容 hash 和不可变事实面 hash；不包含报告正文、prompt、凭据或本地文件系统路径。
- `ui-analysis-mobile-375.png` 与 `ui-analysis-desktop-1440.png` 是 mocked-API UI 验收截图，用于检查控制顺序、文本适配和布局。

合成 golden 合同矩阵单独版本化在 `apps/api/tests/golden/secondary_market_multi_market_sidecars.json`。

从仓库根目录复现实 Wiki gate，并使用确定性的 parsed-ready 样本选择：

```bash
SIQ_MULTI_MARKET_RESEARCH_ENABLED=1 \
SIQ_US_SEC_ANALYSIS_ENABLED=1 \
uv run --project apps/api python \
  scripts/maintenance/run_secondary_market_multi_market_real_smoke.py
```

runner 将 CN 视为只读 legacy golden 回归，不会为它调用新 renderer。HK、US、EU、KR、JP 必须分别发布 exact-identity analysis、factcheck 和 tracking artifacts。门禁还要求所有受保护事实面 digest 保持不变。tracking 运行时关闭外部搜索和情绪分析，因此来源覆盖不可用会记录为 degraded，而不是模拟成功。
