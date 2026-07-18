# 环境变量

SIQ Research Engine 通过环境变量统一管理各服务的运行态路径、外部依赖地址、鉴权密钥以及功能开关。下表汇总了常用环境变量及其默认值与用途。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| SIQ_PROJECT_ROOT | 仓库根目录 | 项目路径锚点 |
| SIQ_LOCAL_STATE_ROOT | 仓库根目录 | 本地状态根 |
| SIQ_DATA_ROOT | $SIQ_LOCAL_STATE_ROOT/data | 历史兼容运行态根 |
| SIQ_RUNTIME_ROOT | $SIQ_LOCAL_STATE_ROOT/var | 新增本地运行态推荐根 |
| SIQ_ARTIFACTS_ROOT | $SIQ_LOCAL_STATE_ROOT/artifacts | 生成产物目录 |
| SIQ_WIKI_ROOT | $SIQ_DATA_ROOT/wiki | LLM Wiki 事实层目录 |
| SIQ_REPORT_DOWNLOADS_ROOT | $SIQ_DATA_ROOT/market-report-finder/downloads | 官方披露下载目录 |
| SIQ_PDF2MD_API_BASE | http://127.0.0.1:15000 | PDF 解析服务地址 |
| SIQ_DOCUMENT_PARSER_API_BASE | http://127.0.0.1:15010 | 通用文档解析服务地址 |
| SIQ_REPORT_FINDER_BASE | http://127.0.0.1:18000 | 官方披露下载服务地址 |
| SIQ_MARKET_REPORT_RULES_BASE | http://127.0.0.1:18020 | 市场规则服务地址 |
| SIQ_HERMES_HOME | $SIQ_RUNTIME_ROOT/hermes/home | Hermes runtime home |
| SIQ_HERMES_RUNTIME | host | 默认仍为 Host；OpenShell 正式门禁通过前不自动切流 |
| SIQ_START_OPENSHELL_GATEWAY | 1 | 随主项目启动或复用 SIQ 专用 OpenShell gateway |
| SIQ_START_OPENSHELL_BROKERS | auto | reader secret 存在时启动/复用 brokers |
| SIQ_AUTH_SECRET_KEY | 无 | API 鉴权密钥，至少 32 字符 |
| SIQ_SOURCE_TOKEN_SECRET | 回退到 SIQ_AUTH_SECRET_KEY | source access token 签名密钥 |
| SIQ_AUTH_COOKIE_MODE | 0 | 启用 HttpOnly cookie 登录兼容模式 |
| SIQ_MEETINGS_ENABLED | 0 | 会议应用中心功能开关 |
| SIQ_AGENT_MEMORY_ENABLED | true | Agent memory 总开关 |
| SIQ_AGENT_MEMORY_MILVUS_COLLECTION | siq_agent_memory_active | Agent memory 语义索引 collection |

## 配置文件

仓库提供了样例环境变量文件 `infra/env/local.example`，其中包含上述变量的默认配置和说明。

推荐工作流：

1. 复制样例文件为本地配置：

```bash
cp infra/env/local.example infra/env/local.env
```

2. 按需修改 `infra/env/local.env` 中的变量值（如密钥、外部服务地址等）。

3. `start_all.sh` 与 `docker compose` 启动脚本会自动读取 `infra/env/local.env`，无需额外注入。

!!! tip
    `SIQ_AUTH_SECRET_KEY` 必须至少 32 字符，建议使用以下命令生成强随机密钥：

    ```bash
    openssl rand -hex 32
    ```

    若未显式设置 `SIQ_SOURCE_TOKEN_SECRET`，将回退使用 `SIQ_AUTH_SECRET_KEY`，生产环境建议两者分别配置。