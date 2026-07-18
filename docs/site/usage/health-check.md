# 健康检查

启动 SIQ Research Engine 后，可以通过以下健康检查端点快速验证各服务的存活与就绪状态。

## 健康端点列表

```bash
curl -s http://127.0.0.1:15173
curl -s http://127.0.0.1:18081/health
curl -s http://127.0.0.1:15000/api/ready
curl -s http://127.0.0.1:15010/api/ready
curl -s http://127.0.0.1:18000/health
curl -s http://127.0.0.1:18020/healthz
curl -s http://127.0.0.1:18642/health
curl -s http://127.0.0.1:18651/health
curl -s http://127.0.0.1:18649/health
curl -s http://127.0.0.1:18650/health
curl -s http://127.0.0.1:18652/health
python3 scripts/openshell/check_v06_completion.py --json
```

## 端点说明

| 端口 | 路径 | 对应服务 |
| --- | --- | --- |
| 15173 | `/` | Web 前端入口 |
| 18081 | `/health` | API 控制面 |
| 15000 | `/api/ready` | PDF 解析服务 |
| 15010 | `/api/ready` | 通用文档解析服务 |
| 18000 | `/health` | 市场报告 finder 服务 |
| 18020 | `/healthz` | 市场规则服务 |
| 18642 | `/health` | 模型服务（节点 1） |
| 18649 | `/health` | 模型服务（节点 2） |
| 18650 | `/health` | 模型服务（节点 3） |
| 18651 | `/health` | 模型服务（节点 4） |
| 18652 | `/health` | 模型服务（节点 5） |

## OpenShell 门禁检查

`python3 scripts/openshell/check_v06_completion.py --json` 用于检查 OpenShell v0.6 完成度门禁，输出包含 `decision` 字段，标识当前是否满足切流条件。

!!! warning
    `check_v06_completion.py` 当前真实门禁仍应显示 `decision=NO_GO`。不要把灰度链路存活误读成正式切流完成——服务可达仅代表灰度链路已建立，并不等价于 OpenShell 已通过正式发布门禁。