FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HOST=0.0.0.0 \
    PORT=15000 \
    SIQ_PDF2MD_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

EXPOSE 15000

CMD ["python", "app.py"]
