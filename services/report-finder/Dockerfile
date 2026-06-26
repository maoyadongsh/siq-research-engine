FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY static ./static
COPY templates ./templates

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "report_finder_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
