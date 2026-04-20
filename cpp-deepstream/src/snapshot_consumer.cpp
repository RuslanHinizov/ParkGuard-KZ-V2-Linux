// =============================================================================
// cpp-deepstream/src/snapshot_consumer.cpp
// =============================================================================
#include "snapshot_consumer.hpp"

#include "kafka_producer.hpp"
#include "snapshot_tap.hpp"

#if __has_include(<nlohmann/json.hpp>)
#include <nlohmann/json.hpp>
#else
#include "json.hpp"
#endif

#if __has_include(<opencv2/imgcodecs.hpp>)
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#define PARKGUARD_CONSUMER_HAVE_OPENCV 1
#else
#define PARKGUARD_CONSUMER_HAVE_OPENCV 0
#endif

#include <chrono>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace parkguard {

namespace {

std::string iso_ms(std::uint64_t wall_ms) {
    const auto secs = static_cast<std::time_t>(wall_ms / 1000);
    std::tm tm{};
#ifdef _WIN32
    gmtime_s(&tm, &secs);
#else
    gmtime_r(&secs, &tm);
#endif
    std::ostringstream os;
    os << std::put_time(&tm, "%Y-%m-%dT%H:%M:%S");
    os << '.' << std::setw(3) << std::setfill('0') << (wall_ms % 1000) << 'Z';
    return os.str();
}

}  // namespace


SnapshotConsumer::SnapshotConsumer(SnapshotConsumerCfg            cfg,
                                    std::shared_ptr<KafkaProducer> producer,
                                    const SnapshotTap&             tap)
    : cfg_(std::move(cfg)),
      producer_(std::move(producer)),
      tap_(tap) {}

SnapshotConsumer::~SnapshotConsumer() {
    stop();
}

void SnapshotConsumer::start() {
    if (!cfg_.enabled) return;
    if (running_.exchange(true)) return;

    std::string errstr;
    std::unique_ptr<RdKafka::Conf> conf(RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL));
    conf->set("bootstrap.servers", cfg_.brokers,       errstr);
    conf->set("group.id",          cfg_.group_id,      errstr);
    conf->set("enable.auto.commit","false",             errstr);
    conf->set("auto.offset.reset", "latest",            errstr);
    conf->set("session.timeout.ms","30000",             errstr);

    consumer_.reset(RdKafka::KafkaConsumer::create(conf.get(), errstr));
    if (!consumer_) {
        running_ = false;
        throw std::runtime_error("KafkaConsumer::create failed: " + errstr);
    }
    const RdKafka::ErrorCode sub = consumer_->subscribe({cfg_.topic_requests});
    if (sub != RdKafka::ERR_NO_ERROR) {
        running_ = false;
        throw std::runtime_error("subscribe failed: " + RdKafka::err2str(sub));
    }

    std::error_code ec;
    std::filesystem::create_directories(cfg_.output_dir, ec);

    thread_ = std::thread(&SnapshotConsumer::run_, this);
}

void SnapshotConsumer::stop() {
    if (!running_.exchange(false)) return;
    if (consumer_) consumer_->close();
    if (thread_.joinable()) thread_.join();
    consumer_.reset();
}

void SnapshotConsumer::run_() {
    while (running_.load(std::memory_order_acquire)) {
        std::unique_ptr<RdKafka::Message> msg(consumer_->consume(cfg_.poll_timeout_ms));
        if (!msg) continue;
        switch (msg->err()) {
            case RdKafka::ERR__TIMED_OUT:
            case RdKafka::ERR__PARTITION_EOF:
                continue;
            case RdKafka::ERR_NO_ERROR:
                handle_message_(*msg);
                break;
            default:
                g_warning("[snapshot-consumer] consume err: %s",
                          msg->errstr().c_str());
                continue;
        }
        // Manual commit after handler returns.
        consumer_->commitAsync(msg.get());
    }
}

