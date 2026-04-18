// =============================================================================
// cpp-deepstream/src/kafka_producer.cpp
// =============================================================================
#include "kafka_producer.hpp"

#include <gst/gst.h>

#include <stdexcept>
#include <utility>

namespace parkguard {

KafkaProducer::KafkaProducer(std::string brokers,
                             std::string client_id,
                             std::string compression)
    : brokers_(std::move(brokers)),
      client_id_(std::move(client_id)),
      compression_(std::move(compression)) {}

KafkaProducer::~KafkaProducer() {
    stop();
}

void KafkaProducer::start() {
    std::string errstr;
    auto cfg = std::unique_ptr<RdKafka::Conf>(
        RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL));

    auto set = [&](const char* k, const std::string& v) {
        if (cfg->set(k, v, errstr) != RdKafka::Conf::CONF_OK) {
            throw std::runtime_error(std::string("rdkafka cfg ") + k + ": " + errstr);
        }
    };
    set("bootstrap.servers",   brokers_);
    set("client.id",           client_id_);
    set("compression.type",    compression_);
    set("enable.idempotence",  "true");
    set("acks",                "all");
    set("linger.ms",           "5");
    set("batch.num.messages",  "200");
    set("message.max.bytes",   std::to_string(10 * 1024 * 1024));  // 10 MB
    set("queue.buffering.max.messages",  "100000");
    set("queue.buffering.max.ms",        "10");

    // Register callbacks
    if (cfg->set("dr_cb",    this, errstr) != RdKafka::Conf::CONF_OK)
        throw std::runtime_error("rdkafka dr_cb: " + errstr);
    if (cfg->set("event_cb", this, errstr) != RdKafka::Conf::CONF_OK)
        throw std::runtime_error("rdkafka event_cb: " + errstr);

    producer_.reset(RdKafka::Producer::create(cfg.get(), errstr));
    if (!producer_) {
        throw std::runtime_error("rdkafka producer create: " + errstr);
    }

    running_ = true;
    poll_thread_ = std::thread([this] {
        while (running_.load()) {
            producer_->poll(200);
        }
    });
    g_message("kafka_producer started client_id=%s brokers=%s",
              client_id_.c_str(), brokers_.c_str());
}

void KafkaProducer::stop() {
    if (!running_.exchange(false)) return;
    if (producer_) {
        producer_->flush(5000);
    }
    if (poll_thread_.joinable()) poll_thread_.join();
    producer_.reset();
    g_message("kafka_producer stopped");
}

bool KafkaProducer::produce_json(const std::string& topic,
                                 const std::string& key,
                                 const std::string& payload) {
    if (!producer_) return false;

    const void* kptr = key.empty() ? nullptr : static_cast<const void*>(key.data());
    const size_t klen = key.empty() ? 0 : key.size();

    RdKafka::ErrorCode err = producer_->produce(
        topic,
        RdKafka::Topic::PARTITION_UA,
        RdKafka::Producer::RK_MSG_COPY,
        const_cast<char*>(payload.data()),
        payload.size(),
        kptr, klen,
        /* timestamp */ 0,
        /* headers */   nullptr,
        /* opaque */    nullptr);

    if (err != RdKafka::ERR_NO_ERROR) {
        g_warning("kafka produce failed: %s", RdKafka::err2str(err).c_str());
        return false;
    }
    return true;
}

void KafkaProducer::flush(int timeout_ms) {
    if (producer_) producer_->flush(timeout_ms);
}

void KafkaProducer::dr_cb(RdKafka::Message& m) {
    if (m.err() != RdKafka::ERR_NO_ERROR) {
        g_warning("kafka delivery failed topic=%s err=%s",
                  m.topic_name().c_str(),
                  m.errstr().c_str());
    }
}

void KafkaProducer::event_cb(RdKafka::Event& e) {
    switch (e.type()) {
        case RdKafka::Event::EVENT_ERROR:
            g_warning("kafka event ERROR: %s", e.str().c_str());
            break;
        case RdKafka::Event::EVENT_LOG:
            if (e.severity() <= RdKafka::Event::EVENT_SEVERITY_WARNING) {
                g_warning("kafka log[%d] %s: %s", e.severity(), e.fac().c_str(), e.str().c_str());
            }
            break;
        default:
            break;
    }
}

}  // namespace parkguard
