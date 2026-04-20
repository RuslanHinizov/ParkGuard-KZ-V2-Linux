// =============================================================================
// cpp-deepstream/src/snapshot_tap.cpp
// =============================================================================
#include "snapshot_tap.hpp"

#include <gstnvdsmeta.h>
#include <nvbufsurface.h>
#include <nvdsmeta.h>

#include <chrono>
#include <cstring>
#include <utility>

#if __has_include(<opencv2/imgcodecs.hpp>)
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#define PARKGUARD_HAVE_OPENCV 1
#else
#define PARKGUARD_HAVE_OPENCV 0
#endif

namespace parkguard {

namespace {

std::uint64_t now_wall_ms() {
    using namespace std::chrono;
    return static_cast<std::uint64_t>(
        duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count());
}

}  // namespace


// --------------------------------------------------------------------------- //
// FrameRing
// --------------------------------------------------------------------------- //
FrameRing::FrameRing() = default;

void FrameRing::push(FrameSlot&& slot) {
    std::lock_guard<std::mutex> lock(mu_);
    const std::uint64_t idx = written_.load(std::memory_order_relaxed);
    slots_[idx % CAPACITY] = std::move(slot);
    written_.store(idx + 1, std::memory_order_release);
}

std::optional<FrameSlot> FrameRing::find_or_latest(std::uint64_t wanted_frame_id) const {
    std::lock_guard<std::mutex> lock(mu_);
    const std::uint64_t total = written_.load(std::memory_order_acquire);
    if (total == 0) return std::nullopt;

    const std::uint64_t n = total < CAPACITY ? total : CAPACITY;
    const std::uint64_t start = total - n;

    // Exact match scan.
    if (wanted_frame_id != 0) {
        for (std::uint64_t i = start; i < total; ++i) {
            const FrameSlot& s = slots_[i % CAPACITY];
            if (s.frame_id == wanted_frame_id && !s.jpeg.empty()) {
                return s;
            }
        }
    }

    // Fallback: latest non-empty slot.
    for (std::uint64_t i = total; i > start; --i) {
        const FrameSlot& s = slots_[(i - 1) % CAPACITY];
        if (!s.jpeg.empty()) return s;
    }
    return std::nullopt;
}


// --------------------------------------------------------------------------- //
// SnapshotTap
// --------------------------------------------------------------------------- //
SnapshotTap::SnapshotTap(SnapshotTapCfg cfg, const CameraIndex& index)
    : cfg_(std::move(cfg)) {
    for (std::size_t i = 0; i < index.id_by_pad.size(); ++i) {
        const std::string& cam = index.id_by_pad[i];
        if (cam.empty()) continue;
        rings_.emplace(cam, std::make_unique<FrameRing>());
        pad_to_camera_.emplace(static_cast<std::uint32_t>(i), cam);
    }
}

gulong SnapshotTap::install(GstPad* pad) {
    if (!cfg_.enabled) return 0;
    if (!pad) return 0;
    return gst_pad_add_probe(
        pad,
        GST_PAD_PROBE_TYPE_BUFFER,
        &SnapshotTap::trampoline,
        this,
        nullptr);
}

std::optional<FrameSlot> SnapshotTap::find(const std::string& camera_id,
                                            std::uint64_t       wanted_frame_id) const {
    auto it = rings_.find(camera_id);
    if (it == rings_.end()) return std::nullopt;
    return it->second->find_or_latest(wanted_frame_id);
}

std::uint64_t SnapshotTap::frames_captured() const noexcept {
    std::uint64_t total = 0;
    for (const auto& [_, ring] : rings_) {
        total += ring->frames_captured();
    }
    return total;
}

GstPadProbeReturn SnapshotTap::trampoline(GstPad* /*pad*/,
                                          GstPadProbeInfo* info,
                                          gpointer user_data) {
    return static_cast<SnapshotTap*>(user_data)->on_buffer(info);
}

GstPadProbeReturn SnapshotTap::on_buffer(GstPadProbeInfo* info) {
    GstBuffer* buf = GST_PAD_PROBE_INFO_BUFFER(info);
    if (!buf) return GST_PAD_PROBE_OK;

    NvDsBatchMeta* batch = gst_buffer_get_nvds_batch_meta(buf);
    if (!batch) return GST_PAD_PROBE_OK;

    // NvBufSurface is the DeepStream alias for the CUDA-backed frame
    // storage. Mapping to host memory is expensive — we skip frames
    // whose camera has no ring (disabled) to minimise overhead.
    GstMapInfo map_info;
    if (!gst_buffer_map(buf, &map_info, GST_MAP_READ)) {
        return GST_PAD_PROBE_OK;
    }
    auto* surface = reinterpret_cast<NvBufSurface*>(map_info.data);

    const std::uint64_t wall = now_wall_ms();

    for (NvDsMetaList* l = batch->frame_meta_list; l; l = l->next) {
        auto* fm = static_cast<NvDsFrameMeta*>(l->data);
        if (!fm) continue;

        auto cam_it = pad_to_camera_.find(fm->pad_index);
        if (cam_it == pad_to_camera_.end()) continue;

        int w = 0, h = 0;
        std::vector<std::uint8_t> jpeg = encode_frame_(surface, fm->source_id, w, h);
        if (jpeg.empty()) continue;

        FrameSlot slot;
        slot.frame_id = fm->frame_num;
        slot.wall_ms  = wall;
        slot.width    = w;
        slot.height   = h;
        slot.jpeg     = std::move(jpeg);

        rings_[cam_it->second]->push(std::move(slot));
    }

    gst_buffer_unmap(buf, &map_info);
    return GST_PAD_PROBE_OK;
}

std::vector<std::uint8_t> SnapshotTap::encode_frame_(void* surface_ptr,
                                                      std::uint32_t source_id,
                                                      int& out_w,
                                                      int& out_h) const {
#if PARKGUARD_HAVE_OPENCV
    auto* surface = static_cast<NvBufSurface*>(surface_ptr);
    if (!surface || source_id >= surface->numFilled) return {};

    NvBufSurfaceParams& p = surface->surfaceList[source_id];
    out_w = static_cast<int>(p.width);
    out_h = static_cast<int>(p.height);

    // Copy device → host if needed. NvBufSurfaceMap pins the surface to
    // host memory; we then wrap it into a cv::Mat with the right colour
    // format.
    if (NvBufSurfaceMap(surface, static_cast<int>(source_id), 0, NVBUF_MAP_READ) != 0) {
        return {};
    }
    NvBufSurfaceSyncForCpu(surface, static_cast<int>(source_id), 0);

    cv::Mat src_wrap;
    switch (p.colorFormat) {
        case NVBUF_COLOR_FORMAT_RGBA:
        case NVBUF_COLOR_FORMAT_BGRA: {
            src_wrap = cv::Mat(
                out_h, out_w, CV_8UC4,
                p.mappedAddr.addr[0],
                static_cast<size_t>(p.planeParams.pitch[0]));
            break;
        }
        default: {
            NvBufSurfaceUnMap(surface, static_cast<int>(source_id), 0);
            return {};
        }
    }

    cv::Mat bgr;
    if (p.colorFormat == NVBUF_COLOR_FORMAT_RGBA) {
        cv::cvtColor(src_wrap, bgr, cv::COLOR_RGBA2BGR);
    } else {
        cv::cvtColor(src_wrap, bgr, cv::COLOR_BGRA2BGR);
    }

    NvBufSurfaceUnMap(surface, static_cast<int>(source_id), 0);

    if (cfg_.downscale_to_width > 0 && bgr.cols > cfg_.downscale_to_width) {
        const double r = static_cast<double>(cfg_.downscale_to_width) / bgr.cols;
        cv::Mat scaled;
        cv::resize(bgr, scaled, cv::Size(), r, r, cv::INTER_AREA);
        bgr = std::move(scaled);
        out_w = bgr.cols;
        out_h = bgr.rows;
    }

    std::vector<std::uint8_t> out;
    const std::vector<int> params = {cv::IMWRITE_JPEG_QUALITY, cfg_.jpeg_quality};
    if (!cv::imencode(".jpg", bgr, out, params)) {
        return {};
    }
    return out;
#else
    (void)surface_ptr;
    (void)source_id;
    out_w = 0;
    out_h = 0;
    return {};
#endif
}

}  // namespace parkguard