void SnapshotConsumer::handle_message_(RdKafka::Message& msg) {
    const std::string payload(static_cast<const char*>(msg.payload()), msg.len());

    nlohmann::json req;
    try {
        req = nlohmann::json::parse(payload);
    } catch (const std::exception& e) {
        g_warning("[snapshot-consumer] bad json: %s", e.what());
        return;
    }

    const std::string request_id = req.value("request_id", "");
    const std::string camera_id  = req.value("camera_id",  "");
    const std::string type       = req.value("type",       "full_frame");
    const std::uint64_t frame_id = req.value("frame_id",   0ULL);
    if (request_id.empty() || camera_id.empty()) {
        g_warning("[snapshot-consumer] missing request_id or camera_id");
        return;
    }

    nlohmann::json resp;
    resp["request_id"] = request_id;
    resp["camera_id"]  = camera_id;

    auto found = tap_.find(camera_id, frame_id);
    if (!found) {
        ring_misses_.fetch_add(1, std::memory_order_relaxed);
        resp["status"] = "miss";
        resp["path"]   = nullptr;
        resp["timestamp"] = iso_ms(
            static_cast<std::uint64_t>(
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::system_clock::now().time_since_epoch()).count()));
        producer_->produce_json(cfg_.topic_responses, request_id, resp.dump());
        return;
    }

    // Decide the payload: full frame or bbox crop.
    std::vector<std::uint8_t> out_jpeg;
    int out_w = found->width;
    int out_h = found->height;
    bool ok = true;

    if (type == "vehicle_crop" || type == "plate_region") {
#if PARKGUARD_CONSUMER_HAVE_OPENCV
        if (req.contains("bbox")) {
            const auto& b = req["bbox"];
            const int x = b.value("x", 0);
            const int y = b.value("y", 0);
            const int w = b.value("w", 0);
            const int h = b.value("h", 0);
            cv::Mat decoded = cv::imdecode(found->jpeg, cv::IMREAD_COLOR);
            if (!decoded.empty() && w > 0 && h > 0) {
                cv::Rect r(
                    std::max(0, x), std::max(0, y),
                    std::min(w, decoded.cols - std::max(0, x)),
                    std::min(h, decoded.rows - std::max(0, y)));
                if (r.width > 0 && r.height > 0) {
                    cv::Mat crop = decoded(r).clone();
                    const std::vector<int> params = {cv::IMWRITE_JPEG_QUALITY,
                                                      cfg_.jpeg_quality};
                    ok = cv::imencode(".jpg", crop, out_jpeg, params);
                    out_w = crop.cols;
                    out_h = crop.rows;
                } else {
                    ok = false;
                }
            } else {
                ok = false;
            }
        } else {
            // No bbox supplied — degrade to full frame.
            out_jpeg = found->jpeg;
        }
#else
        out_jpeg = found->jpeg;  // no OpenCV → ship full frame
#endif
    } else {
        out_jpeg = found->jpeg;
    }

    if (!ok || out_jpeg.empty()) {
        resp["status"]    = "decode_error";
        resp["timestamp"] = iso_ms(found->wall_ms);
        resp["path"]      = nullptr;
        producer_->produce_json(cfg_.topic_responses, request_id, resp.dump());
        return;
    }

    const std::string path =
        cfg_.output_dir + "/" + request_id + ".jpg";
    std::ofstream f(path, std::ios::binary);
    if (!f) {
        resp["status"]    = "write_error";
        resp["timestamp"] = iso_ms(found->wall_ms);
        resp["path"]      = nullptr;
        producer_->produce_json(cfg_.topic_responses, request_id, resp.dump());
        return;
    }
    f.write(reinterpret_cast<const char*>(out_jpeg.data()),
            static_cast<std::streamsize>(out_jpeg.size()));
    f.close();

    resp["status"]    = "ok";
    resp["path"]      = path;
    resp["timestamp"] = iso_ms(found->wall_ms);
    resp["width"]     = out_w;
    resp["height"]    = out_h;
    resp["bytes"]     = out_jpeg.size();
    producer_->produce_json(cfg_.topic_responses, request_id, resp.dump());

    handled_.fetch_add(1, std::memory_order_relaxed);
}

}  // namespace parkguard
