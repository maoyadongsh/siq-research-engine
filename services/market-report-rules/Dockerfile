FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app/services/market-report-rules

COPY packages/market-contracts /app/packages/market-contracts
COPY services/market-report-rules/pyproject.toml services/market-report-rules/uv.lock services/market-report-rules/README.md ./
COPY services/market-report-rules/src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "market_report_rules_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
