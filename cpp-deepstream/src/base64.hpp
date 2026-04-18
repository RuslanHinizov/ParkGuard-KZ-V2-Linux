// =============================================================================
// cpp-deepstream/src/base64.hpp
// -----------------------------------------------------------------------------
// Tiny base64 encoder for OSNet embedding bytes (Adım 6+). Kept separate so
// the probe code stays readable and unit-testable without a crypto dep.
// Thread-safe (pure function, no global state).
// =============================================================================
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace parkguard {

std::string base64_encode(const std::uint8_t* data, std::size_t len);

inline std::string base64_encode(const float* data, std::size_t n_floats) {
    return base64_encode(reinterpret_cast<const std::uint8_t*>(data), n_floats * sizeof(float));
}

}  // namespace parkguard
