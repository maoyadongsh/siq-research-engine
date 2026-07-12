FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/apps/pdf-parser

RUN groupadd --system --gid 10001 siq \
    && useradd --system --uid 10001 --gid siq --home-dir /app --shell /usr/sbin/nologin siq

COPY apps/pdf-parser/requirements.txt apps/pdf-parser/constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt

COPY --chown=siq:siq apps/pdf-parser/ ./
COPY --chown=siq:siq packages/market-contracts/ /app/packages/market-contracts/
COPY --chown=siq:siq services/market-report-rules/src/ /app/services/market-report-rules/src/
COPY --chown=siq:siq scripts/hk/ /app/scripts/hk/
COPY --chown=siq:siq scripts/jp/ /app/scripts/jp/
COPY --chown=siq:siq scripts/kr/ /app/scripts/kr/
RUN pip install --no-cache-dir /app/packages/market-contracts
RUN mkdir -p /state/data/pdf-parser /state/var /state/artifacts \
    && chown -R siq:siq /app /state

ENV HOST=0.0.0.0 \
    PORT=15000 \
    SIQ_LOCAL_STATE_ROOT=/state \
    SIQ_DATA_ROOT=/state/data \
    SIQ_RUNTIME_ROOT=/state/var \
    SIQ_ARTIFACTS_ROOT=/state/artifacts \
    SIQ_PDF2MD_DATA_DIR=/state/data/pdf-parser

EXPOSE 15000

USER siq

CMD ["python", "app.py"]
