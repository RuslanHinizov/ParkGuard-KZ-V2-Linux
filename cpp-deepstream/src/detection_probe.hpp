// =============================================================================
// cpp-deepstream/src/detection_probe.hpp
// -----------------------------------------------------------------------------
// Attaches a pad probe on the tracker src pad. For every outgoing buffer we:
//
//   1. walk NvDsBatchMeta → NvDsFrameMeta → NvDsObjectMeta
//   2. filter to vehicle classes (car/motorcycle/bus/truck)
//   3. build one JSON envelope per camera matching shared/schemas.py
//      DetectionMessage + DetectedObject and publish to the `detections`
//      Kafka topic keyed by sensor_id.
//
// The probe does NOT block the pipeline; JSON serialisation is O(#objects)
// per frame and we hand the payload off to the async KafkaProducer, so the
// probe returns GST_PAD_PROBE_OK within microseconds in the steady state.
//
// Per-camera stats (frames out, fps EWMA, dropped frames, detections/s) are
// surfaced to health_server through ProbeStats — atomic so the HTTP thread
// can read without locking.
// =============================================================================
#pragma once

#include <gst/gst.h>

#include <atomic>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include "kafka_producer.hpp"

namespace parkguard {

struct ProbeStats {
    std::atomic<std::uint64_t> frames{0};
    std::atomic<std::uint64_t> detections{0};
    std::atomic<std::uint64_t> bytes_sent{0};
    std::atomic<std::uint64_t> produce_fails{0};
    std::atomic<std::uint64_t> last_frame_unix_ms{0};
};

// Map from pad_index (nvstreammux source index) → stable camera id + stats.
struct CameraIndex {
    std::vector<std::string>                       id_by_pad;     // pad → id
    std::unordered_map<std::uint32_t, std::size_t> pad_to_slot;   // pad → offset
    std::vector<std::unique_ptr<ProbeStats>>       stats;
};

class DetectionProbe {
public:
    DetectionProbe(std::shared_ptr<KafkaProducer> producer,
                   std::string topic,
                   CameraIndex index,
                   int         expected_embedding_dim = 0);

    // Install the probe on the given pad (usually the tracker's src pad).
    // Returns the probe id (non-zero on success).
    gulong install(GstPad* pad);

    const CameraIndex& index() const noexcept { return index_; }

    // Number of objects whose OSNet embedding was extracted/encoded this
    // session. Surfaced to /metrics for fleet-level monitoring.
    std::uint64_t embeddings_extracted() const noexcept {
        return embeddings_extracted_.load(std::memory_order_relaxed);
    }

private:
    static GstPadProbeReturn trampoline(GstPad* pad, GstPadProbeInfo* info, gpointer user_data);
    GstPadProbeReturn        on_buffer(GstPadProbeInfo* info);

    std::shared_ptr<KafkaProducer> producer_;
    std::string                    topic_;
    CameraIndex                    index_;
    int                            expected_embedding_dim_{0};
    std::atomic<std::uint64_t>     embeddings_extracted_{0};
};

}  // namespace parkguard
