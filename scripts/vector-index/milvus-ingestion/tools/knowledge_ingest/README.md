# SIQ 知识库入库系统

## 目录职责

`knowledge_ingest` 是面向 Milvus 的轻量知识库入库 UI 和异步引擎，适合快速把法规、公司资料、行业资料和内部知识文档切块并写入指定 collection。

它属于应用中心的轻量知识库工具，常用于法规库、行业库、一级市场项目背景库和 profile knowledge。对于 Hermes 记忆，Milvus 只承担语义索引职责；长期记忆的权威账本、scope、ACL、半衰期和按需全量召回由 API memory service 与 PostgreSQL 共同治理。

## 在系统中的位置

```text
法规 / 公司资料 / 行业资料 / 内部知识文档
  -> knowledge_ingest
     -> chunking / embedding / Milvus collection
     -> siq_legal / 研究知识库 / 调试检索
```

## 核心内容

| 能力 | 说明 |
| --- | --- |
| collection 管理 | 自动发现、新建、删除和选择 collection |
| 多格式入库 | PDF、DOCX、Markdown、TXT |
| embedding 选择 | 本地 vLLM embedding 或 DashScope |
| 参数化切块 | chunk size、条款感知切分和文件名前缀 |
| 断点续传 | 文件粒度的进度记录与恢复 |
| 检索测试 | 入库后即时检索验证 |
| 实时监控 | 文件数、向量数、失败数和日志 |

## 当前项目位置

该工具适合快速构建法规库、行业资料库和内部知识库。对于多市场财报 evidence package，优先使用上层 market evidence ingestion 脚本，以便保留 market、company、report、Wiki path 和 source evidence 等 metadata。

它的价值是低门槛入库和即时检索验证，但 Milvus 仍只是语义索引；权威事实应回到 Wiki package、PostgreSQL 或原始文档。

## 典型用法

```bash
cd /home/maoyd/siq-research-engine/scripts/vector-index/milvus-ingestion/tools/knowledge_ingest
unset ALL_PROXY all_proxy
python3 knowledge_ingest_ui.py
```

默认地址：

```text
http://localhost:7860
```

若有专用虚拟环境：

```bash
/path/to/.venv/bin/python knowledge_ingest_ui.py
```

## 关键边界或治理规则

- 这是知识库入库工具，不负责事实校验或研究结论生成。
- collection schema、向量维度和 metadata 结构必须与检索消费方保持一致。
- 对法规和合同类文档，应优先做条款边界切分，而不是粗暴按固定长度切块。
- API key 只通过环境变量注入，不出现在截图、README 示例或配置快照中。

## 维护建议

- 大批量入库前先用小目录验证 schema 和检索结果。
- 若用于 `siq_legal`，要优先保证条款定位和引用可读性。
- 如果引入新的 embedding 模型或切块策略，应同步记录适用场景和回退方案。
- 尽量让 UI 配置项和底层实际行为保持一致，避免“表单能选但引擎不支持”的状态。
