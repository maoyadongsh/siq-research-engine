# SIQ 可版本化数据集目录

`datasets/` 用于保存可以进入源码仓的稳定数据集、fixtures 和小型样本。这里不放运行态下载文件、大体积 PDF、数据库文件、解析缓存或一次性评测输出。

## 推荐子目录

| 路径 | 内容 |
| --- | --- |
| `datasets/eval` | 稳定评测集和 golden cases |
| `datasets/fixtures` | 单元测试和 contract tests 使用的小型 fixture |
| `datasets/samples` | 文档示例、最小样本、人工构造样例 |

## 收录规则

- 文件体积可控，优先使用 JSON、JSONL、CSV、Markdown 等文本格式。
- 样本必须能说明来源、用途、schema version 和更新方式。
- 含个人信息、密钥、用户上传原文或版权敏感大文件的内容不得放入此目录。
- 大型原始披露文件应保存在 `var/market-report-finder`、外部磁盘或对象存储，并通过 manifest 引用。
