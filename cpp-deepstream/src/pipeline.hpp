// =============================================================================
// cpp-deepstream/src/pipeline.hpp
// -----------------------------------------------------------------------------
// Builds and owns the single GStreamer pipeline:
//
//   uridecodebin(N) → nvstreammux → nvinfer(PGIE) → nvtracker → fakesink
//                                                        ↑
//                                         DetectionProbe installed here
//
// Adım 3 MVP runs N=1 camera. The class is N-safe already; Adım 11 just
// adds more cameras to the config and gets multiple source branches.
// =============================================================================
#pragma once

#include <gst/gst.h>

#include <memory>
#include <string>
#include <vector>

#include "config.hpp"
#include "detection_probe.hpp"
#include "kafka_producer.hpp"

namespace parkguard {

class Pipeline {
public:
    Pipeline(RuntimeCfg cfg, std::shared_ptr<KafkaProducer> producer);
    ~Pipeline();

    Pipeline(const Pipeline&)            = delete;
    Pipeline& operator=(const Pipeline&) = delete;

    // Build the graph and return once all elements are linked. Throws
    // std::runtime_error with a descriptive message on any failure.
    void build();

    // Set state to PLAYING. Returns false if the state change fails
    // (caller should shut down).
    bool start();

    // Set state to NULL, unref. Safe to call multiple times.
    void stop();

    // Pointer to the GMainLoop the caller should run.
    GMainLoop*           main_loop()      const noexcept { return loop_; }
    const DetectionProbe* detection_probe() const noexcept { return probe_.get(); }

private:
    GstElement* build_source_bin(std::size_t pad_index, const CameraCfg& cam);

    static gboolean on_bus_message(GstBus* bus, GstMessage* msg, gpointer user_data);
    static void     on_pad_added(GstElement* src, GstPad* new_pad, gpointer user_data);

    RuntimeCfg                     cfg_;
    std::shared_ptr<KafkaProducer> producer_;

    GstElement* pipeline_{nullptr};
    GstElement* streammux_{nullptr};
    GstElement* pgie_{nullptr};
    GstElement* tracker_{nullptr};
    GstElement* sink_{nullptr};

    GMainLoop*                      loop_{nullptr};
    std::unique_ptr<DetectionProbe> probe_;
    std::vector<GstElement*>        source_bins_;
};

}  // namespace parkguard
