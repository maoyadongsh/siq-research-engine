FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HOST=0.0.0.0 \
    PORT=15010 \
    SIQ_PROJECT_ROOT=/app \
    SIQ_DATA_ROOT=/data \
    SIQ_DOCUMENT_PARSE_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

EXPOSE 15010

CMD ["python", "app.py"]
