// =============================================================================
// cpp-deepstream/src/detection_probe.cpp
// =============================================================================
#include "detection_probe.hpp"

#include <gstnvdsmeta.h>
#include <nvdsmeta.h>

#if __has_include(<nlohmann/json.hpp>)
#include <nlohmann/json.hpp>
#else
#include "json.hpp"
#endif

#include <chrono>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <utility>

namespace parkguard {

namespace {

// ISO-8601 UTC with ms precision: 2026-04-17T10:23:45.123Z
std::string iso_utc_ms(std::uint64_t frame_ns_pts, std::uint64_t wall_ms) {
    (void)frame_ns_pts;  // frame PTS is pipeline-local; we stamp with wall time
    const auto ms_epoch = std::chrono::milliseconds{wall_ms};
    const auto secs     = std::chrono::duration_cast<std::chrono::seconds>(ms_epoch);
    const std::time_t t = static_cast<std::time_t>(secs.count());
    std::tm tm{};
#ifdef _WIN32
    gmtime_s(&tm, &t);
#else
    gmtime_r(&t, &tm);
#endif
    std::ostringstream os;
    os << std::put_time(&tm, "%Y-%m-%dT%H:%M:%S");
    os << '.' << std::setw(3) << std::setfill('0') << (wall_ms % 1000) << 'Z';
    return os.str();
}

const char* class_name_for(int class_id) {
    // PGIE model label order (spec §7.2 filter set [2,3,5,7]).
    switch (class_id) {
        case 2: return "car";
        case 3: return "motorcycle";
        case 5: return "bus";
        case 7: return "truck";
        default: return "other";
    }
}

bool is_vehicle(int class_id) {
    return class_id == 2 || class_id == 3 || class_id == 5 || class_id == 7;
}

std::uint64_t now_wall_ms() {
    using namespace std::chrono;
    return static_cast<std::uint64_t>(
        duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count());
}

}  // namespace

DetectionProbe::DetectionProbe(std::shared_ptr<KafkaProducer> producer,
                               std::string                    topic,
                               CameraIndex                    index)
    : producer_(std::move(producer)),
      topic_(std::move(topic)),
      index_(std::move(index)) {}

gulong DetectionProbe::install(GstPad* pad) {
    return gst_pad_add_probe(
        pad,
        GST_PAD_PROBE_TYPE_BUFFER,
        &DetectionProbe::trampoline,
        this,
        /* notify */ nullptr);
}

GstPadProbeReturn DetectionProbe::trampoline(GstPad*          /*pad*/,
                                             GstPadProbeInfo* info,
                                             gpointer         user_data) {
    return static_cast<DetectionProbe*>(user_data)->on_buffer(info);
}

GstPadProbeReturn DetectionProbe::on_buffer(GstPadProbeInfo* info) {
    auto* buf = static_cast<GstBuffer*>(info->data);
    if (!buf) return GST_PAD_PROBE_OK;

    NvDsBatchMeta* batch = gst_buffer_get_nvds_batch_meta(buf);
    if (!batch) return GST_PAD_PROBE_OK;

    const std::uint64_t wall_ms = now_wall_ms();

    for (NvDsMetaList* l_frame = batch->frame_meta_list; l_frame; l_frame = l_frame->next) {
        auto* fm = static_cast<NvDsFrameMeta*>(l_frame->data);
        if (!fm) continue;

        const std::uint32_t pad_idx = fm->pad_index;
        auto it = index_.pad_to_slot.find(pad_idx);
        if (it == index_.pad_to_slot.end()) continue;
        const std::size_t slot = it->second;

        nlohmann::json envelope;
        envelope["sensor_id"] = index_.id_by_pad[slot];
        envelope["timestamp"] = iso_utc_ms(fm->buf_pts, wall_ms);
        envelope["frame_id"]  = static_cast<std::uint64_t>(fm->frame_num);
        envelope["objects"]   = nlohmann::json::array();

        std::uint64_t det_count = 0;
        for (NvDsMetaList* l_obj = fm->obj_meta_list; l_obj; l_obj = l_obj->next) {
            auto* om = static_cast<NvDsObjectMeta*>(l_obj->data);
            if (!om) continue;
            if (!is_vehicle(om->class_id)) continue;

            const auto& r = om->rect_params;
            nlohmann::json obj;
            obj["ds_track_id"] = static_cast<std::int64_t>(om->object_id);
            obj["class_id"]    = om->class_id;
            obj["class_name"]  = class_name_for(om->class_id);
            obj["confidence"]  = om->confidence;
            obj["bbox"]        = {
                {"x", static_cast<int>(r.left)},
                {"y", static_cast<int>(r.top)},
                {"w", static_cast<int>(r.width)},
                {"h", static_cast<int>(r.height)}};
            obj["centroid"]    = {
                {"x", static_cast<int>(r.left + r.width  / 2.0)},
                {"y", static_cast<int>(r.top  + r.height / 2.0)}};
            obj["embedding_b64"] = nullptr;  // populated from Adım 6 onwards
            envelope["objects"].push_back(std::move(obj));
            ++det_count;
        }

        const std::string payload = envelope.dump();
        const bool ok = producer_->produce_json(topic_, index_.id_by_pad[slot], payload);

        auto& st = *index_.stats[slot];
        st.frames.fetch_add(1, std::memory_order_relaxed);
        st.detections.fetch_add(det_count, std::memory_order_relaxed);
        st.bytes_sent.fetch_add(payload.size(), std::memory_order_relaxed);
        if (!ok) st.produce_fails.fetch_add(1, std::memory_order_relaxed);
        st.last_frame_unix_ms.store(wall_ms, std::memory_order_relaxed);
    }

    return GST_PAD_PROBE_OK;
}

}  // namespace parkguard
