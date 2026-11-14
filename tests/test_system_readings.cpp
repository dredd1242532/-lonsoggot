// Executed by test_system_readings.py without a game, driver or elevated privileges.
#define _CRT_SECURE_NO_WARNINGS
#include "system_readings.h"
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>

namespace sr = mkwvk::system_readings;
static int checks = 0;
#define CHECK(test) do { ++checks; if (!(test)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #test); return 1; } } while (0)

int main(int argc, char** argv) {
    CHECK(argc == 2);
    CHECK(sr::proc_kib("VmRSS:\t123 kB\n", "VmRSS") == 123 * 1024);
    CHECK(sr::proc_kib("MemAvailable: 0 kB\n", "MemAvailable") == 0);
    CHECK(sr::proc_kib("VmRSSExtra: 9 kB\nVmRSS: 42 kB\n", "VmRSS") == 42 * 1024);
    for (const char* text : {"", "VmRSS: -1 kB", "VmRSS: +1 kB", "VmRSS: 1 MB", "VmRSS: 1 kB junk",
                             "VmRSS: 18446744073709551616 kB", "VmRSS: 18446744073709551615 kB", "VmRSS: nan kB"})
        CHECK(!sr::proc_kib(text, "VmRSS"));
    uint32_t interval = 1000;
    for (const char* text : {"", "0", "249", "60001", "1000ms", "-1000", " 1000", "1e3", "999999999999999999999"}) {
        CHECK(!sr::parse_interval(text, interval));
        CHECK(interval == 1000);
    }
    CHECK(sr::parse_interval("250", interval) && interval == 250);
    CHECK(sr::parse_interval("60000", interval) && interval == 60000);

    sr::Sample before, after;
    before.monotonic_ns = 1000000000;
    after.monotonic_ns = 2000000000;
    before.cpu_ns = 500000000;
    after.cpu_ns = 3000000000;
    CHECK(sr::cpu_percent(before, after) == 25000);  // 250% of one logical CPU, not 2.5% of a PC.
    after.cpu_ns = before.cpu_ns;
    CHECK(sr::cpu_percent(before, after) == 0);
    after.cpu_ns = 1;
    CHECK(!sr::cpu_percent(before, after));
    after.cpu_ns.reset();
    CHECK(!sr::cpu_percent(before, after));
    after.cpu_ns = before.cpu_ns;
    after.monotonic_ns = before.monotonic_ns;
    CHECK(!sr::cpu_percent(before, after));
    after.monotonic_ns = 0;
    CHECK(!sr::cpu_percent(before, after));

    char text[64];
    sr::memory_text(text, sizeof text, {});
    CHECK(!std::strcmp(text, "n/a"));
    sr::memory_text(text, sizeof text, 0);
    CHECK(!std::strcmp(text, "0 MiB"));
    sr::memory_text(text, sizeof text, 3 * 1048576ull + 123);
    CHECK(!std::strcmp(text, "3 MiB"));
    sr::cpu_text(text, sizeof text, 25001);
    CHECK(!std::strcmp(text, "250.01%"));
    sr::cpu_text(text, sizeof text, {});
    CHECK(!std::strcmp(text, "n/a"));

    sr::Sampler sampler;
    int clocks = 0, reads = 0, writes = 0;
    uint64_t now = 1000000000, cpu = 1000000;
    auto clock = [&] { ++clocks; return now; };
    auto read = [&] { ++reads; sr::Sample s; s.cpu_ns = cpu; return s; };
    auto emit = [&](const sr::Sample&) { ++writes; };
    for (int i = 0; i < 1000; i++) CHECK(!sampler.tick(false, 1000, clock, read, emit));
    CHECK(clocks == 0 && reads == 0 && writes == 0);
    CHECK(sampler.tick(true, 1000, clock, read, emit));
    CHECK(!sampler.sample().cpu_hundredths);
    now += 999999999;
    CHECK(!sampler.tick(true, 1000, clock, read, emit));
    CHECK(reads == 1 && writes == 1);
    ++now;
    cpu += 2000000000;
    CHECK(sampler.tick(true, 1000, clock, read, emit));
    CHECK(sampler.sample().cpu_hundredths == 20000);
    now += 10000000000;
    CHECK(sampler.tick(true, 1000, clock, read, emit));
    CHECK(reads == 3 && writes == 3);  // No catch-up burst for missed intervals.
    const int last_clocks = clocks;
    CHECK(!sampler.tick(false, 1000, clock, read, emit));
    CHECK(clocks == last_clocks && reads == 3 && writes == 3);
    CHECK(sampler.tick(true, 1000, clock, read, emit));
    CHECK(!sampler.sample().cpu_hundredths);  // Re-enable never compares a stale interval.
    now = 0;
    CHECK(!sampler.tick(true, 1000, clock, read, emit));
    CHECK(reads == 4 && writes == 4);

    FILE* f = sr::create_csv(argv[1]);
    CHECK(f);
    sr::csv_header(f);
    sr::Sample sample;
    sample.monotonic_ns = 1234567890;
    CHECK(sr::csv_sample(f, sample));
    sample.cpu_hundredths = 25001;
    sample.rss_bytes = 3145728;
    sample.ram_available_bytes = 0; // A real zero remains distinct from unknown.
    sample.device_used_bytes = 123;
    sample.device_budget_bytes = 456;
    CHECK(sr::csv_sample(f, sample));
    CHECK(std::fclose(f) == 0);
    CHECK(sr::create_csv(argv[1]) == nullptr); // No clobber, including after an earlier capture closes.
    const sr::Sample native = sr::read_process();
    CHECK(sr::monotonic_ns() > 0);
    // The OS may refuse a reading. Never turn that refusal into a fabricated zero/pass.
    std::printf("Native availability: cpu=%d rss=%d ram=%d\n", int(native.cpu_ns.has_value()),
                int(native.rss_bytes.has_value()), int(native.ram_available_bytes.has_value()));
    std::printf("PASS: %d system-reading checks; disabled path clocks=0 reads=0 writes=0\n", checks);
    return 0;
}
