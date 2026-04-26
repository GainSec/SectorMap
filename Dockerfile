FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=3737 \
    SECTORMAP_DATA_DIR=/app/data \
    SECTORMAP_STATIC_DIR=/app/app/static

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY README.md ./README.md
COPY docs ./docs

RUN mkdir -p /app/data

EXPOSE 3737
VOLUME ["/app/data"]

CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-3737}"]
