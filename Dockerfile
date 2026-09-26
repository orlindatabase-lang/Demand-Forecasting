# Backend API image. Lives at the PROJECT ROOT, not inside api/, because
# `gcloud run deploy --source .` only auto-discovers a Dockerfile at the root
# of the given source directory - and this build needs the project-root CSVs
# (SKU_Master_Data_Final_Cleaned.csv, Indian_Festivals_Ecommerce_Sales_2025_2028.csv)
# alongside api/'s code, since design_attributes.py/festival_calendar.py both
# resolve them as Path(__file__).resolve().parent.parent / "<file>.csv" - one
# directory above api/ (2026-09-26, user-requested GCP deployment - found via
# a live Cloud Run log showing "/SKU_Master_Data_Final_Cleaned.csv not found").
FROM python:3.13-slim

WORKDIR /app

# libgomp1: OpenMP runtime needed by lightgbm/xgboost at import time.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY api/ api/
COPY SKU_Master_Data_Final_Cleaned.csv Indian_Festivals_Ecommerce_Sales_2025_2028.csv ./

WORKDIR /app/api

# Cloud Run injects $PORT (defaults to 8080) - uvicorn must bind 0.0.0.0 to
# exactly that port, unlike run.py's local-dev multi-socket setup.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
