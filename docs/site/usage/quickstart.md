# 快速启动

本章节介绍 SIQ Research Engine 的快速启动方式，包含本地一键启动、可选启动模式、Docker Compose 部署以及前置依赖要求。

## 本地一键启动

```bash
cd /home/maoyd/siq-research-engine
cp infra/env/local.example infra/env/local.env
export SIQ_AUTH_SECRET_KEY="${SIQ_AUTH_SECRET_KEY:-$(openssl rand -hex 32)}"
export SIQ_SOURCE_TOKEN_SECRET="${SIQ_SOURCE_TOKEN_SECRET:-$(openssl rand -hex 32)}"
./start_all.sh
```

启动完成后，默认 Web 入口为：`http://127.0.0.1:15173`

## 可选启动模式

针对不同场景，可以通过环境变量跳过部分组件的启动：

- 不启动 Hermes：

```bash
SIQ_START_HERMES_GATEWAYS=0 ./start_all.sh
```

- 不启动 OpenShell：

```bash
SIQ_START_OPENSHELL_GATEWAY=0 SIQ_START_OPENSHELL_BROKERS=0 ./start_all.sh
```

## Docker Compose

如果希望通过 Docker Compose 一键拉起全部服务：

```bash
cd /home/maoyd/siq-research-engine
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.env up
```

按需启用 profile：

```bash
docker compose -f infra/docker/docker-compose.yml \
  --env-file infra/env/local.env \
  --profile external-services \
  --profile monitoring \
  up
```

## 前置要求

部署 SIQ Research Engine 前，请确认本地环境满足以下条件：

- 操作系统：Linux（推荐 Ubuntu 22.04 或同等发行版）
- Docker：已安装并可正常运行 `docker compose`
- Python：3.11 及以上版本
- Node.js：22 及以上版本
- uv：Python 包管理工具（用于统一虚拟环境与依赖管理）