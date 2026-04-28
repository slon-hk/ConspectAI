# syntax=docker/dockerfile:1.6
#
# Project layout:
#   ff/
#     ├── backend/          ← Python source (main.py, rag.py, ...)
#     ├── conf/
#     │   ├── Dockerfile    (this file)
#     │   └── requirements.txt
#     └── static/

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Build
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps — PyMuPDF ships prebuilt wheels for Linux x86_64/aarch64
# so we don't need libmupdf-dev. Still need libpq-dev for asyncpg.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libffi-dev \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY conf/requirements.txt .
RUN pip install --upgrade pip wheel \
 && pip install --only-binary=:all: Pillow \
 && pip install -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Runtime libs: libpq5 for asyncpg, libjpeg for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libjpeg62-turbo \
        zlib1g \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 1000 orion

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# App code
COPY --chown=orion:orion backend/ /app/
# Static assets (error pages, etc.)
COPY --chown=orion:orion static/ /app/static/

# Persistent storage dirs
RUN mkdir -p /app/uploads/rag_images && chown -R orion:orion /app/uploads

USER orion

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/models > /dev/null || exit 1

CMD ["sh", "-c", "python -c 'import main' && exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}"]