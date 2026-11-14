// Trimmed from the local sampled monitor (31ad1f5da): strict procfs values, CPU deltas,
// locale-independent CSV and exclusive UTF-8 output. No vendor probes or thread enumeration.
#define _CRT_SECURE_NO_WARNINGS
#include "system_readings.h"

#include <charconv>
#include <cstring>
#include <filesystem>
#include <limits>
#include <string_view>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <psapi.h>
#include <fcntl.h>
#include <io.h>
#include <share.h>
#include <sys/stat.h>
#else
#include <fcntl.h>
#include <time.h>
#include <unistd.h>
#endif

namespace mkwvk::system_readings {
namespace {
std::string_view trim(std::string_view s) {
    const auto first = s.find_first_not_of(" \t\r\n");
    if (first == s.npos) return {};
    return s.substr(first, s.find_last_not_of(" \t\r\n") - first + 1);
}
std::string_view token(std::string_view& s) {
    s = trim(s);
    const auto end = s.find_first_of(" \t\r\n");
    const auto result = s.substr(0, end);
    s = end == s.npos ? std::string_view{} : s.substr(end);
    return result;
}
bool unsigned_number(std::string_view text, uint64_t& value) {
    text = trim(text);
    if (text.empty()) return false;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    return result.ec == std::errc{} && result.ptr == text.data() + text.size();
}
#ifndef _WIN32
Reading proc_file(const char* path, const char* key) {
    FILE* f = std::fopen(path, "r");
    if (!f) return {};
    char line[512];
    Reading result;
    while (std::fgets(line, sizeof line, f)) {
        // Never accept a prefix of a truncated line.
        if (!std::strchr(line, '\n') && !std::feof(f)) {
            int c;
            while ((c = std::fgetc(f)) != EOF && c != '\n') {}
            continue;
        }
        if (auto value = proc_kib(line, key)) { result = value; break; }
    }
    if (std::ferror(f)) result.reset();
    std::fclose(f);
    return result;
}
#endif
bool csv_value(FILE* f, Reading value) {
    if (!value) return std::fputs("na", f) != EOF;
    char text[32];
    auto converted = std::to_chars(text, text + sizeof text, *value);
    return converted.ec == std::errc{} &&
        std::fwrite(text, 1, size_t(converted.ptr - text), f) == size_t(converted.ptr - text);
}
} // namespace

Reading proc_kib(const char* text, const char* key) {
    std::string_view remaining(text), wanted(key);
    while (!remaining.empty()) {
        const auto end = remaining.find('\n');
        auto line = trim(remaining.substr(0, end));
        remaining = end == remaining.npos ? std::string_view{} : remaining.substr(end + 1);
        if (!line.starts_with(wanted) || line.size() <= wanted.size() || line[wanted.size()] != ':') continue;
        line.remove_prefix(wanted.size() + 1);
        uint64_t number = 0;
        if (!unsigned_number(token(line), number) || token(line) != "kB" || !trim(line).empty() ||
            number > std::numeric_limits<uint64_t>::max() / 1024) return {};
        return number * 1024;
    }
    return {};
}
Reading cpu_percent(const Sample& previous, const Sample& current) {
    if (!previous.cpu_ns || !current.cpu_ns || current.monotonic_ns <= previous.monotonic_ns ||
        *current.cpu_ns < *previous.cpu_ns) return {};
    // CPU 100% is one logical CPU. Round to hundredths, without integer multiplication overflow.
    const double value = double(*current.cpu_ns - *previous.cpu_ns) * 10000.0 /
                         double(current.monotonic_ns - previous.monotonic_ns);
    if (value >= double(std::numeric_limits<uint64_t>::max())) return {};
    return uint64_t(value + 0.5);
}
bool parse_interval(const char* text, uint32_t& milliseconds) {
    if (!text || !*text) return false;
    unsigned n = 0;
    for (const char* p = text; *p; ++p) {
        if (*p < '0' || *p > '9') return false;
        unsigned digit = unsigned(*p - '0');
        if (n > (60000 - digit) / 10) return false;
        n = n * 10 + digit;
    }
    if (n < 250) return false;
    milliseconds = n;
    return true;
}
void memory_text(char* out, size_t size, Reading bytes) {
    if (bytes) std::snprintf(out, size, "%llu MiB", (unsigned long long)(*bytes >> 20));
    else std::snprintf(out, size, "n/a");
}
void cpu_text(char* out, size_t size, Reading hundredths) {
    if (hundredths) std::snprintf(out, size, "%llu.%02llu%%", (unsigned long long)(*hundredths / 100),
                                (unsigned long long)(*hundredths % 100));
    else std::snprintf(out, size, "n/a");
}
void csv_header(FILE* file) {
    std::fputs("# longshot_system,1\nmonotonic_ns,cpu_process_pct,rss_bytes,ram_available_bytes,vram_used_bytes,vram_budget_bytes\n", file);
}
bool csv_sample(FILE* file, const Sample& s) {
    if (!csv_value(file, s.monotonic_ns) || std::fputc(',', file) == EOF) return false;
    if (s.cpu_hundredths) {
        if (std::fprintf(file, "%llu.%02llu", (unsigned long long)(*s.cpu_hundredths / 100),
                         (unsigned long long)(*s.cpu_hundredths % 100)) < 0) return false;
    } else if (std::fputs("na", file) == EOF) return false;
    for (Reading value : {s.rss_bytes, s.ram_available_bytes, s.device_used_bytes, s.device_budget_bytes})
        if (std::fputc(',', file) == EOF || !csv_value(file, value)) return false;
    return std::fputc('\n', file) != EOF && std::fflush(file) == 0 && std::ferror(file) == 0;
}
Sample read_process() {
    Sample s;
#ifdef _WIN32
    FILETIME birth{}, exit{}, kernel{}, user{};
    if (GetProcessTimes(GetCurrentProcess(), &birth, &exit, &kernel, &user)) {
        const auto ticks = [](FILETIME t) { return (uint64_t(t.dwHighDateTime) << 32) | t.dwLowDateTime; };
        const uint64_t k = ticks(kernel), u = ticks(user), max = std::numeric_limits<uint64_t>::max() / 100;
        if (k <= max && u <= max - k) s.cpu_ns = (k + u) * 100;
    }
    PROCESS_MEMORY_COUNTERS_EX memory{};
    memory.cb = sizeof memory;
    if (K32GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&memory), sizeof memory))
        s.rss_bytes = memory.WorkingSetSize;
    MEMORYSTATUSEX system{};
    system.dwLength = sizeof system;
    if (GlobalMemoryStatusEx(&system)) s.ram_available_bytes = system.ullAvailPhys;
