// =============================================================================
// cpp-deepstream/src/kafka_producer.hpp
// -----------------------------------------------------------------------------
// Thin wrapper around librdkafka's C++ producer. Design goals:
//   * non-blocking produce() (fire-and-forget from inside GStreamer probes)
//   * a background poll() thread so delivery reports are drained
//   * no throwing — every failure is logged and the call returns false
//
// Thread safety: produce() is safe to call from any thread. Stop() must be
// called before destruction.
// =============================================================================
#pragma once

#include <librdkafka/rdkafkacpp.h>

#include <atomic>
#include <memory>
#include <string>
#include <thread>

namespace parkguard {

class KafkaProducer : public RdKafka::DeliveryReportCb, public RdKafka::EventCb {
public:
    KafkaProducer(std::string brokers,
                  std::string client_id,
                  std::string compression);
    ~KafkaProducer() override;

    KafkaProducer(const KafkaProducer&)            = delete;
    KafkaProducer& operator=(const KafkaProducer&) = delete;

    // Start the background poll thread; throws on handle creation failure.
    void start();
    void stop();

    // Enqueue a message. Returns false on queue-full / unknown topic.
    // `key` may be empty. Ownership of `payload` is not transferred —
    // librdkafka copies it internally (RK_MSG_COPY).
    bool produce_json(const std::string& topic,
                      const std::string& key,
                      const std::string& payload);

    // Drain-on-shutdown: flush up to timeout_ms.
    void flush(int timeout_ms = 5000);

    // rdkafka callbacks
    void dr_cb(RdKafka::Message& m) override;
    void event_cb(RdKafka::Event& e) override;

private:
    std::string                      brokers_;
    std::string                      client_id_;
    std::string                      compression_;
    std::unique_ptr<RdKafka::Producer> producer_;
    std::thread                      poll_thread_;
    std::atomic<bool>                running_{false};
};

}  // namespace parkguard
