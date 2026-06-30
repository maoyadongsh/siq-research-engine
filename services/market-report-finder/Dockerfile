FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    MARKET_REPORT_DOWNLOAD_DIR=/app/downloads \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "market_report_finder_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
