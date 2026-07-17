# syntax=docker/dockerfile:1

# OpenShell v0.0.83's ARM64 supervisor requires GLIBC_2.39 in practice.
FROM python@sha256:baf89808ec37adeaab83cec287adb4a2afa4a11c1d51e961c7ec737877e61af6

LABEL org.opencontainers.image.title="SIQ OpenShell Hermes minimal PoC"
LABEL org.opencontainers.image.revision="ddb8d8fa842283ef651a6e4514f8f561f736c72e"
LABEL ai.siq.hermes.patch-sha256="856d6e1820fe4f41669535a3e21c34a153e98318bcce90a607509c24d423d8c5"

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The OpenShell supervisor creates a network namespace and requires the trusted
# `ip` helper. The sandbox user is selected later by the OpenShell policy.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates iproute2 nftables \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/sh sandbox

WORKDIR /opt/hermes-agent
COPY hermes-agent/ /opt/hermes-agent/
RUN python -m pip install --no-cache-dir /opt/hermes-agent aiohttp==3.13.3 \
    && hermes --version \
    && find /opt/hermes-agent -type d -exec chmod 0555 {} + \
    && find /opt/hermes-agent -type f -exec chmod 0444 {} +

ARG SIQ_POC_FIXTURE_SHA256=unset
LABEL ai.siq.poc-fixture-sha256="$SIQ_POC_FIXTURE_SHA256"

COPY poc/ /opt/siq-poc/
RUN chmod 0555 /opt/siq-poc/entrypoint.sh /opt/siq-poc/model_stub.py \
    && chmod 0444 /opt/siq-poc/config.yaml \
    && mkdir -p /home/sandbox/.hermes /sandbox /workspace \
    && chown -R sandbox:sandbox /home/sandbox /sandbox /workspace

ENV HOME=/home/sandbox \
    HERMES_HOME=/home/sandbox/.hermes \
    API_SERVER_ENABLED=true \
    API_SERVER_HOST=127.0.0.1 \
    API_SERVER_PORT=28642 \
    API_SERVER_MODEL_NAME=siq-hermes-minimal-poc \
    NO_PROXY=127.0.0.1,localhost,::1 \
    no_proxy=127.0.0.1,localhost,::1

WORKDIR /workspace
EXPOSE 28642

# OpenShell replaces the image command with its supervisor. The lifecycle
# script invokes this entrypoint explicitly after policy activation.
CMD ["/opt/siq-poc/entrypoint.sh"]
