// Optional process/system readings for the counter and a local CSV. No Vulkan or game dependency.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <optional>

namespace mkwvk::system_readings {
using Reading = std::optional<uint64_t>;
struct Sample {
    uint64_t monotonic_ns = 0;
    Reading cpu_ns, cpu_hundredths, rss_bytes, ram_available_bytes, device_used_bytes, device_budget_bytes;
};

// A procfs kB value is KiB, not decimal KB. Missing, malformed and overflowing values are unknown.
Reading proc_kib(const char* text, const char* key);
Reading cpu_percent(const Sample& previous, const Sample& current);
bool parse_interval(const char* text, uint32_t& milliseconds);
void memory_text(char* out, size_t size, Reading bytes);
void cpu_text(char* out, size_t size, Reading hundredths);
void csv_header(FILE* file);
bool csv_sample(FILE* file, const Sample& sample);
Sample read_process();
uint64_t monotonic_ns();
FILE* create_csv(const char* utf8_path);   // Exclusive creation; never overwrites a previous capture.

// No clock, reader, writer or allocation on the disabled path. A newly enabled interval starts fresh.
class Sampler {
    bool active = false;
    uint64_t next_ns = 0;
    Sample latest;
public:
    const Sample& sample() const { return latest; }
    template<class Clock, class Read, class Emit>
    bool tick(bool enabled, uint32_t interval_ms, Clock clock, Read read, Emit emit) {
        if (!enabled) {
            if (active) latest = {}, active = false;
            return false;
        }
        const uint64_t now = clock();
        if (!now) return false;   // A failed monotonic-clock read is not a timestamp at zero.
        if (active && now >= latest.monotonic_ns && now < next_ns) return false;
        Sample next = read();
        next.monotonic_ns = now;
        if (active) next.cpu_hundredths = cpu_percent(latest, next);
        latest = next;
        active = true;
        next_ns = now + uint64_t(interval_ms) * 1000000;
        emit(latest);
        return true;
    }
};
} // namespace mkwvk::system_readings
