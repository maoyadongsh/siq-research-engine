# scripts 目录说明

`scripts/` 保存 SIQ 迁移后仍可复用的脚本入口。这里放按职责分组的薄脚本，不放应用源码、运行态数据或历史生成资产。

当前分组：

| 路径 | 职责 |
| --- | --- |
| `scripts/ops` | 本地运维辅助，例如备份和健康检查 |
| `scripts/maintenance` | 数据集生成、离线维护等可重复任务 |
| `scripts/migration` | 迁移期一次性或低频转换脚本 |

应用启动入口在仓库根目录 `start_all.sh` 或各服务目录：

- `apps/api/start.sh`
- `apps/pdf-parser/run.sh`
- `apps/web`

历史头像生成、旧样例和不再维护的脚本不再保留在当前仓库。需要恢复时先从外部备份或旧项目只读对照中确认用途，再提升到对应分组并适配 SIQ 路径和环境变量。
