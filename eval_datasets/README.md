# SIQ 评测语料

## 目录定位

`eval_datasets/` 保存 SIQ 早期或历史延续下来的评测语料和回归样本集合。它更像“历史评测资产层”，而不是新的版本化 fixture 首选目录。

当前根 README 里的测试资产统计包含本目录中的历史回归样本。新样本优先进入 `datasets/`，但这里仍对 parser、agent memory、market ingestion 和 primary market IC 的历史兼容测试有价值。

## 主要内容

| 目录 / 文件 | 用途 |
| --- | --- |
| `siq_financial_analysis_eval_v1_*` | 财报分析评测语料 |
| `document_parser_cases/` | 通用文档解析回归样本 |
| `market_ingestion_cases/` | 多市场入库和 package 回归样本 |
| `upload_split/` | 拆分后的评测输入材料 |

## 当前最新状态

新的二级市场 MVP 样本已经迁向 `datasets/market_ingestion/`，本目录主要保留历史评测语料和旧回归集。后续新增小型、稳定、可版本化 case 时，优先进入 `datasets/`；本目录只在需要兼容旧 harness 或保留历史基线时继续使用。

## 与其他数据目录的边界

- `eval_datasets/`：历史评测语料和回归集。
- `datasets/`：新增的稳定、可版本化样本首选目录。
- `artifacts/`：单次评测运行输出，不应回写到这里。

因此，新的小型评测样本优先放 `datasets/`，而不是继续让 `eval_datasets/` 膨胀。

## 可提交与不可提交内容

可提交：

- 经过整理的历史评测基线与回归样本
- 与评测字段强绑定的 CSV / JSONL / Markdown 语料

不可提交：

- 单次跑分输出
- 临时 HTML 报告
- 本地调试日志和导出文件

## 运行或使用建议

- 运行完整评测前，先做小样本联通测试，确认鉴权、字段映射和响应解析正常。
- 对涉及数字和事实的问题，人工复核应同时看事实正确性、证据完整性和口径一致性。
- 新评测结果若要长期保留，应进入 `artifacts/eval-runs/`；只有被整理成稳定基线后再考虑迁入 `datasets/`。

## 维护原则

- 把这里视为“历史资产库”，而不是新的默认落点。
- 样本应保留基本上下文说明，避免多年后只剩文件而无人知道用途。
- 新旧评测目录边界在 README 中始终说清楚。
