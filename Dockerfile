# FinEventAgent production image
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    RUN_MODE=hybrid \
    PORT=8000

COPY demo/requirements.txt /app/demo/requirements.txt
RUN pip install --no-cache-dir -r /app/demo/requirements.txt

COPY . /app

# Drop local secrets if any were copied by mistake
RUN rm -f /app/.env || true

EXPOSE 8000

CMD ["sh", "-c", "uvicorn demo.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