#else
    struct timespec cpu{};
    if (!clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &cpu) && cpu.tv_sec >= 0)
        s.cpu_ns = uint64_t(cpu.tv_sec) * 1000000000 + uint64_t(cpu.tv_nsec);
    s.rss_bytes = proc_file("/proc/self/status", "VmRSS");
    s.ram_available_bytes = proc_file("/proc/meminfo", "MemAvailable");
#endif
    return s;
}
uint64_t monotonic_ns() {
#ifdef _WIN32
    LARGE_INTEGER count{}, frequency{};
    if (!QueryPerformanceCounter(&count) || !QueryPerformanceFrequency(&frequency) || frequency.QuadPart <= 0) return 0;
    const uint64_t n = uint64_t(count.QuadPart), f = uint64_t(frequency.QuadPart);
    return (n / f) * 1000000000 + (n % f) * 1000000000 / f;
#else
    struct timespec now{};
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return 0;
    return uint64_t(now.tv_sec) * 1000000000 + uint64_t(now.tv_nsec);
#endif
}
FILE* create_csv(const char* utf8_path) {
    const std::filesystem::path path(reinterpret_cast<const char8_t*>(utf8_path));
    int fd = -1;
#ifdef _WIN32
    if (_wsopen_s(&fd, path.c_str(), _O_CREAT | _O_EXCL | _O_WRONLY | _O_BINARY,
                  _SH_DENYNO, _S_IREAD | _S_IWRITE) != 0) return nullptr;
    FILE* file = _fdopen(fd, "wb");
    if (!file) _close(fd);
#else
    fd = open(path.c_str(), O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return nullptr;
    FILE* file = fdopen(fd, "wb");
    if (!file) close(fd);
#endif
    return file;
}
} // namespace mkwvk::system_readings
