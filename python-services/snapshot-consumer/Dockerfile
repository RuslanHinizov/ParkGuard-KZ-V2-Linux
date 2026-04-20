# =============================================================================
# python-services/snapshot-consumer/Dockerfile
# -----------------------------------------------------------------------------
# Small Python-only image — no GPU, no ML deps. The only runtime
# requirement is access to the shared volume that the DeepStream container
# writes JPEGs into (`/shared/snapshots`), plus Kafka + MinIO over the LAN.
# =============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH="/app/python-services:/app/python-services/snapshot-consumer" \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Almaty

RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10005 --home /app snapshot

WORKDIR /app/python-services/snapshot-consumer
COPY python-services/shared            /app/python-services/shared
COPY python-services/snapshot-consumer /app/python-services/snapshot-consumer

RUN pip install --upgrade pip && pip install .

USER snapshot:snapshot

EXPOSE 9400
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "src.main"]
