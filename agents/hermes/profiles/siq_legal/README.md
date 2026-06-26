# siq_legal

SIQ 法务助手 Hermes profile。

## 当前检查结论

检查时间：2026-05-29。`http://127.0.0.1:8652/health` 返回正常。当前 profile 的 `state.db` 中有 53 个 sessions、672 条 messages。Milvus standalone 进程存在，默认 collection 为 `ic_legal_scanner`；主前端 `/legal` 通过聚合后端 `/api/legal/chat/*` 调用该 Agent，并展示 Wiki 中 `legal/*.html` 法律意见书。

法律意见书标准保存位置：

```text
/home/maoyd/wiki/companies/<company_id>/legal/
```

正式 HTML 意见书保存前必须通过：

```bash
python3 /home/maoyd/.hermes/profiles/siq_legal/scripts/validate_legal_opinion.py /path/to/opinion.html
```

### 决赛关注点

| 维度 | 本 Agent 贡献 |
| --- | --- |
| 创新性 | 把财报研究工作台扩展到上市公司合规/法务初筛，并要求法规依据来自本机 Milvus 检索而非模型记忆 |
| 技术难度 | hybrid_search 融合向量、关键词、条款号精确召回、相邻 chunk 和 reranker，并用质量门禁约束 HTML 意见书 |
| 完成度 | CLI、检索规则、意见书模板、校验脚本、保存脚本、前端法务页和 Hermes gateway 均已具备 |
| 商业价值 | 支持企业内合规问答、法规依据检索和法律意见书初稿生产，降低非诉合规研究成本 |

### 评委技术说明

`siq_legal` 是 SIQ 从财报研究扩展到合规初筛的专用 Agent。它与财务分析 Agent 的最大区别是：结论不能来自模型记忆，必须先检索本机法规库，并在输出中保留法规来源、chunk 和引用依据。

| 环节 | 技术实现 | 风险控制 |
| --- | --- | --- |
| 技术架构 | Hermes profile + Milvus 法规库 + hybrid_search CLI + HTML 意见书校验/保存 | 法规检索、适用分析和产物落盘分层 |
| 技术栈 | Python、Milvus、OpenAI-compatible embedding、reranker、Hermes Runs、HTML validator | 支持私有法规库和可审计输出 |
| 数据流 | 用户事实/问题 -> Milvus hybrid_search -> 法规依据筛选 -> 适用性分析 -> HTML 意见书 -> 校验保存 | 避免只凭模型记忆回答法律问题 |
| 法规存储 | Milvus `ic_legal_scanner` collection，来源为本地全量法律文档 | 法规库私有化、可追溯 |
| 检索召回 | `hybrid_search` 同时使用向量召回、关键词召回、条款号精确召回、相邻 chunk 补召回 | 减少只靠语义相似导致的错法条 |
| 融合排序 | RRF 融合、reranker、来源 profile、精确条款命中和错配来源降权 | 提高上市公司治理/信披问题的召回质量 |
| 法律意见生成 | HTML 意见书模板、事实适用分析、依据列表、不确定性说明 | 输出像法律意见书，而不是聊天回答 |
| 质量门禁 | `validate_legal_opinion.py` 检查结构、免责声明、引用和保存规范 | 防止无依据或格式不合规的意见书进入 Wiki |
| 保存与展示 | `save_opinion` 写入公司 Wiki `legal/`，前端 `/legal` 展示 | 与分析、核查、跟踪报告同一展示体系 |

该 Agent 的商业价值是把企业合规研究的第一轮检索、归纳和意见书草拟自动化，但不会替代正式律师意见。技术难度集中在检索质量和边界控制：它要能回答通用法律问题，也要优先识别上市公司信披、治理、关联交易、再融资、减持、处罚、内控等资本市场场景。

## 数据源

- Milvus: `127.0.0.1:19530`
- Attu: `http://127.0.0.1:3000`
- 默认 collection: `ic_legal_scanner`
- 来源文件根目录: `/home/maoyd/文档/全量法律`
- Milvus 法规向量库详细说明：`/home/maoyd/.hermes/profiles/siq_legal/milvus/README.md`

## 启动

```bash
hermes --profile siq_legal
```

API server 端口：

```text
127.0.0.1:8652
```

## CLI

```bash
./SIQ_legal status
./SIQ_legal collections
./SIQ_legal schema
./SIQ_legal sample --limit 3
./SIQ_legal search "公司法 独立董事 任期" --top-k 8
./SIQ_legal hybrid_search "上市公司关联交易披露要求" --top-k 8
./SIQ_legal hybrid_search "合同解除后违约金怎么处理" --top-k 8
./SIQ_legal benchmark --max-cases 4
./SIQ_legal save_opinion 000333-美的集团 /path/to/opinion.html
```

`search` / `hybrid_search` 需要 `.env` 中配置兼容 OpenAI embeddings 的 1024 维 embedding 服务。
`hybrid_search` 不扫描本地 md 文件，只在 Milvus 内做向量召回、来源过滤向量召回、JSON 关键词召回、条款号精确召回、相邻 chunk 补召回、RRF 融合和可选 reranker 重排。最终排序会融合 reranker 分数、法规来源 profile、精确条款命中和错配来源降权，避免上市公司信披/治理问题被泛化法规片段抢位。

Milvus collection schema、embedding/reranker 模型、1024 维向量、hybrid_search 流程和安全边界见：

```text
/home/maoyd/.hermes/profiles/siq_legal/milvus/README.md
```

关键词体系偏向上市公司法务合规评估（治理、信披、关联交易、再融资、减持、处罚、内控、数据合规等），但保留通用法律问题兜底：合同、劳动、侵权、婚姻家事、刑事、行政等问题会落到 `default` / `law` profile，并继续用原始问题做向量召回。

默认本地模型：

```text
LEGAL_EMBEDDING_API_URL=http://127.0.0.1:8013/v1
LEGAL_EMBEDDING_MODEL=Qwen3-VL-Embedding-2B
LEGAL_RERANKER_API_URL=http://127.0.0.1:8001/v1
LEGAL_RERANKER_MODEL=Qwen3-VL-Reranker-2B
```

## 法律意见书保存位置

正式法律意见书要求使用 HTML 格式，并保存到公司 Wiki 的 `legal/` 目录，例如：

```text
/home/maoyd/wiki/companies/000333-美的集团/legal/
```

推荐使用固化脚本保存：

```bash
./SIQ_legal save_opinion 000333-美的集团 /path/to/opinion.html
```

```text
LEGAL_EMBEDDING_API_URL=
LEGAL_EMBEDDING_MODEL=
LEGAL_EMBEDDING_API_KEY=
LEGAL_RERANKER_API_URL=
LEGAL_RERANKER_MODEL=
```
