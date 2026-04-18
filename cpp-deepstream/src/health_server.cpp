// =============================================================================
// cpp-deepstream/src/health_server.cpp
// =============================================================================
#include "health_server.hpp"

#include <httplib.h>

#if __has_include(<nlohmann/json.hpp>)
#include <nlohmann/json.hpp>
#else
#include "json.hpp"
#endif

#include <chrono>
#include <sstream>
#include <utility>

namespace parkguard {

struct HealthServer::Impl {
    httplib::Server svr;
};

HealthServer::HealthServer(std::string bind, int port, const DetectionProbe& probe)
    : bind_(std::move(bind)), port_(port), probe_(probe), impl_(std::make_unique<Impl>()) {}

HealthServer::~HealthServer() {
    stop();
}

void HealthServer::start() {
    if (running_.exchange(true)) return;
    thread_ = std::thread([this] { this->run(); });
}

void HealthServer::stop() {
    if (!running_.exchange(false)) return;
    if (impl_) impl_->svr.stop();
    if (thread_.joinable()) thread_.join();
}

void HealthServer::run() {
    auto& svr = impl_->svr;

    svr.Get("/healthz", [this](const httplib::Request&, httplib::Response& res) {
        const auto& idx = probe_.index();
        const std::uint64_t wall_ms = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count());

        nlohmann::json out;
        out["status"]  = "ok";
        out["service"] = "cpp-deepstream";
        out["version"] = PARKGUARD_VERSION;
        out["cameras"] = nlohmann::json::array();
        for (std::size_t i = 0; i < idx.id_by_pad.size(); ++i) {
            const auto& s = *idx.stats[i];
            std::uint64_t last = s.last_frame_unix_ms.load();
            nlohmann::json cam;
            cam["id"]                      = idx.id_by_pad[i];
            cam["frames"]                  = s.frames.load();
            cam["detections"]              = s.detections.load();
            cam["bytes_sent"]              = s.bytes_sent.load();
            cam["produce_fails"]           = s.produce_fails.load();
            cam["last_frame_age_ms"]       = last == 0 ? -1 : static_cast<std::int64_t>(wall_ms - last);
            out["cameras"].push_back(std::move(cam));
        }
        res.set_content(out.dump(), "application/json");
    });

    svr.Get("/metrics", [this](const httplib::Request&, httplib::Response& res) {
        const auto& idx = probe_.index();
        std::ostringstream o;
        o << "# HELP parkguard_ds_frames_total Frames processed per camera.\n";
        o << "# TYPE parkguard_ds_frames_total counter\n";
        for (std::size_t i = 0; i < idx.id_by_pad.size(); ++i) {
            const auto& s = *idx.stats[i];
            o << "parkguard_ds_frames_total{camera=\"" << idx.id_by_pad[i] << "\"} "
              << s.frames.load() << "\n";
        }
        o << "# HELP parkguard_ds_detections_total Detections emitted to Kafka.\n";
        o << "# TYPE parkguard_ds_detections_total counter\n";
        for (std::size_t i = 0; i < idx.id_by_pad.size(); ++i) {
            const auto& s = *idx.stats[i];
            o << "parkguard_ds_detections_total{camera=\"" << idx.id_by_pad[i] << "\"} "
              << s.detections.load() << "\n";
        }
        o << "# HELP parkguard_ds_produce_fails_total Kafka produce failures.\n";
        o << "# TYPE parkguard_ds_produce_fails_total counter\n";
        for (std::size_t i = 0; i < idx.id_by_pad.size(); ++i) {
            const auto& s = *idx.stats[i];
            o << "parkguard_ds_produce_fails_total{camera=\"" << idx.id_by_pad[i] << "\"} "
              << s.produce_fails.load() << "\n";
        }
        res.set_content(o.str(), "text/plain; version=0.0.4");
    });

    svr.listen(bind_.c_str(), port_);
}

}  // namespace parkguard
