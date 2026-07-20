# FunASR 模型服务脚本归档

本目录归档根 README 所引用的 FunASR 独立模型服务启动入口：

- `start_funasr_vllm.sh`：管理 `Fun-ASR-Nano-2512` vLLM/HTTP 服务，提供 start、stop、restart、status、logs、foreground 和 test。
- `serve_vllm.py`：启动脚本实际调用的 FunASR HTTP 服务入口快照；归档仅移除了上游文件的行尾空格。

机器级在用来源分别为：

```text
/home/maoyd/modles_setup/start_funasr_vllm.sh
/home/maoyd/services/FunASR/examples/industrial_data_pretraining/fun_asr_nano/serve_vllm.py
```

归档保留原脚本的默认绝对路径和环境变量覆盖能力，不复制模型权重、Conda 环境、FunASR 完整源码树、缓存、音频或运行日志。默认 `FUNASR_APP_DIR` 仍指向机器上的完整 FunASR checkout；本目录的 `serve_vllm.py` 用于审计和灾备参考，不替代它所依赖的同目录 `model.py`、`ctc.py` 和 `tools/`。

归档一致性从 `infra/model-services` 目录验证：

```bash
sha256sum -c launcher-sources.sha256
```
