FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app/services/market-report-rules

RUN groupadd --system --gid 10001 siq \
    && useradd --system --uid 10001 --gid siq --home-dir /app --shell /usr/sbin/nologin siq

COPY packages/market-contracts /app/packages/market-contracts
COPY services/market-report-rules/pyproject.toml services/market-report-rules/uv.lock services/market-report-rules/README.md ./
COPY services/market-report-rules/src ./src
RUN uv sync --frozen --no-dev
RUN mkdir -p /state/var /state/artifacts \
    && chown -R siq:siq /app /state

ENV PATH="/app/.venv/bin:$PATH" \
    SIQ_LOCAL_STATE_ROOT=/state \
    SIQ_DATA_ROOT=/state/data \
    SIQ_RUNTIME_ROOT=/state/var \
    SIQ_ARTIFACTS_ROOT=/state/artifacts \
    PYTHONUNBUFFERED=1

EXPOSE 8000

USER siq

CMD ["uvicorn", "market_report_rules_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
