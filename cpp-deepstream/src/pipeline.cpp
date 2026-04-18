// =============================================================================
// cpp-deepstream/src/pipeline.cpp
// =============================================================================
#include "pipeline.hpp"

#include <gst/gst.h>

#include <cassert>
#include <cstdio>
#include <stdexcept>
#include <utility>

namespace parkguard {

namespace {

// Helpers ---------------------------------------------------------------------
GstElement* make_or_throw(const char* factory, const char* name) {
    GstElement* e = gst_element_factory_make(factory, name);
    if (!e) {
        throw std::runtime_error(std::string("failed to create element '") + factory + "'");
    }
    return e;
}

void link_or_throw(GstElement* a, GstElement* b, const char* a_name, const char* b_name) {
    if (!gst_element_link(a, b)) {
        throw std::runtime_error(std::string("failed to link ") + a_name + " -> " + b_name);
    }
}

// Link an RTSP source pad coming out of uridecodebin into an nvstreammux sink pad.
void link_src_to_mux(GstPad* src_pad, GstElement* streammux, std::size_t pad_index) {
    std::string sink_name = "sink_" + std::to_string(pad_index);
    GstPad* sink_pad = gst_element_request_pad_simple(streammux, sink_name.c_str());
    if (!sink_pad) {
        throw std::runtime_error("streammux request pad failed: " + sink_name);
    }
    if (gst_pad_link(src_pad, sink_pad) != GST_PAD_LINK_OK) {
        gst_object_unref(sink_pad);
        throw std::runtime_error("gst_pad_link failed for " + sink_name);
    }
    gst_object_unref(sink_pad);
}

}  // namespace

// -----------------------------------------------------------------------------
Pipeline::Pipeline(RuntimeCfg cfg, std::shared_ptr<KafkaProducer> producer)
    : cfg_(std::move(cfg)), producer_(std::move(producer)) {}

Pipeline::~Pipeline() {
    stop();
    if (loop_) {
        g_main_loop_unref(loop_);
        loop_ = nullptr;
    }
}

void Pipeline::build() {
    loop_ = g_main_loop_new(nullptr, FALSE);
    pipeline_ = gst_pipeline_new("parkguard-pipeline");
    if (!pipeline_) throw std::runtime_error("gst_pipeline_new returned nullptr");

    streammux_ = make_or_throw("nvstreammux", "streammux");
    pgie_      = make_or_throw("nvinfer",     "pgie");
    tracker_   = make_or_throw("nvtracker",   "tracker");
    sink_      = make_or_throw("fakesink",    "sink");

    // ---- muxer config ------------------------------------------------------
    g_object_set(streammux_,
                 "width",                   cfg_.muxer.width,
                 "height",                  cfg_.muxer.height,
                 "batch-size",              static_cast<gint>(cfg_.cameras.size()),
                 "batched-push-timeout",    cfg_.muxer.batched_push_timeout_us,
                 "live-source",             cfg_.muxer.live_source ? TRUE : FALSE,
                 "nvbuf-memory-type",       0,
                 nullptr);

    // ---- pgie --------------------------------------------------------------
    g_object_set(pgie_,
                 "config-file-path", cfg_.pgie.config_file.c_str(),
                 nullptr);

    // ---- tracker -----------------------------------------------------------
    g_object_set(tracker_,
                 "tracker-width",          cfg_.tracker.width,
                 "tracker-height",         cfg_.tracker.height,
                 "ll-lib-file",            cfg_.tracker.ll_lib.c_str(),
                 "ll-config-file",         cfg_.tracker.config_file.c_str(),
                 "compute-hw",             1,         // GPU
                 "display-tracking-id",    FALSE,
                 nullptr);

    // ---- sink (MVP just drops; Adım 8+ adds snapshot tee) ------------------
    g_object_set(sink_,
                 "sync",        FALSE,
                 "enable-last-sample", FALSE,
                 "async",       FALSE,
                 "qos",         FALSE,
                 nullptr);

    gst_bin_add_many(GST_BIN(pipeline_), streammux_, pgie_, tracker_, sink_, nullptr);

    // ---- sources -----------------------------------------------------------
    CameraIndex index;
    for (std::size_t i = 0; i < cfg_.cameras.size(); ++i) {
        const CameraCfg& cam = cfg_.cameras[i];
        if (!cam.enabled) continue;
        GstElement* bin = build_source_bin(i, cam);
        source_bins_.push_back(bin);

        index.id_by_pad.push_back(cam.id);
        index.pad_to_slot[static_cast<std::uint32_t>(i)] = index.id_by_pad.size() - 1;
        index.stats.emplace_back(std::make_unique<ProbeStats>());
    }

    // ---- link pgie -> tracker -> sink --------------------------------------
    link_or_throw(streammux_, pgie_,    "streammux",   "pgie");
    link_or_throw(pgie_,      tracker_, "pgie",        "tracker");
    link_or_throw(tracker_,   sink_,    "tracker",     "sink");

    // ---- attach probe on tracker src pad -----------------------------------
    probe_ = std::make_unique<DetectionProbe>(producer_, cfg_.kafka.topic_detections, std::move(index));
    GstPad* tracker_src = gst_element_get_static_pad(tracker_, "src");
    if (!tracker_src) throw std::runtime_error("tracker src pad not found");
    if (probe_->install(tracker_src) == 0) {
        gst_object_unref(tracker_src);
        throw std::runtime_error("DetectionProbe install failed");
    }
    gst_object_unref(tracker_src);

    // ---- bus handler -------------------------------------------------------
    GstBus* bus = gst_pipeline_get_bus(GST_PIPELINE(pipeline_));
    gst_bus_add_watch(bus, &Pipeline::on_bus_message, this);
    gst_object_unref(bus);
}

GstElement* Pipeline::build_source_bin(std::size_t pad_index, const CameraCfg& cam) {
    const std::string bin_name = "src-bin-" + cam.id;
    GstElement* bin    = gst_bin_new(bin_name.c_str());
    GstElement* uridec = make_or_throw("uridecodebin", ("uridec-" + cam.id).c_str());
    g_object_set(uridec, "uri", cam.rtsp_url.c_str(), nullptr);

    // Stash the pad index + streammux pointer so the pad-added callback can wire up.
    g_object_set_data(G_OBJECT(uridec), "parkguard.pad_index",
                      GSIZE_TO_POINTER(pad_index));
    g_object_set_data(G_OBJECT(uridec), "parkguard.streammux", streammux_);

    g_signal_connect(uridec, "pad-added", G_CALLBACK(&Pipeline::on_pad_added), this);

    gst_bin_add(GST_BIN(bin), uridec);
    gst_bin_add(GST_BIN(pipeline_), bin);
    return bin;
}

gboolean Pipeline::on_bus_message(GstBus* /*bus*/, GstMessage* msg, gpointer user_data) {
    auto* self = static_cast<Pipeline*>(user_data);
    switch (GST_MESSAGE_TYPE(msg)) {
        case GST_MESSAGE_ERROR: {
            GError* err = nullptr;
            gchar*  dbg = nullptr;
            gst_message_parse_error(msg, &err, &dbg);
            g_printerr("[pipeline] ERROR from %s: %s\n",
                       GST_OBJECT_NAME(msg->src),
                       err ? err->message : "(unknown)");
            if (dbg) g_printerr("[pipeline] debug: %s\n", dbg);
            g_clear_error(&err);
            g_free(dbg);
            if (self->loop_) g_main_loop_quit(self->loop_);
            break;
        }
        case GST_MESSAGE_EOS:
            g_message("[pipeline] EOS received");
            if (self->loop_) g_main_loop_quit(self->loop_);
            break;
        case GST_MESSAGE_WARNING: {
            GError* err = nullptr;
            gchar*  dbg = nullptr;
            gst_message_parse_warning(msg, &err, &dbg);
            g_warning("[pipeline] WARN from %s: %s",
                      GST_OBJECT_NAME(msg->src),
                      err ? err->message : "(unknown)");
            g_clear_error(&err);
            g_free(dbg);
            break;
        }
        case GST_MESSAGE_STATE_CHANGED: {
            if (GST_MESSAGE_SRC(msg) == GST_OBJECT(self->pipeline_)) {
                GstState old_s, new_s, pend;
                gst_message_parse_state_changed(msg, &old_s, &new_s, &pend);
                g_message("[pipeline] state %s → %s",
                          gst_element_state_get_name(old_s),
                          gst_element_state_get_name(new_s));
            }
            break;
        }
        default: break;
    }
    return TRUE;
}

void Pipeline::on_pad_added(GstElement* src, GstPad* new_pad, gpointer user_data) {
    auto* self = static_cast<Pipeline*>(user_data);
    (void)self;

    GstCaps* caps = gst_pad_get_current_caps(new_pad);
    if (!caps) caps = gst_pad_query_caps(new_pad, nullptr);
    if (!caps) return;
    const GstStructure* s = gst_caps_get_structure(caps, 0);
    const char* name = gst_structure_get_name(s);
    const bool is_video = name && g_str_has_prefix(name, "video/");
    gst_caps_unref(caps);
    if (!is_video) return;

    auto* streammux = static_cast<GstElement*>(
        g_object_get_data(G_OBJECT(src), "parkguard.streammux"));
    const std::size_t pad_index =
        GPOINTER_TO_SIZE(g_object_get_data(G_OBJECT(src), "parkguard.pad_index"));
    try {
        link_src_to_mux(new_pad, streammux, pad_index);
        g_message("[pipeline] linked source pad → streammux sink_%zu", pad_index);
    } catch (const std::exception& e) {
        g_printerr("[pipeline] %s\n", e.what());
    }
}

bool Pipeline::start() {
    if (!pipeline_) return false;
    GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE) {
        g_printerr("[pipeline] failed to set PLAYING\n");
        return false;
    }
    return true;
}

void Pipeline::stop() {
    if (pipeline_) {
        gst_element_set_state(pipeline_, GST_STATE_NULL);
        gst_object_unref(pipeline_);
        pipeline_ = nullptr;
    }
}

}  // namespace parkguard
