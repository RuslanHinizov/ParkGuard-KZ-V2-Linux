// =============================================================================
// cpp-deepstream/src/snapshot_consumer.hpp
// -----------------------------------------------------------------------------
// Kafka consumer that drains the `snapshot_requests` topic, looks up frames
// in the SnapshotTap ring, writes JPEG files to the shared volume, and
// produces matching `snapshot_responses` envelopes.
//
// Runs one background thread. Uses librdkafka's C++ high-level consumer with
// manual offset commit (spec §15.1). A single consumer instance serves all
// cameras — the partition count of `snapshot_requests` is expected to be 1
// per DeepStream host; scale-out happens by adding DeepStream hosts, not
// threads.
//
// Request envelope (JSON):
//   {"camera_id": "cam_01",
//    "frame_id":  847123,              // optional
//    "request_id":"<uuid>",
//    "reason":    "violation"|"ocr"|"review",
//    "type":      "vehicle_crop"|"full_frame"|"plate_region",
//    "bbox":      {"x":340,"y":220,"w":180,"h":110}  // optional
//   }
//
// Response envelope (JSON):
//   {"request_id":"<uuid>",
//    "camera_id": "cam_01",
//    "path":      "/shared/snapshots/<uuid>.jpg",
//    "timestamp": "2026-04-17T10:23:45.123Z",
//    "width":     1920,
//    "height":    1080,
//    "status":    "ok"|"miss"|"decode_error"}
// =============================================================================
#pragma once

#include <librdkafka/rdkafkacpp.h>

#include <atomic>
#include <memory>
#include <string>
#include <thread>

namespace parkguard {

class KafkaProducer;
class SnapshotTap;

struct SnapshotConsumerCfg {
    bool        enabled{false};
    std::string brokers;
    std::string group_id{"deepstream-snapshot"};
    std::string topic_requests{"snapshot_requests"};
    std::string topic_responses{"snapshot_responses"};
    std::string output_dir{"/shared/snapshots"};
    int         jpeg_quality{85};   // for bbox re-crop
    int         poll_timeout_ms{500};
};

class SnapshotConsumer {
public:
    SnapshotConsumer(SnapshotConsumerCfg            cfg,
                     std::shared_ptr<KafkaProducer> producer,
                     const SnapshotTap&             tap);
    ~SnapshotConsumer();

    SnapshotConsumer(const SnapshotConsumer&)            = delete;
    SnapshotConsumer& operator=(const SnapshotConsumer&) = delete;

    // Start the consumer thread. Throws std::runtime_error on rdkafka
    // handle creation failure.
    void start();

    // Stop + join. Safe to call multiple times.
    void stop();

    // Metrics.
    std::uint64_t requests_handled() const noexcept {
        return handled_.load(std::memory_order_relaxed);
    }
    std::uint64_t ring_misses() const noexcept {
        return ring_misses_.load(std::memory_order_relaxed);
    }

private:
    void run_();
    void handle_message_(RdKafka::Message& msg);

    SnapshotConsumerCfg             cfg_;
    std::shared_ptr<KafkaProducer>  producer_;
    const SnapshotTap&              tap_;

    std::unique_ptr<RdKafka::KafkaConsumer> consumer_;
    std::thread                             thread_;
    std::atomic<bool>                       running_{false};

    std::atomic<std::uint64_t>              handled_{0};
    std::atomic<std::uint64_t>              ring_misses_{0};
};

}  // namespace parkguard
