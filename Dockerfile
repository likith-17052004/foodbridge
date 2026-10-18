FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data/processed ./data/processed
COPY data/demo_photos ./data/demo_photos
COPY data/eval/vision_summary.json ./data/eval/vision_summary.json
COPY scripts/simulate_history.py ./scripts/simulate_history.py
# Cloud Run sets $PORT. SQLite in /tmp is restored from / snapshotted to Cloud Storage (app/services/persist.py).
ENV DB_PATH=/tmp/foodbridge.db UPLOAD_DIR=/tmp/uploads
CMD ["sh", "-c", "python -m app.bootstrap && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-graceful-shutdown 8"]
