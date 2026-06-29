# SIQ 法务合规 Agent

`siq_legal` 是 SIQ Research Engine 的法务合规 profile，对应 Web 工作台 `/legal` 页面和 API 后端 `/api/legal/*`。它面向上市公司治理、信息披露、关联交易、再融资、减持、处罚、内控和通用法律问题，提供基于本地法规库的检索、分析和法律意见书初稿。

## 定位

法务合规 Agent 的结论必须来自本机法规库检索结果和用户提供事实，不依赖模型记忆直接输出法条。它适合做合规初筛、法规依据整理和意见书草拟，不替代正式律师意见。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 法规检索 | 基于 Milvus collection 检索法规 chunk、条款和来源 |
| 混合召回 | 向量召回、关键词召回、条款号精确召回、相邻 chunk 补召回 |
| 融合排序 | RRF、reranker、来源 profile、精确条款命中和错配降权 |
| 场景识别 | 区分上市公司信披/治理场景和通用法律问题 |
| 意见书草拟 | 输出事实、问题、依据、分析、风险和免责声明 |
| 质量校验 | 保存前检查 HTML 结构、依据引用和输出边界 |

## 数据源

| 项目 | 默认值 |
| --- | --- |
| Milvus | `127.0.0.1:19530` |
| Attu | `http://127.0.0.1:3000` |
| 默认 collection | `ic_legal_scanner` |
| Embedding 服务 | `LEGAL_EMBEDDING_API_URL` |
| Reranker 服务 | `LEGAL_RERANKER_API_URL` |

## CLI 能力

```bash
./SIQ_legal status
./SIQ_legal collections
./SIQ_legal schema
./SIQ_legal sample --limit 3
./SIQ_legal search "公司法 独立董事 任期" --top-k 8
./SIQ_legal hybrid_search "上市公司关联交易披露要求" --top-k 8
./SIQ_legal benchmark --max-cases 4
./SIQ_legal save_opinion 000333-美的集团 /path/to/opinion.html
```

`hybrid_search` 不扫描本地 Markdown 文件，而是在 Milvus 内完成召回、融合、重排和来源过滤。

## 输出

法律意见书写入：

```text
companies/<company_id>/legal/
  <stock_code>-<short_name>-legal-<topic>.html
```

保存前应通过校验脚本：

```bash
python3 scripts/validate_legal_opinion.py /path/to/opinion.html
./SIQ_legal save_opinion <company_id> /path/to/opinion.html
```

## 输出边界

- 不凭模型记忆直接引用法规。
- 不把法规检索结果包装成正式法律意见。
- 不遗漏依据来源、条款、适用范围和不确定性。
- 不输出与用户事实无关的泛化法律结论。
- 不泄露 `.env` 中的模型、数据库或法规库访问凭据。
