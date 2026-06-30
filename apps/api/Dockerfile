FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app

COPY apps/api/pyproject.toml ./apps/api/pyproject.toml
COPY apps/api/uv.lock ./apps/api/uv.lock
COPY packages/market-contracts ./packages/market-contracts
RUN uv sync --frozen --no-dev --directory apps/api

COPY . .

ENV PATH="/app/apps/api/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/apps/api/.venv" \
    PYTHONUNBUFFERED=1 \
    SIQ_PROJECT_ROOT=/app \
    SIQ_BACKEND_ROOT=/app/apps/api

WORKDIR /app/apps/api

EXPOSE 18081

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "18081"]
