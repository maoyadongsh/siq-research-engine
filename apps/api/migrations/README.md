# 应用数据库迁移

全新本地数据库会在 API 启动时由 `SQLModel.metadata` 创建。已有 PostgreSQL 数据库必须在启动新应用镜像前先执行编号前向迁移。

运行态所有权变更需要把 `006_create_runtime_coordination_tables.sql` 应用到 `SIQ_APP_DATABASE_URL`。该迁移是追加式、幂等的。请由部署迁移任务执行一次，再启动 API；如果运行协调表不完整，启动期 schema validation 会点名这个文件。

iOS 原生会议采集需要按编号顺序应用迁移 `004`、`005`、`007` 和 `008_add_meeting_native_capture_epoch_manifest_digest.sql`。迁移 007 冻结每个 batch 的 sample 坐标和 SHA-256 声明，使 rollover/seal 后到达的离线上传可以按已签名规范 manifest 校验，而不是信任客户端 digest 字符串。迁移 008 是追加式 PostgreSQL 升级，面向在 epoch-level digest 字段存在前已经运行过迁移 004 的安装；对全新 schema 来说它是 no-op。

不要通过 drop table 回滚这些迁移：表中包含 job、lease 和 quota 的审计状态。非生产回滚可以把 job 和 IC lease backend 都设回 `file`。生产环境保持失败关闭并要求 PostgreSQL；schema 缺陷必须通过新的前向迁移修正。
