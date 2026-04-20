// =============================================================================
// cpp-deepstream/src/config.hpp
// -----------------------------------------------------------------------------
// Loads the YAML runtime config used by the DeepStream binary. Adım 3 (MVP)
// supports exactly one camera; Adım 11 extends the schema to N with the same
// reader (see spec §14 scale step).
//
// Schema (runtime.yml):
//
//   muxer:
//     width: 1280
//     height: 720
//     batched_push_timeout_us: 40000
//     live_source: true
//   pgie:
//     config_file: configs/pgie_yolo11s.txt
//   tracker:
//     config_file: configs/tracker_nvdcf.yml
//     ll_lib: /opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so
//     width: 640
//     height: 384
//   kafka:
//     brokers: "kafka:9092"
//     topic_detections: "detections"
//     client_id: "deepstream-cam_01"
//     compression: "lz4"
//   health:
//     bind: "0.0.0.0"
//     port: 9100
//   cameras:
//     - id: "cam_01"
//       rtsp_url: "rtsp://demo:demo@192.168.1.10:554/stream1"
//       enabled: true
// =============================================================================
#pragma once

#include <string>
#include <vector>

namespace parkguard {

struct CameraCfg {
    std::string id;
    std::string rtsp_url;
    bool        enabled{true};
};

struct MuxerCfg {
    int  width{1280};
    int  height{720};
    int  batched_push_timeout_us{40000};
    bool live_source{true};
};

struct PgieCfg {
    std::string config_file;
};

// SGIE is optional — Adım 3 MVP runs PGIE + tracker only; Adım 6
// flips `enabled: true` and points `config_file` at sgie_osnet_reid.txt.
struct SgieCfg {
    bool        enabled{false};
    std::string config_file;
    int         embedding_dim{128};
};

struct TrackerCfg {
    std::string config_file;
    std::string ll_lib{"/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"};
    int         width{640};
    int         height{384};
};

struct KafkaCfg {
    std::string brokers;
    std::string topic_detections{"detections"};
    std::string client_id{"deepstream"};
    std::string compression{"lz4"};
};

struct HealthCfg {
    std::string bind{"0.0.0.0"};
    int         port{9100};
};

// Snapshot tap (Adım 8, spec §7.3). When `enabled: false` (MVP default)
// no ring buffer is allocated and the Kafka consumer doesn't start.
struct SnapshotCfg {
    bool        enabled{false};
    int         jpeg_quality_ring{80};        // ring encode quality
    int         jpeg_quality_crop{85};        // bbox re-encode quality
    int         ring_downscale_to_width{0};   // 0 = keep original size
    std::string topic_requests{"snapshot_requests"};
    std::string topic_responses{"snapshot_responses"};
    std::string group_id{"deepstream-snapshot"};
    std::string output_dir{"/shared/snapshots"};
    int         poll_timeout_ms{500};
};

struct RuntimeCfg {
    MuxerCfg                  muxer;
    PgieCfg                   pgie;
    SgieCfg                   sgie;
    TrackerCfg                tracker;
    KafkaCfg                  kafka;
    HealthCfg                 health;
    SnapshotCfg               snapshot;
    std::vector<CameraCfg>    cameras;
};

// Load from a YAML file, throwing std::runtime_error with a helpful message
// on any schema violation. The caller owns the returned value.
RuntimeCfg load_runtime_cfg(const std::string& path);

}  // namespace parkguard
