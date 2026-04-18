# =============================================================================
# event-api Dockerfile — FastAPI + Alembic (Python 3.12 slim)
# -----------------------------------------------------------------------------
# Multi-stage: builder installs deps, runner only copies the venv. Works for
# both ``uvicorn`` and ``alembic`` invocations.
# =============================================================================

# ---- Builder ----------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: psycopg2 requires libpq, shapely wants geos.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libgeos-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (layer caching) — copy only metadata files.
COPY python-services/event-api/pyproject.toml /app/python-services/event-api/pyproject.toml

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip wheel setuptools \
    && pip install --no-cache-dir \
       "fastapi>=0.115,<0.116" \
       "uvicorn[standard]>=0.32,<0.33" \
       "starlette>=0.41,<0.42" \
       "websockets>=13.1,<14" \
       "python-multipart>=0.0.12" \
       "slowapi>=0.1.9" \
       "pydantic>=2.9,<3" \
       "pydantic-settings>=2.6,<3" \
       "sqlalchemy[asyncio]>=2.0.36,<3" \
       "asyncpg>=0.30,<0.31" \
       "psycopg2-binary>=2.9.9" \
       "alembic>=1.13,<2" \
       "geoalchemy2>=0.15,<0.16" \
       "redis>=5.1,<6" \
       "aiokafka>=0.12,<0.13" \
       "minio>=7.2,<8" \
       "structlog>=24.4,<25" \
       "tenacity>=9.0,<10" \
       "shapely>=2.0,<3"

# ---- Runner -----------------------------------------------------------------
FROM python:3.12-slim AS runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/python-services:/app/python-services/event-api" \
    ALEMBIC_CONFIG="/app/migrations/alembic.ini"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libgeos-c1v5 \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --create-home --shell /bin/bash --uid 10001 app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# App code (python-services/shared + event-api + migrations)
COPY --chown=app:app python-services/shared          /app/python-services/shared
COPY --chown=app:app python-services/event-api       /app/python-services/event-api
COPY --chown=app:app migrations                      /app/migrations

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fs http://localhost:8000/healthz || exit 1

WORKDIR /app/python-services/event-api

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips=*"]
