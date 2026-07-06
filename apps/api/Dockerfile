FROM ghcr.io/astral-sh/uv:python3.13-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN groupadd --system --gid 10001 siq \
    && useradd --system --uid 10001 --gid siq --home-dir /app --shell /usr/sbin/nologin siq

COPY apps/api/pyproject.toml ./apps/api/pyproject.toml
COPY apps/api/uv.lock ./apps/api/uv.lock
COPY packages/market-contracts ./packages/market-contracts
RUN uv sync --frozen --no-dev --directory apps/api

COPY --chown=siq:siq . .
RUN mkdir -p /state/data/wiki /state/data/downloads /state/data/backend /state/artifacts \
    && chown -R siq:siq /app /state

ENV PATH="/app/apps/api/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/apps/api/.venv" \
    SIQ_PROJECT_ROOT=/app \
    SIQ_BACKEND_ROOT=/app/apps/api \
    SIQ_LOCAL_STATE_ROOT=/state \
    SIQ_DATA_ROOT=/state/data \
    SIQ_RUNTIME_ROOT=/state/var \
    SIQ_ARTIFACTS_ROOT=/state/artifacts

WORKDIR /app/apps/api

EXPOSE 18081

USER siq

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "18081"]
