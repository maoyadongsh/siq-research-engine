# syntax=docker/dockerfile:1

# OpenShell v0.0.83's ARM64 supervisor requires GLIBC_2.39 in practice.
FROM python@sha256:baf89808ec37adeaab83cec287adb4a2afa4a11c1d51e961c7ec737877e61af6

ARG SIQ_HERMES_COMMIT
ARG SIQ_HERMES_PATCH_SHA256
ARG SIQ_SANDBOX_UID=1000
ARG SIQ_SANDBOX_GID=1000

LABEL org.opencontainers.image.title="SIQ OpenShell Hermes siq_analysis"
LABEL org.opencontainers.image.revision="$SIQ_HERMES_COMMIT"
LABEL ai.siq.hermes.patch-sha256="$SIQ_HERMES_PATCH_SHA256"

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPYCACHEPREFIX=/tmp/siq-pycache \
    SIQ_PROJECT_ROOT=/home/maoyd/siq-research-engine \
    HERMES_HOME=/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis \
    HERMES_RUNTIME_HOME=/sandbox/siq-analysis-runtime-state \
    HERMES_AUTH_FILE=/sandbox/runtime-auth/auth.json \
    API_SERVER_ENABLED=true \
    API_SERVER_HOST=127.0.0.1 \
    API_SERVER_PORT=28651 \
    API_SERVER_MODEL_NAME=siq_analysis \
    NO_PROXY=127.0.0.1,localhost,::1 \
    no_proxy=127.0.0.1,localhost,::1 \
    PATH=/opt/siq/hermes/venv/bin:/usr/local/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Supervisor networking needs iproute2/nftables. Fonts preserve Chinese report
# rendering, while curl/git are part of the reviewed terminal capability set.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl fontconfig fonts-noto-cjk git iproute2 nftables xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "$SIQ_SANDBOX_GID" sandbox \
    && useradd --uid "$SIQ_SANDBOX_UID" --gid "$SIQ_SANDBOX_GID" --create-home --shell /bin/sh sandbox

COPY node-v20.20.2-linux-arm64.tar.xz /tmp/node.tar.xz
RUN printf '%s  %s\n' \
        '73093db209e4e9e09dd7d15a47aeaab1b74833830df03efa5f942a1122c5fa71' \
        '/tmp/node.tar.xz' \
        | sha256sum -c - \
    && mkdir -p /usr/local/node \
    && tar -xJf /tmp/node.tar.xz --strip-components=1 -C /usr/local/node \
    && rm -f /tmp/node.tar.xz \
    && node --version | grep -Fx 'v20.20.2'

WORKDIR /opt/hermes-agent
COPY hermes-agent/ /opt/hermes-agent/
COPY requirements-siq-analysis.txt /opt/siq/requirements-siq-analysis.txt
RUN python -m pip install --no-cache-dir uv==0.11.7 \
    && python -m venv /opt/siq/hermes/venv \
    && UV_PROJECT_ENVIRONMENT=/opt/siq/hermes/venv uv sync --frozen --no-dev \
    && /opt/siq/hermes/venv/bin/python -m pip install --no-cache-dir \
        --requirement /opt/siq/requirements-siq-analysis.txt \
    && HERMES_HOME=/tmp/hermes-build /opt/siq/hermes/venv/bin/hermes --version \
    && rm -rf /tmp/hermes-build \
    && /opt/siq/hermes/venv/bin/python -c \
        'import aiohttp, exa_py, jsonschema, psycopg2; print("siq_analysis dependencies: ok")'

ARG SIQ_CONTEXT_SHA256
ARG SIQ_RUNTIME_CONFIG_SHA256
ARG SIQ_HERMES_INTEGRATION_PATCH_SHA256
LABEL ai.siq.openshell.context-sha256="$SIQ_CONTEXT_SHA256"
LABEL ai.siq.openshell.runtime-config-sha256="$SIQ_RUNTIME_CONFIG_SHA256"
LABEL ai.siq.hermes.integration-patch-sha256="$SIQ_HERMES_INTEGRATION_PATCH_SHA256"

