# Nemotron 3 Nano Omni 模型服务

SIQ 使用本机 OpenAI 兼容服务 `http://127.0.0.1:8007/v1`，对外模型名为 `nemotron_3_nano_omni`。该模型服务主要用于会议转写、长上下文理解、多模态实验和与 NVIDIA 模型服务栈相关的本地能力验证。

隔离的 vLLM runtime 与已下载权重不进入仓库。本目录只提供项目侧管理入口：

```bash
infra/model-services/nemotron3/manage_nemotron3_vllm.sh status
infra/model-services/nemotron3/manage_nemotron3_vllm.sh restart
infra/model-services/nemotron3/manage_nemotron3_vllm.sh test
```

如果 runtime 脚本安装在其他路径，可通过 `NEMOTRON3_RUNTIME_SCRIPT` 覆盖默认位置。运维时应优先使用上述脚本，保证健康检查、重启和轻量测试口径一致。
