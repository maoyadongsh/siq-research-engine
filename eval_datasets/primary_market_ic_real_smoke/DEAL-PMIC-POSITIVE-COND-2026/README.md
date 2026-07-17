# DEAL-PMIC-POSITIVE-COND-2026

仅用于合成评测。该 package 不包含真实公司、人员、客户、供应商、法律意见、市场研究、财务报表或投资交易。它只能用于隔离的一级市场 IC workflow 评测。

该 fixture 包含 40 个已验证项目 Evidence 项：business、finance、legal 和 risk 各 10 个。它用于支持一个真实的 R0-R4 正向/条件化支持 smoke，同时保留一个证据完整但重要的产能/估值张力。这里没有故意缺失的关键事实。最终 R4 approval 仍要求 workflow 的可信人工确认，绝不能从该 fixture 自动推断。

重新生成：

```bash
python eval_datasets/primary_market_ic_real_smoke/generate_evidence_complete_fixture.py
```

逐字节确定性校验：

```bash
python eval_datasets/primary_market_ic_real_smoke/generate_evidence_complete_fixture.py --check
```
