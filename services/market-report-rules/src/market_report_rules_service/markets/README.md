# 市场模块

## 目录职责

`markets/` 保存 `services/market-report-rules` 的市场差异层。每个市场都以独立模块存在，负责描述本市场的 rule profile、storage profile、parser boundary 和可选的市场专属 extraction 逻辑。

这个目录存在的意义，是把“市场差异”留在市场模块里，而不是把所有特殊规则膨胀到共享层。

它是二级市场投研分析智能体集群全球化能力的规则插件层：官方源、parser 和模型可以替换，但市场会计语义、证据坐标、入库 schema 和 quality gate 必须在这里逐市场显式沉淀。一级市场在做公开可比公司研究时也会复用这些市场事实，但不把它们当成私有项目尽调结论。

## 当前市场矩阵

| 市场 | 主要职责 | 典型输出 |
| --- | --- | --- |
| `cn` | A 股 PDF / 财报结构兼容 | `pdf2md` schema、三表与勾稽结果 |
| `hk` | 港股年报 package 与 HK MVP 质量门禁 | `pdf2md_hk` schema、evidence coverage、statement coverage |
| `us` | SEC package / XBRL facts 规则 profile | `sec_us` schema、filing anchors、normalized metrics |
| `eu` | ESEF / IFRS package profile | `eu_ifrs` schema、IFRS metrics、ESEF evidence |
| `jp` | EDINET / PDF summary 混合 profile | `edinet_jp` schema、company Wiki path、JP-specific labels |
| `kr` | DART / KRX / PDF profile | `dart_kr` schema、K-IFRS metrics、DART evidence |

市场模块的商业意义是让 SIQ 能逐市场交付，而不是承诺一条“万能解析规则”覆盖所有披露制度。每个市场都有自己的存储边界、证据坐标和质量风险。

## 新增市场必须提供的文件

新增市场时，至少需要提供：

- `definition.py`：定义市场 profile、storage profile、UI / page metadata 和 parser boundary。
- `__init__.py`：导出该市场对外可见的公共 surface。

如果一个市场连这两个文件都没有，就还没有资格被注册进 `MARKET_MODULES`。

## 可选扩展文件

视市场复杂度，可选提供：

- `rules.py`：该市场的财务标签、概念或映射规则。
- `extractor.py`：市场专属 extraction 实现。
- `adapter.py`：与 legacy service、外部 parser 或兼容层的桥接逻辑。

是否需要这些文件，取决于市场差异是否已经大到不能由共享逻辑承接。

## 共享层必须保持轻薄的原因

共享层例如 `registry.py`、`storage.py`、`extraction.py` 和 `app.py` 应保持轻薄，原因很直接：

- 市场差异一旦进入共享层，就会迅速把共享代码变成难以维护的分支森林。
- 共享层越薄，越容易知道一个行为到底属于“全市场通用”还是“单市场例外”。
- 当新增市场时，团队应修改市场模块，而不是把所有新规则直接塞进顶层入口。

当前的设计原则是：

- `registry.py` 只负责读取和列出市场 profile。
- `storage.py` 只负责列出市场存储 profile。
- `extraction.py` 只负责分发到市场 extractor。
- `app.py` 只负责公开 API 和元数据。

## 新增市场时的注册与约束

新增市场时应遵守以下约束：

1. 先在 `markets/<code>/` 建立完整模块。
2. 再在 `markets/__init__.py` 注册 `MARKET_MODULES`。
3. 确认 `/markets`、`/profiles`、`/rules` 输出与新模块一致。
4. 不把业务规则直接散落到共享层文件。
5. README、测试和 module metadata 同步更新。

只有当市场模块自身能清晰说明职责、边界和存储语义时，它才算真正接入了 SIQ 的 rules 体系。

## 高精度验收清单

一个新市场只有同时回答以下问题，才算接入了 SIQ 事实生产线，而不是仅能解析样本：

- 公司、filing、报告期和 parse run 是否有稳定且不与其他市场冲突的身份。
- 年报/中报/摘要、合并/母公司、年度/YTD/QTD 是否能可靠区分。
- raw value、canonical value、币种、倍率、括号负数和 value polarity 是否分别保留。
- 主表总额、分部维度、附注明细和 XBRL extension concept 是否不会互相顶替。
- 资产负债、利润、现金流 bridge 能否在同一 source family 内选择输入，并输出 evidence refs。
- PDF page/table/bbox、HTML anchor 或 XBRL concept/context/unit 是否能从 normalized fact 回跳。
- warning/fail 如何影响 draft、review、canonical、retrieval、production 五类 promotion target。
- package、PostgreSQL view、Milvus metadata 和 Agent ResearchIdentity 是否使用同一市场代码与 stable ID。

只通过字段数量或“能出 JSON”的测试不足以放量；需要 package contract、质量门禁、入库回查和至少一个回答级引用/计算回归共同通过。
