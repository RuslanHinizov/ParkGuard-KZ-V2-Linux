// =============================================================================
// cpp-deepstream/src/health_server.hpp
// -----------------------------------------------------------------------------
// Tiny HTTP server that exposes /healthz (JSON) and /metrics (Prometheus) on
// a fixed port (spec §7.5). Backed by cpp-httplib (header-only, vendored).
// The health reflection is read-only on the shared ProbeStats atoms — no
// locking, no impact on the hot path.
// =============================================================================
#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>

#include "detection_probe.hpp"

namespace parkguard {

class HealthServer {
public:
    HealthServer(std::string bind, int port, const DetectionProbe& probe);
    ~HealthServer();

    HealthServer(const HealthServer&)            = delete;
    HealthServer& operator=(const HealthServer&) = delete;

    void start();
    void stop();

private:
    void run();

    std::string           bind_;
    int                   port_;
    const DetectionProbe& probe_;
    std::thread           thread_;
    std::atomic<bool>     running_{false};
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace parkguard
