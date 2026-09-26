FROM python:3.13-slim

WORKDIR /app

# libgomp1: OpenMP runtime needed by lightgbm/xgboost at import time.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects $PORT (defaults to 8080) - uvicorn must bind 0.0.0.0 to
# exactly that port, unlike run.py's local-dev multi-socket setup.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
