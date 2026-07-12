FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system --gid 10001 siq \
    && useradd --system --uid 10001 --gid siq --home-dir /app --shell /usr/sbin/nologin siq

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt

COPY --chown=siq:siq . .
RUN mkdir -p /state/data/document-parser /state/data/pdf-parser /state/var /state/artifacts \
    && chown -R siq:siq /app /state

ENV HOST=0.0.0.0 \
    PORT=15010 \
    SIQ_PROJECT_ROOT=/app \
    SIQ_LOCAL_STATE_ROOT=/state \
    SIQ_DATA_ROOT=/state/data \
    SIQ_RUNTIME_ROOT=/state/var \
    SIQ_ARTIFACTS_ROOT=/state/artifacts \
    SIQ_DOCUMENT_PARSE_DATA_DIR=/state/data/document-parser \
    SIQ_PDF2MD_DATA_DIR=/state/data/pdf-parser

EXPOSE 15010

USER siq

CMD ["gunicorn", "--bind", "0.0.0.0:15010", "--workers", "1", "--threads", "8", "--timeout", "300", "--graceful-timeout", "30", "--keep-alive", "5", "--worker-tmp-dir", "/tmp", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
