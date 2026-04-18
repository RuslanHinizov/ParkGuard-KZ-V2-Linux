# =============================================================================
# python-services/violation-service/Dockerfile
# -----------------------------------------------------------------------------
# The single most critical Python service in the stack (spec §8).
# Layout mirrors event-api so PYTHONPATH reaches both 'shared' and 'src'.
# =============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY python-services/shared /app/python-services/shared
COPY python-services/violation-service /app/python-services/violation-service

WORKDIR /app/python-services/violation-service
RUN pip install --upgrade pip && pip install .

# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/python-services:/app/python-services/violation-service" \
    TZ=Asia/Almaty

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgeos-c1v5 \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10003 --home /app violation
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY python-services/shared /app/python-services/shared
COPY python-services/violation-service /app/python-services/violation-service

USER violation:violation
WORKDIR /app/python-services/violation-service

EXPOSE 9200
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "src.main"]
