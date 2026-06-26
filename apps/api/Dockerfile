FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SIQ_BACKEND_ROOT=/app

EXPOSE 18081

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "18081"]
