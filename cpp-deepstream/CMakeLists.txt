# =============================================================================
# cpp-deepstream/CMakeLists.txt
# -----------------------------------------------------------------------------
# Minimum configuration for the single-binary DeepStream pipeline runner.
# Depends on:
#   - DeepStream 9.0 (nvcr.io/nvidia/deepstream:9.0-triton-multiarch)
#   - CUDA 12.x + TensorRT 10.14+ (provided by the base image)
#   - GStreamer 1.22 core + gst-plugins-bad / -nv
#   - librdkafka++ (apt: librdkafka-dev)
#   - yaml-cpp     (apt: libyaml-cpp-dev)
#   - nlohmann/json (apt: nlohmann-json3-dev OR vendored)
#   - cpp-httplib  (header-only, vendored under third_party/)
# =============================================================================

cmake_minimum_required(VERSION 3.22)
project(parkguard_deepstream
    VERSION 0.3.0
    DESCRIPTION "ParkGuard KZ DeepStream detection pipeline"
    LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(
    -Wall -Wextra -Wpedantic -Wshadow -Wconversion
    -Wnon-virtual-dtor -Wold-style-cast
    -fstack-protector-strong
)

# ---- DeepStream paths --------------------------------------------------------
set(DS_ROOT "/opt/nvidia/deepstream/deepstream"
    CACHE PATH "DeepStream SDK install prefix")

include_directories(
    ${DS_ROOT}/sources/includes
    ${DS_ROOT}/sources/includes/cvcore_headers
)
link_directories(
    ${DS_ROOT}/lib
)

# ---- pkg-config deps ---------------------------------------------------------
find_package(PkgConfig REQUIRED)
pkg_check_modules(GST REQUIRED
    gstreamer-1.0
    gstreamer-video-1.0
    gstreamer-app-1.0
    gstreamer-rtsp-1.0
    glib-2.0)

find_package(yaml-cpp REQUIRED)

# OpenCV — used for JPEG encode of the snapshot ring buffer and bbox crops.
# The DeepStream base image ships OpenCV 4.5+, so no special config is needed.
find_package(OpenCV REQUIRED COMPONENTS core imgcodecs imgproc)

# nlohmann/json: prefer package, fall back to vendored single-header.
find_package(nlohmann_json 3.10 QUIET)
if(NOT nlohmann_json_FOUND)
    message(STATUS "nlohmann_json not found system-wide; vendored header expected at third_party/json.hpp")
    include_directories(${CMAKE_SOURCE_DIR}/third_party)
endif()

# librdkafka C++ binding
find_library(RDKAFKA_LIB    rdkafka    REQUIRED)
find_library(RDKAFKA_CPP_LIB rdkafka++ REQUIRED)

# cpp-httplib header-only (vendored)
include_directories(${CMAKE_SOURCE_DIR}/third_party/cpp-httplib)

# ---- binary ------------------------------------------------------------------
set(SRC
    src/main.cpp
    src/config.cpp
    src/pipeline.cpp
    src/kafka_producer.cpp
    src/detection_probe.cpp
    src/health_server.cpp
    src/base64.cpp
    src/snapshot_tap.cpp
    src/snapshot_consumer.cpp
)

add_executable(parkguard_ds ${SRC})
target_include_directories(parkguard_ds PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${GST_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
)
target_link_libraries(parkguard_ds PRIVATE
    ${GST_LIBRARIES}
    yaml-cpp
    ${RDKAFKA_CPP_LIB}
    ${RDKAFKA_LIB}
    ${OpenCV_LIBS}
    nvdsgst_meta
    nvds_meta
    nvdsgst_helper
    nvds_utils
    nvbufsurface
    pthread
    dl
)
if(nlohmann_json_FOUND)
    target_link_libraries(parkguard_ds PRIVATE nlohmann_json::nlohmann_json)
endif()
target_compile_definitions(parkguard_ds PRIVATE
    PARKGUARD_VERSION="${PROJECT_VERSION}"
)

install(TARGETS parkguard_ds DESTINATION /opt/parkguard/bin)
