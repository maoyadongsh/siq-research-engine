# 投委会 Profile 向量检索合同

本数据集验证 Deal OS 现有 `vector_retrieval` 业务路径。每个 case 都要求逻辑 profile collection 能解析到版本化物理 Milvus collection，并用精确的 profile 与 project tag 检索受管方法论。

它属于一级市场投委会智能体集群。目标是验证 IC profiles 能召回方法论和背景知识，同时不把这些背景知识误认为项目 Evidence：背景知识可以指导分析方式，但不能验证 deal facts，也不能覆盖人工确认的项目材料。

nightly 性能基线会报告 Recall@K、MRR 和逐 case 延迟 P95。该探针对开发者本地运行是可选项；传入 `--require-ic-vector-retrieval-probe` 时会失败关闭。

本目录不包含项目事实、运行态检索命中、embedding、凭据或 Milvus dump。
