# Market Modules

## 目录职责

`markets/` 保存 `services/market-report-rules` 的市场差异层。每个市场都以独立模块存在，负责描述本市场的 rule profile、storage profile、parser boundary 和可选的 market-specific extraction 逻辑。

这个目录存在的意义，是把“市场差异”留在市场模块里，而不是把所有特殊规则膨胀到共享层。

## 新增市场必须提供的文件

新增市场时，至少需要提供：

- `definition.py`：定义市场 profile、storage profile、UI / page metadata 和 parser boundary。
- `__init__.py`：导出该市场对外可见的公共 surface。

如果一个市场连这两个文件都没有，就还没有资格被注册进 `MARKET_MODULES`。

## 可选扩展文件

视市场复杂度，可选提供：

- `rules.py`：该市场的财务标签、概念或映射规则。
- `extractor.py`：market-specific extraction 实现。
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
