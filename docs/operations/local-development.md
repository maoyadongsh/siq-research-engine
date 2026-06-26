# 本地开发操作说明

本文记录 SIQ Research Engine 当前推荐的本地开发启动方式。所有命令默认在本仓库执行：

```text
/home/maoyd/siq-research-engine
```

## 一键启动

```bash
cd /home/maoyd/siq-research-engine
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start_all.sh
```

该脚本会按 SIQ 默认路径启动：

- 公告搜索下载服务 `:18000`
- API 聚合后端 `:18081`
- PDF 解析服务 `:15000`
- Web 前端 `:15173`
- Hermes gateway `:18642`, `:18649`, `:18650`, `:18651`, `:18652`

MinerU、VLM、本地 LLM 等本机模型推理服务可以共享，例如 `:8002`、`:8003`、`:8004`、`:8006`。

打开：

```text
http://localhost:15173
```

## 手动启动

API：

```bash
cd /home/maoyd/siq-research-engine/apps/api
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start.sh
```

Web：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm install
npm run dev -- --host 0.0.0.0 --port 15173
```

PDF 解析服务：

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
./run.sh
```

公告搜索下载服务：

```bash
cd /home/maoyd/siq-research-engine/services/report-finder
uv run uvicorn report_finder_service.app:app --host 127.0.0.1 --port 18000
```

## 常用环境变量

| 变量 | 用途 |
| --- | --- |
| `SIQ_AUTH_SECRET_KEY` | API 鉴权密钥，开发环境也必须设置 |
| `SIQ_WIKI_ROOT` | Wiki 根目录，默认 `data/wiki` |
| `SIQ_PDF2MD_DATA_DIR` | PDF 解析运行态目录，默认 `data/pdf-parser` |
| `SIQ_REPORT_FINDER_ROOT` | 公告搜索服务目录，默认 `services/report-finder` |
| `SIQ_HERMES_HOME` | Hermes 运行态目录，默认 `data/hermes/home` |
| `SIQ_HERMES_PROFILES_ROOT` | Hermes profiles 目录，默认 `data/hermes/home/profiles` |
| `DATABASE_URL` | PostgreSQL 连接串 |

## 健康检查

```bash
curl -s http://localhost:15173
curl -s http://localhost:18081/health
curl -s http://localhost:15000/api/health
curl -s http://localhost:18000/health
```

Hermes gateway 按需单独启动后检查：

```bash
curl -s http://localhost:18642/health
curl -s http://localhost:18651/health
curl -s http://localhost:18649/health
curl -s http://localhost:18650/health
curl -s http://localhost:18652/health
```

## 开发验证

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests
```

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
```

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
```

```bash
cd /home/maoyd/siq-research-engine
bash -n start_all.sh
bash -n apps/api/start.sh
bash -n apps/pdf-parser/run.sh
```

## 注意事项

- 模型服务、MinerU API、VLM 和 vLLM 是主机相关服务，不由 `start_all.sh` 自动启动。
- `data/` 是运行态目录，不应整体纳入 Git。
- 新增路径配置时优先使用 `SIQ_*`，旧变量只作为兼容回退。
