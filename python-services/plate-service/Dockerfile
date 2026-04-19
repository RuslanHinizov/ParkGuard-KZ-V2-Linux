# =============================================================================
# python-services/plate-service/Dockerfile
# -----------------------------------------------------------------------------
# Nomeroff-net 4.0.1 is pinned to torch 2.x + CUDA 12.x and supports Python
# 3.10 cleanly (3.12 breaks several transitive wheels). We therefore deviate
# from the rest of the fleet and base on python:3.10-slim with a CUDA runtime
# layered on top (spec §9.1).
#
# Nomeroff's detector + OCR weights are downloaded at build time (~1.3 GB)
# and baked into the image so a cold container starts without any external
# network roundtrip — see `download_nomeroff_models.sh` for the host-side
# equivalent.
# =============================================================================
FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Almaty

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3.10-venv python3-pip \
        build-essential git \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Use pinned Python 3.10 interpreter; python3 on 22.04 defaults to 3.10.
RUN ln -sf /usr/bin/python3.10 /usr/local/bin/python && \
    ln -sf /usr/bin/python3.10 /usr/local/bin/python3

WORKDIR /build
COPY python-services/shared /app/python-services/shared
COPY python-services/plate-service /app/python-services/plate-service

WORKDIR /app/python-services/plate-service
RUN pip install --upgrade pip && pip install '.[gpu]'

# Model cache warmup — spec §9.1: bake weights into the image so first
# inference doesn't hit the network. Nomeroff caches under ~/.nomeroff.
RUN mkdir -p /root/.nomeroff && \
    python -c "from nomeroff_net import pipeline; \
               p = pipeline('number_plate_detection_and_reading', image_loader='opencv'); \
               print('nomeroff models cached')" \
    || echo "WARN: model prefetch failed; will download on first request"

# -----------------------------------------------------------------------------
FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/python-services:/app/python-services/plate-service" \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Almaty

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 \
        libgl1 libglib2.0-0 \
        tini \
    && rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3.10 /usr/local/bin/python && \
    ln -sf /usr/bin/python3.10 /usr/local/bin/python3

RUN useradd --system --uid 10004 --home /app plate
COPY --from=builder /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /root/.nomeroff /home/plate/.nomeroff
RUN chown -R plate:plate /home/plate/.nomeroff
COPY python-services/shared /app/python-services/shared
COPY python-services/plate-service /app/python-services/plate-service

USER plate:plate
WORKDIR /app/python-services/plate-service

EXPOSE 9300
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "src.main"]
