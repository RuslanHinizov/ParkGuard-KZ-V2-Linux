// =============================================================================
// cpp-deepstream/src/base64.cpp
// =============================================================================
#include "base64.hpp"

namespace parkguard {

namespace {
constexpr char kTable[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
}

std::string base64_encode(const std::uint8_t* data, std::size_t len) {
    std::string out;
    out.reserve(((len + 2) / 3) * 4);

    std::size_t i = 0;
    for (; i + 2 < len; i += 3) {
        std::uint32_t triple = (static_cast<std::uint32_t>(data[i])     << 16)
                             | (static_cast<std::uint32_t>(data[i + 1]) <<  8)
                             |  static_cast<std::uint32_t>(data[i + 2]);
        out.push_back(kTable[(triple >> 18) & 0x3F]);
        out.push_back(kTable[(triple >> 12) & 0x3F]);
        out.push_back(kTable[(triple >>  6) & 0x3F]);
        out.push_back(kTable[ triple        & 0x3F]);
    }
    if (i < len) {
        std::uint32_t triple = static_cast<std::uint32_t>(data[i]) << 16;
        if (i + 1 < len) triple |= static_cast<std::uint32_t>(data[i + 1]) << 8;
        out.push_back(kTable[(triple >> 18) & 0x3F]);
        out.push_back(kTable[(triple >> 12) & 0x3F]);
        out.push_back(i + 1 < len ? kTable[(triple >> 6) & 0x3F] : '=');
        out.push_back('=');
    }
    return out;
}

}  // namespace parkguard
