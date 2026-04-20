// =============================================================================
// cpp-deepstream/src/snapshot_tap.hpp
// -----------------------------------------------------------------------------
// Per-camera 30-frame ring buffer of JPEG-encoded full frames (spec §7.1, §7.3).
//
// The tap is a GstPad probe that, for every incoming batched buffer, pulls the
// per-source frame out of the NvBufSurface, encodes it to JPEG on the GPU
// (nvjpegenc is invoked via a small helper element created once per process;
// `cv::imencode` is the CPU fallback when the GPU encoder path is
// unavailable), and stores the resulting blob — keyed by (camera_id, frame_id,
// wall-clock ms) — in a thread-safe SPMC ring. On request, the consumer thread
// reads the nearest frame and either returns the full-frame JPEG or decodes
// and re-encodes a bbox crop.
//
// The tap operates entirely out-of-band; the detection probe on the tracker
// src pad still sees the buffer unchanged. Ring writes are lock-free under
// a single producer (the streaming thread); reads take a short mutex.
// =============================================================================
#pragma once

#include <gst/gst.h>

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "detection_probe.hpp"  // CameraIndex, ProbeStats

namespace parkguard {

// A single frame-encoded slot. `jpeg` is small enough (100-300 KB for
// 1920×1080 @ Q=80) that we keep the whole ring in RAM.
struct FrameSlot {
    std::uint64_t frame_id{0};
    std::uint64_t wall_ms{0};
    int           width{0};
    int           height{0};
    std::vector<std::uint8_t> jpeg;  // full-frame encode
};

// Lock-free-ish SPMC ring per camera. Capacity is a compile-time constant
// (30 frames ≈ 1.2 s at 25 fps, spec §7.3). Writer is the streaming
// thread; readers are the snapshot-consumer thread(s).
class FrameRing {
public:
    static constexpr std::size_t CAPACITY = 30;

    FrameRing();

    // Push a new slot — overwrites the oldest when full.
    void push(FrameSlot&& slot);

    // Find the slot whose frame_id matches `wanted_frame_id`. If none,
    // returns the most recent frame in the ring. std::nullopt only when
    // the ring is still empty.
    std::optional<FrameSlot> find_or_latest(std::uint64_t wanted_frame_id) const;

    std::uint64_t frames_captured() const noexcept {
        return written_.load(std::memory_order_relaxed);
    }

private:
    mutable std::mutex                         mu_;
    std::array<FrameSlot, CAPACITY>            slots_{};
    std::atomic<std::uint64_t>                 written_{0};
};


// Snapshot tap config. Lives on RuntimeCfg (see config.hpp snapshot section).
struct SnapshotTapCfg {
    bool        enabled{false};
    int         jpeg_quality{80};       // 0–100 (OpenCV scale)
    int         downscale_to_width{0};  // 0 = no downscale; else longest edge
};


// Installed once on the tracker's src pad (same pad DetectionProbe uses).
// Both probes see the buffer in the same probe order — DetectionProbe
// returns OK, then our tap probe reads the surface and writes to the ring.
class SnapshotTap {
public:
    SnapshotTap(SnapshotTapCfg cfg, const CameraIndex& index);

    // Attach probe; returns GstPad probe id (0 on failure).
    gulong install(GstPad* pad);

    // Ring lookup — called from the snapshot_consumer thread. camera_id
    // must be one the tap knows about; returns nullopt for an unknown
    // camera or an empty ring.
    std::optional<FrameSlot> find(const std::string& camera_id,
                                  std::uint64_t       wanted_frame_id) const;

    // Metric accessor: total frames captured across all cameras.
    std::uint64_t frames_captured() const noexcept;

private:
    static GstPadProbeReturn trampoline(GstPad* pad,
                                        GstPadProbeInfo* info,
                                        gpointer user_data);
    GstPadProbeReturn        on_buffer(GstPadProbeInfo* info);

    // Encode a single NvBufSurface frame plane to JPEG via OpenCV.
    // Returns an empty vector on any failure (mapping, cvtColor, encode).
    std::vector<std::uint8_t> encode_frame_(
        void* surface_ptr,
        std::uint32_t source_id,
        int& out_w,
        int& out_h) const;

    SnapshotTapCfg                                       cfg_;
    std::unordered_map<std::string, std::unique_ptr<FrameRing>> rings_;
    // camera_id lookup from pad index (nvstreammux source id).
    std::unordered_map<std::uint32_t, std::string>       pad_to_camera_;
};

}  // namespace parkguard
