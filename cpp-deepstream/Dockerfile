# =============================================================================
# cpp-deepstream/Dockerfile
# -----------------------------------------------------------------------------
# Runs the parkguard_ds binary on a NVIDIA DeepStream 9.0 base (required for
# Blackwell / RTX 5070 Ti per spec §15.6). The TensorRT engines live on a
# bind-mounted volume (../cpp-deepstream/models) because engines must be
# built on the host GPU (scripts/build_tensorrt_engines.sh).
#
# Build:
#   docker build -t parkguard/deepstream:0.3 .
# Run (host networking, all GPUs):
#   docker run --rm --gpus all --network host \
#     -v $PWD/cpp-deepstream/models:/opt/parkguard/models:ro \
#     -v $PWD/cpp-deepstream/configs:/opt/parkguard/configs:ro \
#     -v $PWD/cpp-deepstream/configs/runtime.yml:/etc/parkguard/runtime.yml:ro \
#     parkguard/deepstream:0.3
# =============================================================================

ARG BASE_IMAGE=nvcr.io/nvidia/deepstream:9.0-triton-multiarch
FROM ${BASE_IMAGE} AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        pkg-config \
        libyaml-cpp-dev \
        librdkafka-dev \
        nlohmann-json3-dev \
        libgstreamer1.0-dev \
        libgstreamer-plugins-base1.0-dev \
        libgstreamer-plugins-bad1.0-dev \
        libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY cpp-deepstream/ /build/cpp-deepstream/
# Fetch vendored header-only deps at build time.
RUN mkdir -p /build/cpp-deepstream/third_party/cpp-httplib && \
    curl -fsSL -o /build/cpp-deepstream/third_party/cpp-httplib/httplib.h \
        https://raw.githubusercontent.com/yhirose/cpp-httplib/v0.18.0/httplib.h

RUN cmake -S /build/cpp-deepstream -B /build/out -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /build/out -j"$(nproc)"

# -----------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS runtime

ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility,video,graphics
ENV GST_DEBUG=2

RUN apt-get update && apt-get install -y --no-install-recommends \
        libyaml-cpp0.7 \
        librdkafka++1 \
        librdkafka1 \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/out/parkguard_ds /opt/parkguard/bin/parkguard_ds
COPY cpp-deepstream/configs/ /opt/parkguard/configs/

RUN useradd --system --uid 10002 --home /opt/parkguard parkguard && \
    mkdir -p /etc/parkguard /opt/parkguard/models && \
    chown -R parkguard:parkguard /opt/parkguard /etc/parkguard
USER parkguard:parkguard
WORKDIR /opt/parkguard

EXPOSE 9100
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/parkguard/bin/parkguard_ds", "--config", "/etc/parkguard/runtime.yml"]
