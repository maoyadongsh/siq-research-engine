# Nemotron 3 Nano Omni

SIQ uses the local OpenAI-compatible service at `http://127.0.0.1:8007/v1` with served model name `nemotron_3_nano_omni`.

The isolated vLLM runtime and downloaded weights stay outside the repository. This directory provides the project-facing management entrypoint:

```bash
infra/model-services/nemotron3/manage_nemotron3_vllm.sh status
infra/model-services/nemotron3/manage_nemotron3_vllm.sh restart
infra/model-services/nemotron3/manage_nemotron3_vllm.sh test
```

Override `NEMOTRON3_RUNTIME_SCRIPT` when the runtime script is installed at another path.