COPY project/ /home/maoyd/siq-research-engine/
COPY entrypoint.sh observe-entrypoint.sh healthcheck.py validate_placeholder_auth.py validate_provider_placeholders.py \
    runtime_state_lifecycle_smoke.py probe_milvus_sandbox_boundary.py /opt/siq/
COPY minimax-cn-auth-pool.template.json /opt/siq/minimax-cn-auth-pool.template.json
COPY openshell-client/ /opt/siq/openshell-client/
COPY siq-fetch /usr/local/bin/siq-fetch

RUN chmod 0555 /opt/siq/entrypoint.sh /opt/siq/observe-entrypoint.sh /opt/siq/healthcheck.py \
        /opt/siq/validate_placeholder_auth.py \
        /opt/siq/validate_provider_placeholders.py /opt/siq/runtime_state_lifecycle_smoke.py \
        /opt/siq/probe_milvus_sandbox_boundary.py /usr/local/bin/siq-fetch \
    && chmod 0444 /opt/siq/minimax-cn-auth-pool.template.json \
    && find /opt/siq/openshell-client -type d -exec chmod 0555 {} + \
    && find /opt/siq/openshell-client -type f -exec chmod 0444 {} + \
    && find /opt/hermes-agent -type d -exec chmod 0555 {} + \
    && find /opt/hermes-agent -type f -exec chmod 0444 {} + \
    && find /home/maoyd/siq-research-engine -type d -exec chmod 0555 {} + \
    && find /home/maoyd/siq-research-engine -type f -exec chmod 0444 {} + \
    && install -d -o sandbox -g sandbox -m 0700 \
        "$HERMES_HOME" \
        "$HERMES_HOME/cache" \
        "$HERMES_HOME/checkpoints" \
        "$HERMES_HOME/cron" \
        "$HERMES_HOME/logs" \
        "$HERMES_HOME/memories" \
        "$HERMES_HOME/sessions" \
        "$HERMES_HOME/workspace" \
        /sandbox /sandbox/siq-analysis-runtime-state /workspace \
    && test ! -e "$HERMES_HOME/auth.json" \
    && test ! -e "$HERMES_HOME/.env"

# These transfer clients are present only so the formal sandbox proof can
# execute their real binaries against the deny-by-default network policy.
RUN cp -a /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.original \
    && sha256sum /etc/apt/sources.list.d/debian.sources > /tmp/debian.sources.sha256 \
    && sed -i \
        -e 's|URIs: http://deb.debian.org/debian-security|URIs: https://mirrors.aliyun.com/debian-security|' \
        -e 's|URIs: http://deb.debian.org/debian|URIs: https://mirrors.aliyun.com/debian|' \
        /etc/apt/sources.list.d/debian.sources \
    && grep -Fxq 'URIs: https://mirrors.aliyun.com/debian' /etc/apt/sources.list.d/debian.sources \
    && grep -Fxq 'URIs: https://mirrors.aliyun.com/debian-security' /etc/apt/sources.list.d/debian.sources \
    && ! grep -Fq 'URIs: http://deb.debian.org/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends openssh-client rclone rsync \
    && mv -fT /tmp/debian.sources.original /etc/apt/sources.list.d/debian.sources \
    && sha256sum --check /tmp/debian.sources.sha256 \
    && rm -f /tmp/debian.sources.sha256 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /home/maoyd/siq-research-engine
EXPOSE 28651
# Direct docker image smoke probes authenticated loopback HTTP. Under OpenShell,
# Docker runs this command outside the sandbox network namespace, so the helper
# instead verifies the exact supervisor and Hermes process identities.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD ["/opt/siq/hermes/venv/bin/python", "/opt/siq/healthcheck.py"]

# OpenShell replaces CMD with its supervisor and invokes this explicitly after
# applying Landlock and network policy.
USER sandbox:sandbox
CMD ["/opt/siq/entrypoint.sh"]
