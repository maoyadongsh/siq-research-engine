# test 目录说明

本目录目前是一个独立的 Python 占位包，不是当前前后端的完整测试体系。

当前文件：

```text
test/
  pyproject.toml
  uv.lock
  main.py
  README.md
```

`main.py` 只输出：

```text
Hello from test!
```

## 运行

```bash
cd /home/maoyd/finsight/test
uv run python main.py
```

## 当前测试分布

真实业务测试分散在其他项目/模块中：

| 位置 | 说明 |
| --- | --- |
| `/home/maoyd/report-finder-service/tests` | PDF 下载服务的单元测试和接口测试 |
| `/home/maoyd/finsight/pdf2md_web/tests` | PDF 解析服务的质量、路径和财务抽取相关测试 |
| `/home/maoyd/finsight/backend` | 当前没有独立 pytest 测试目录 |
| `/home/maoyd/finsight/finall_all_front_0516/front` | 当前以 TypeScript build 和 ESLint 为主 |

## 建议补齐方向

后续若要把本目录建设为主项目测试入口，建议按以下结构扩展：

```text
test/
  backend/
    test_chat_routes.py
    test_wiki_routes.py
    test_downloads_routes.py
    test_workflow_routes.py
  frontend/
    playwright/
      test_navigation.spec.ts
      test_agent_chat.spec.ts
  smoke/
    check_services.py
```

优先测试项：

1. 聚合后端 `/health`、`/api/wiki/companies/list`、`/api/system/status`。
2. 五个聊天前缀的 history/session/active 接口是否结构一致。
3. Vite 代理是否把 `/api/v1/*` 转发到 `8000`，把 `/pdfapi/*` 转发到 `5000`。
4. `/analysis`、`/verify`、`/tracking`、`/legal` 四个报告页是否能用统一 `ReportViewer` 加载 HTML。
5. 当前确认版 agent 头像资源是否存在。
