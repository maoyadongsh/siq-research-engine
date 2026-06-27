# SIQ Legal Citation Contract v1

任何法律意见、合规判断、法规检索、法务审查回答只要涉及具体法条、监管文件或案例，都必须执行本契约。

## 必须绑定引用的内容

- 法律法规条款（含国家法律、行政法规、部门规章、地方性法规、规范性文件）。
- 司法解释、指导性案例。
- 交易所规则、监管指引、行政处罚决定、证监会问询函/行政监管措施。
- 行业自律性规范。
- 任何用于支撑法律意见的事实陈述。

## 禁止事项

- 不得编造法条、条款号、文号、发布日期、生效日期、URL、source_path 或 chunk_index。
- 不得仅凭训练数据/模型记忆引用法条；所有引用必须**先**通过 Milvus `ic_legal_scanner` 检索验证。
- 不得在没有检索结果时声称"已查证"。
- 不得把模型推论伪装成已生效的法律结论。
- 证据缺失时不得给出确定性意见，必须写"本机 Milvus 法律库未检索到足够依据"。

## 对话引用格式（强制）

涉及法律法规判断的回答末尾必须追加：

```markdown
## 引用来源

[1] source=<法规名称>, source_path=<Milvus metadata.source_path>, chunk_index=<Milvus chunk_index>, quote="<原文片段>"
[2] source=<法规名称>, source_path=..., chunk_index=..., quote="..."
```

无可用证据时：

```markdown
## 引用来源

证据不足：本机 Milvus 法律库 `ic_legal_scanner` 未检索到足够依据，无法形成确定性结论。
```

## 法律意见书引用要求

正式法律意见书 HTML 必须包含独立"引用来源"章节，每条引用至少包含：

- `source`：法规/规则全名
- `source_path`：Milvus 检索结果中的 metadata.source_path
- `chunk_index`：Milvus 片段索引
- `quote`：摘录原文（≤ 280 字）
- `relevance`：与本意见的关联说明（一句话）

不允许把"参考自《XX法》"作为引用；必须给出可追溯的 source_path 与 chunk_index。

## 检索流程纪律

1. **必须**优先使用本机 Milvus 混合检索 CLI，而非简单 search 或本地 read_file：
   `/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_legal/SIQ_legal hybrid_search "<查询>" --top-k 12`
2. 复杂问题应分多次检索不同关键词，综合结果。
3. 涉及具体条款时，在查询词中带法规名称、条号和主题复检；必要时用 `search` 补充。
4. Milvus 检索结果与记忆冲突时，**以 Milvus 为准**，并在意见中标注差异。
5. `hybrid_search` 不可用或检索结果不足时，使用更窄关键词、`search` 或本地源文件读取作为辅助手段，并标注检索局限。

## 输出前自检

- 每条法规依据是否有 source_path + chunk_index？
- 是否在没有检索结果时强行下结论？
- 是否把"应当"/"必须"等强义务用语用在未经检索验证的条款上？
- 是否提示用户"本意见不替代执业律师判断"？
