# =============================================================================
# python-services/penalty-card-service/Dockerfile
# -----------------------------------------------------------------------------
# WeasyPrint requires Pango, Cairo, and associated font libs. The Debian
# slim base ships without them; we install via apt. No GPU needed.
# =============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH="/app/python-services:/app/python-services/penalty-card-service" \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Almaty

# WeasyPrint runtime deps: pango, cairo, gdk-pixbuf, libffi, fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10006 --home /app penalty

WORKDIR /app/python-services/penalty-card-service
COPY python-services/shared                 /app/python-services/shared
COPY python-services/penalty-card-service   /app/python-services/penalty-card-service

RUN pip install --upgrade pip && pip install .

USER penalty:penalty

EXPOSE 9500
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "src.main"]
