# Third-Party Licenses and Notices

## Scope

SIQ Research Engine source code authored by `maoyadongsh` is licensed under the Apache License, Version 2.0, as stated in the root `LICENSE` file.

That project license does not relicense third-party software, model weights, datasets, container images, hosted APIs, or other external assets. Each external component remains subject to its own license or service terms. This document records the directly integrated components and the license boundaries relevant to the repository and its DGX Spark deployment. It is not a substitute for the full upstream license texts.

## Directly Integrated Software

| Component | Pinned or used version | Upstream license | How SIQ uses it | Required handling |
| --- | --- | --- | --- | --- |
| [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | `v0.0.83`, commit `e3d26dd3ae0dee247bbc5db368545832757ac493` | Apache-2.0 | Sandbox gateway, policy, filesystem/network/provider controls, BYOC integration, and locally maintained patches | Preserve Apache-2.0 terms and attribution; identify modified files. The pinned upstream revision has no separate `NOTICE` file. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | `0.13.0`, commit `ddb8d8fa842283ef651a6e4514f8f561f736c72e` | MIT | Agent runtime reconstructed into an ignored build context and modified by SIQ integration patches | Preserve the MIT copyright and permission notice in copies or substantial portions. The build process copies the upstream `LICENSE` into the runtime context. |
| [vLLM](https://github.com/vllm-project/vllm) | Nemotron image pinned to `0.20.0`; other launchers use the deployed image/version recorded by the operator | Apache-2.0 | Independent OpenAI-compatible generation, pooling, scoring, and ASR decoder services | Preserve upstream license and notices in redistributed images; verify the resolved image digest and included notices for a release. |
| [MinerU](https://github.com/opendatalab/MinerU) | Locally deployed package `3.1.2` | [MinerU Open Source License](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md): Apache-2.0 with additional terms | External document parsing API invoked by SIQ; MinerU source and environment are not vendored in this repository | Preserve the license and attribution. The current terms require prominent MinerU attribution for third-party online services and a separate commercial license above the stated MAU or monthly-revenue thresholds. Recheck the exact license shipped with the deployed package. |
| [FunASR](https://github.com/modelscope/FunASR) | Deployment-managed runtime; Python dependency constrained to `funasr>=1.3.10,<2.0` | MIT | Local speech recognition, VAD, punctuation, and speaker-processing runtime | Preserve the upstream MIT notice when redistributing the runtime or source. |
| [Milvus](https://github.com/milvus-io/milvus) / [PyMilvus](https://github.com/milvus-io/pymilvus) | Deployment-managed Milvus; `pymilvus>=2.4.0` | Apache-2.0 | Vector storage and retrieval for memory and selected retrieval paths | Preserve applicable Apache-2.0 licenses and notices in redistributed packages or images. |
| [PostgreSQL](https://www.postgresql.org/) | Deployment-managed local service | PostgreSQL License | Structured financial facts, task state, evidence metadata, and application persistence | Preserve PostgreSQL copyright and permission notices when redistributing PostgreSQL. |

SIQ also uses ordinary Python and JavaScript dependencies declared in `pyproject.toml`, `requirements.txt`, `package.json`, and lock files. Their package metadata remains authoritative for the exact resolved version and license. Before shipping a binary, container bundle, appliance image, or hosted distribution, generate a release-specific software bill of materials and third-party notice bundle from the resolved environment rather than relying only on this source-level summary.

## Models and Model Weights

Model weights are not included in this repository. Launcher scripts only record how separately obtained model artifacts are served. The root Apache-2.0 license does not grant rights to any model weights.

| Model or service | Repository/default identifier | Governing license or terms | SIQ usage boundary |
| --- | --- | --- | --- |
| NVIDIA Nemotron 3 Nano Omni | [`nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4`](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4) | [NVIDIA Open Model Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/) | Separately downloaded local multimodal model; its weights are not Apache-2.0 merely because the SIQ launcher is. |
| MinerU2.5-Pro | [`opendatalab/MinerU2.5-Pro-2604-1.2B`](https://huggingface.co/opendatalab/MinerU2.5-Pro-2604-1.2B) | Apache-2.0 according to the model card | Separately downloaded document parsing weights. The model license and the MinerU application/runtime license are distinct. |
| Qwen3-VL Embedding | [`Qwen/Qwen3-VL-Embedding-2B`](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B) | Apache-2.0 according to the model card | Separately downloaded multimodal embedding weights used by selected vector retrieval and memory paths. |
| Qwen3-VL Reranker | [`Qwen/Qwen3-VL-Reranker-2B`](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B) | Apache-2.0 according to the model card | Separately downloaded reranking weights used for eligible candidate sets; LLM-Wiki does not use this model. |
| Fun-ASR Nano | [`FunAudioLLM/Fun-ASR-Nano-2512`](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512) | Apache-2.0 according to the model card | Separately downloaded speech recognition weights. Auxiliary VAD, punctuation, and speaker models must be checked independently. |
| StepFun Step-3.7 Flash | Cloud provider model/API | StepFun account, API, and service terms | Remote provider access only. No StepFun model weights or service code are distributed by this repository. |

Model cards and provider terms can change independently of this repository. A deployment or public release must retain the exact downloaded model revision, source URL, acceptance record, and applicable license text in its own deployment manifest.

## Container Images and System Services

The repository references third-party base images and local services, including vLLM images, Python base images, PostgreSQL, Milvus, and other deployment-managed services. A script reference does not copy the image's license into the SIQ project license. Container redistribution must preserve every license and notice shipped in the selected image layers and must comply with the image registry's terms.

LLM-Wiki is SIQ's own structured knowledge organization and logical navigation layer. It does not embed or rerank its knowledge graph through Qwen3-VL Embedding or Qwen3-VL Reranker. Any storage engines or libraries used by a deployment remain separately licensed components.

## Data, Media, and Evaluation Materials

The Apache-2.0 source license does not automatically grant rights to annual reports, prospectuses, research documents, customer data, recordings, images, generated evaluation data, or other content processed by the system. Operators must establish a lawful basis for collection and processing, respect source-site terms, and obtain the necessary permissions for recordings and biometric or speaker-related processing.

Synthetic fixtures included for software testing remain covered by the repository license only where they are original SIQ materials and are explicitly marked as synthetic. Do not infer that third-party documents or externally sourced media are relicensed by their presence in a local runtime directory.

## Release Compliance Checklist

1. Record the Git commit, image digests, package lock files, model revisions, and model-card/license snapshots used by the release.
2. Generate an SBOM and a resolved dependency license report for Python, JavaScript, containers, and system packages.
3. Preserve all required copyright, license, attribution, and NOTICE files in source and binary distributions.
4. Mark modifications to Apache-2.0 components and retain the Hermes MIT notice in every runtime copy containing Hermes source.
5. Keep model weights, customer data, credentials, caches, logs, and private runtime state outside the source repository.
6. Review strong-copyleft or custom-license components separately before external network service operation or redistribution.
7. Recheck licenses whenever a model, image, package, or upstream commit changes.

For questions about whether a specific distribution satisfies a third-party license, obtain qualified legal review; this inventory documents engineering provenance and does not provide legal advice.
