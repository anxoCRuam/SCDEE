# ============================================================
# SCDEE Backend — Dockerfile
# ============================================================
# Single image used for all Python services:
#   - API server (gunicorn / runserver)
#   - Celery worker
#   - Celery beat scheduler
#
# Build context: backend/
# ============================================================

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and buffering stdout/stderr.
# Unbuffered output ensures logs appear immediately in Docker.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System dependencies:
#   - build-essential, libpq-dev: psycopg (PostgreSQL).
#   - libzbar0: native library required by pyzbar to decode QR codes
#     (RF-9.3) and exercised by tests/integration of the instrumented
#     PDF pipeline.
#   - poppler-utils: provides pdftoppm/pdftocairo, needed by pdf2image
#     to rasterise the generated instrumented PDFs back to images for
#     QR decoding in the end-to-end test.
#   - tesseract-ocr: optional pytesseract OCR engine plugin (RF-9.9).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libzbar0 \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-spa \
        gettext \
        graphviz \
        libgraphviz-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security.
# Running as root inside containers is a security risk.
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home appuser

WORKDIR /app

# Install Python dependencies first (layer caching optimization).
# This layer is only rebuilt when requirements change, not on code changes.
COPY requirements/ requirements/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements/dev.txt

# Copy application code.
COPY . .

# Create directory for ingestion inbox (SFTP simulation in dev).
RUN mkdir -p /app/ingestion_inbox && chown appuser:appuser /app/ingestion_inbox

# Switch to non-root user.
USER appuser

# Default command: Django dev server.
# Overridden in docker-compose for celery worker/beat.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
