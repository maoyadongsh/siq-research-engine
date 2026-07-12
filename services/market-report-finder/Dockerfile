FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app

RUN groupadd --system --gid 10001 siq \
    && useradd --system --uid 10001 --gid siq --home-dir /app --shell /usr/sbin/nologin siq

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
RUN mkdir -p /state/data/market-report-finder/downloads \
    && chown -R siq:siq /app /state

ENV PATH="/app/.venv/bin:$PATH" \
    SIQ_DEPLOYMENT_PROFILE=docker \
    MARKET_REPORT_DOWNLOAD_DIR=/state/data/market-report-finder/downloads \
    SIQ_LOCAL_STATE_ROOT=/state \
    SIQ_DATA_ROOT=/state/data \
    SIQ_RUNTIME_ROOT=/state/var \
    SIQ_ARTIFACTS_ROOT=/state/artifacts \
    PYTHONUNBUFFERED=1

EXPOSE 8000

USER siq

CMD ["uvicorn", "market_report_finder_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
