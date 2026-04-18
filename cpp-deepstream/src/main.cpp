// =============================================================================
// cpp-deepstream/src/main.cpp
// -----------------------------------------------------------------------------
// Entry point for the single-binary DeepStream detection runner.
//
// Usage:
//   parkguard_ds --config /etc/parkguard/runtime.yml
//
// The process lifetime:
//   1. parse args + YAML config
//   2. start Kafka producer (background poll thread)
//   3. build GStreamer pipeline (may throw)
//   4. start health HTTP server (separate thread)
//   5. run GMainLoop until SIGINT/SIGTERM/EOS/error
//   6. graceful shutdown: stop pipeline, flush kafka, join threads
// =============================================================================

#include <glib-unix.h>
#include <gst/gst.h>

#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <memory>
#include <string>

#include "config.hpp"
#include "health_server.hpp"
#include "kafka_producer.hpp"
#include "pipeline.hpp"

namespace {

struct Args {
    std::string config_path{"/etc/parkguard/runtime.yml"};
};

void print_usage(const char* argv0) {
    std::fprintf(stderr,
                 "usage: %s --config <runtime.yml>\n"
                 "  (defaults to /etc/parkguard/runtime.yml)\n",
                 argv0);
}

Args parse_args(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; ++i) {
        std::string s = argv[i];
        if (s == "--config" && i + 1 < argc) {
            a.config_path = argv[++i];
        } else if (s == "--version") {
            std::printf("parkguard_ds %s\n", PARKGUARD_VERSION);
            std::exit(0);
        } else {
            print_usage(argv[0]);
            std::exit(2);
        }
    }
    return a;
}

}  // namespace

static gboolean on_sigint(gpointer loop) {
    g_message("SIGINT/SIGTERM received, shutting down");
    g_main_loop_quit(static_cast<GMainLoop*>(loop));
    return G_SOURCE_CONTINUE;  // remain registered; loop exits
}

int main(int argc, char** argv) {
    gst_init(&argc, &argv);
    const Args args = parse_args(argc, argv);

    parkguard::RuntimeCfg cfg;
    try {
        cfg = parkguard::load_runtime_cfg(args.config_path);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[fatal] config: %s\n", e.what());
        return 1;
    }

    auto kafka = std::make_shared<parkguard::KafkaProducer>(
        cfg.kafka.brokers, cfg.kafka.client_id, cfg.kafka.compression);
    try {
        kafka->start();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[fatal] kafka: %s\n", e.what());
        return 1;
    }

    parkguard::Pipeline pipeline(cfg, kafka);
    try {
        pipeline.build();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[fatal] pipeline build: %s\n", e.what());
        kafka->stop();
        return 1;
    }

    parkguard::HealthServer health(cfg.health.bind, cfg.health.port, *pipeline.detection_probe());
    health.start();

    if (!pipeline.start()) {
        std::fprintf(stderr, "[fatal] pipeline start failed\n");
        health.stop();
        kafka->stop();
        return 1;
    }

    GMainLoop* loop = pipeline.main_loop();
    g_unix_signal_add(SIGINT,  on_sigint, loop);
    g_unix_signal_add(SIGTERM, on_sigint, loop);
    g_message("parkguard_ds %s running — %zu camera(s)",
              PARKGUARD_VERSION, cfg.cameras.size());
    g_main_loop_run(loop);

    pipeline.stop();
    health.stop();
    kafka->stop();
    g_message("parkguard_ds exited cleanly");
    return 0;
}
