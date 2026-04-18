// =============================================================================
// cpp-deepstream/src/config.cpp
// =============================================================================
#include "config.hpp"

#include <yaml-cpp/yaml.h>

#include <stdexcept>

namespace parkguard {

namespace {

template <typename T>
T req(const YAML::Node& n, const char* key) {
    if (!n[key] || n[key].IsNull()) {
        throw std::runtime_error(std::string("config missing required key: ") + key);
    }
    return n[key].as<T>();
}

}  // namespace

RuntimeCfg load_runtime_cfg(const std::string& path) {
    YAML::Node root;
    try {
        root = YAML::LoadFile(path);
    } catch (const YAML::Exception& e) {
        throw std::runtime_error("failed to parse config " + path + ": " + e.what());
    }
    if (!root.IsMap()) {
        throw std::runtime_error("config root must be a mapping: " + path);
    }

    RuntimeCfg c;

    if (auto m = root["muxer"]) {
        c.muxer.width                   = m["width"].as<int>(c.muxer.width);
        c.muxer.height                  = m["height"].as<int>(c.muxer.height);
        c.muxer.batched_push_timeout_us = m["batched_push_timeout_us"]
                                              .as<int>(c.muxer.batched_push_timeout_us);
        c.muxer.live_source             = m["live_source"].as<bool>(c.muxer.live_source);
    }

    c.pgie.config_file = req<std::string>(root["pgie"], "config_file");

    if (auto t = root["tracker"]) {
        c.tracker.config_file = req<std::string>(t, "config_file");
        c.tracker.ll_lib      = t["ll_lib"].as<std::string>(c.tracker.ll_lib);
        c.tracker.width       = t["width"].as<int>(c.tracker.width);
        c.tracker.height      = t["height"].as<int>(c.tracker.height);
    } else {
        throw std::runtime_error("config missing 'tracker'");
    }

    if (auto k = root["kafka"]) {
        c.kafka.brokers          = req<std::string>(k, "brokers");
        c.kafka.topic_detections = k["topic_detections"].as<std::string>(c.kafka.topic_detections);
        c.kafka.client_id        = k["client_id"].as<std::string>(c.kafka.client_id);
        c.kafka.compression      = k["compression"].as<std::string>(c.kafka.compression);
    } else {
        throw std::runtime_error("config missing 'kafka'");
    }

    if (auto h = root["health"]) {
        c.health.bind = h["bind"].as<std::string>(c.health.bind);
        c.health.port = h["port"].as<int>(c.health.port);
    }

    auto cams = root["cameras"];
    if (!cams || !cams.IsSequence() || cams.size() == 0) {
        throw std::runtime_error("config 'cameras' must be a non-empty sequence");
    }
    for (const auto& node : cams) {
        CameraCfg cam;
        cam.id       = req<std::string>(node, "id");
        cam.rtsp_url = req<std::string>(node, "rtsp_url");
        cam.enabled  = node["enabled"].as<bool>(true);
        c.cameras.push_back(std::move(cam));
    }
    return c;
}

}  // namespace parkguard
