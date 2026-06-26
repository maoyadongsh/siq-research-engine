# Model Service Launch Scripts

Date: 2026-06-25

This folder consolidates model service launch scripts referenced by SIQ Research Engine / the copied SIQ stack.

It intentionally contains scripts and lightweight service definitions only. It does not contain model weights, Hugging Face caches, Conda environments, Docker images, or ComfyUI.

## Included Groups

| Group | Folder | Original Source | Purpose |
| --- | --- | --- | --- |
| MinerU | `mineru/` | `/home/maoyd/modles_setup` | PDF parsing upstream service scripts, now defaulting the SIQ PDF parser wrapper to `apps/pdf-parser` on `15000`. |
| Qwen3.6 35B | `qwen3-6-35b/` | `/home/maoyd/modles_setup` | Local OpenAI-compatible Qwen3.6 35B vLLM service scripts. |
| Qwen VL retrieval | `qwen-vl-retrieval/` | `/home/maoyd/modles_setup` | Qwen3-VL embedding and reranker service scripts. |
| Gemma4 26B | `gemma4-26b/` | `/home/maoyd/modles_setup` | Gemma4 26B A4B NVFP4 vLLM service launch script. |
| systemd user units | `systemd-user/` | `/home/maoyd/.config/systemd/user` | Existing user-level service definitions for MinerU and Qwen3.6. |

## Excluded By Design

- Model weights and snapshots, such as `/home/maoyd/hf_cache_new`
- Conda environments, such as `/home/maoyd/miniconda3/envs/*`
- ComfyUI application and model tree
- FunASR, LocateAnything, Qwen 27B, Qwen 35 9B, Claude proxy, and other unrelated launch scripts
- PID files, `__pycache__`, and vendored Python package overrides

## Current Files

```text
mineru/
  MinerU2.5-Pro-2604-1.2B_up.py
  start_pdf2md_services.sh

qwen3-6-35b/
  Qwen3.6-35B-A3B-FP8_vllm_up.py
  serve_qwen36_fp8_vllm.sh
  serve_qwen36_fp8_vllm_newenv.sh
  setup_qwen36_vllm_env.sh

qwen-vl-retrieval/
  Qwen3-VL-Embedding-2B_up.py
  Qwen3-VL-Reranker-2B_up.py

gemma4-26b/
  start_gemma4_26b_a4b_nvfp4_vllm.sh

systemd-user/
  mineru-api.service
  mineru-vllm.service
  qwen36-vllm.service
```

These scripts still contain host-specific model/runtime paths for weights, virtual environments, and system services. The SIQ PDF parser wrapper path has been normalized to `apps/pdf-parser`; remaining host paths should be treated as machine-local defaults and overridden with environment variables when running elsewhere.
