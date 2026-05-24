# douge_ai_agent/wiki 目录说明

本目录是项目内的历史归档、样例数据和服务快照目录，不是当前聚合后端默认读取的主 Wiki 根目录。

当前聚合后端默认 Wiki 根目录为：

```text
/home/maoyd/wiki
```

对应环境变量：

```bash
export WIKI_ROOT=/home/maoyd/wiki
```

## 1. 当前目录内容

```text
wiki/
  pdf2md_web/                   PDF 解析服务的一份项目内快照/副本
  pdf2md_web.tar.gz             PDF 解析服务归档包
  report-finder-service.tar.gz   PDF 下载服务归档包
  wiki.tar.gz                   Wiki 数据归档包
  tracking/                     早期持续跟踪样例数据
```

## 2. 与主 Wiki 的区别

| 路径 | 定位 | 当前使用情况 |
| --- | --- | --- |
| `/home/maoyd/wiki` | 主 Wiki 数据根目录 | 聚合后端 `routers/wiki.py` 默认读取这里 |
| `/home/maoyd/wiki/companies` | 主公司库 | 前端报告页、工作台、搜索框都依赖它 |
| `/home/maoyd/finsight/wiki` | 项目内归档/样例 | 不作为当前默认 `WIKI_ROOT` |
| `/home/maoyd/finsight/wiki/tracking` | 早期 tracking 样例 | `backend/agents/tracking` 默认相对路径可能读到它 |

## 3. pdf2md_web 快照

`wiki/pdf2md_web/` 是 PDF 解析服务的一份项目内快照，包含：

```text
app.py
run.sh
requirements.txt
templates/
static/
scripts/
tests/
tasks.db
README.md
```

当前主前端和聚合后端默认对接的 PDF 解析服务在项目外：

```text
/home/maoyd/pdf2md_web
```

如需运行当前解析服务，优先使用外部主目录：

```bash
cd /home/maoyd/pdf2md_web
./run.sh
```

项目内快照可用于对照、归档或回滚参考，但不要误把它当成当前线上解析服务。

## 4. tracking 样例

`wiki/tracking/` 保存早期持续跟踪样例，例如：

```text
wiki/tracking/000001-平安银行/
  tracking-items.md
  alerts/
  metrics/
  updates/
  sentiment/
```

`backend/routers/tracking.py` 中早期规则型 Tracking API 会按相对路径 `wiki/tracking` 查找数据。因此从项目根目录启动后端时，可能读取这里的样例；当前主 UI 的 `/tracking` 报告展示则主要读取主 Wiki：

```text
/home/maoyd/wiki/companies/<company_dir>/tracking/*.html
```

## 5. 归档包

| 文件 | 说明 |
| --- | --- |
| `pdf2md_web.tar.gz` | PDF 解析服务快照归档 |
| `report-finder-service.tar.gz` | PDF 下载服务快照归档 |
| `wiki.tar.gz` | Wiki 数据归档 |

归档包体积较大，通常不参与前后端运行。迁移项目前请先确认是否需要这些历史包。

## 6. 如果要切换 WIKI_ROOT

聚合后端启动时指定：

```bash
cd /home/maoyd/finsight/backend
WIKI_ROOT=/path/to/wiki uv run uvicorn main:app --reload --host 0.0.0.0 --port 10081
```

新的根目录应包含：

```text
companies/
  <company_dir>/
    company.json
    analysis/*.html
    factcheck/*.html
    tracking/*.html
    legal/*.html
```

前端设置页中的“Wiki 根目录提示”只是展示和团队约定，后端实际读取以环境变量 `WIKI_ROOT` 为准。

