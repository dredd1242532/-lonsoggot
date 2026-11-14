"""Low physical RAM on Linux must warn even when swap makes the commit limit large.

Extract the production warning and boot call. Authored memory-query and UI spies
exercise the exact 7639 MiB RAM + 32767 MiB swap report, boundaries, query failure,
unit conversion and headless behavior. Windows retains its RAM + page-file rule.
On Linux, a second build selects the production Windows policy after the host
headers are included; its memory query and UI remain spies, not Windows runtime.
On macOS, the real <sys/sysctl.h> struct xsw_usage and an authored sysctlbyname
report hw.memsize and vm.swapusage in bytes: the same cases under macOS's
physical-RAM rule, its dynamic-swap wording and boot log, byte-to-MiB truncation,
a failed RAM query's diagnostic, and a failed swap query that still decides and
shows the RAM notice without claiming a swap figure. --arch builds and runs one macOS
architecture (x86_64 under Rosetta).

    python -I -B tests/test_low_memory_notice.py [--cc gcc] [--source host/hos_main.c] [--arch arm64|x86_64]
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    if text.count(marker) != 1:
        raise SystemExit("marker not found once: " + marker)
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


FIXTURE = r"""
#define _CRT_SECURE_NO_WARNINGS
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "longshot_env.h"
/* Preserve the real host's environment API before selecting a policy. No
   production condition is changed: _WIN32 selects its existing Windows path. */
#ifdef _WIN32
#define TEST_NATIVE_WINDOWS
#endif
#if defined(TEST_WINDOWS_POLICY) && !defined(_WIN32)
#define _WIN32 1
#endif
static unsigned long long ram_mb = 7639, swap_mb = 32767;
static int query_failed, boxes;
#ifndef _WIN32
static unsigned mem_unit = 1;
static int sdl_loads;
#endif
static char last_log[1024], box_text[512];
#ifdef _WIN32
/* The notice only reads these fields. A union leaves its sizeof initializer
   warning-free without replacing the function under test. */
typedef union {
    unsigned long dwLength;
    struct { unsigned long long ullTotalPhys, ullTotalPageFile; } sizes;
} MEMORYSTATUSEX;
#define ullTotalPhys sizes.ullTotalPhys
#define ullTotalPageFile sizes.ullTotalPageFile
static int GlobalMemoryStatusEx(MEMORYSTATUSEX *m) {
    m->ullTotalPhys = ram_mb << 20;
    m->ullTotalPageFile = (ram_mb + swap_mb) << 20;
    return !query_failed;
}
#elif defined(__APPLE__)
/* macOS reports bytes through sysctl. The real header gives struct xsw_usage's
   layout; the spy accepts only the reads production should make. */
#include <errno.h>
#include <stdint.h>
#include <sys/sysctl.h>
static unsigned long long byte_slack;   /* (bytes short of the next MiB: the units check) */
static int bad_queries;
static char logs[4096];   /* (every boot line of one notice, not only the last) */
static int spy_sysctlbyname(const char *name, void *old, size_t *oldlen, void *new_value, size_t new_len) {
    if (!name || !old || !oldlen || new_value || new_len) return ++bad_queries, errno = EINVAL, -1;
    if (!strcmp(name, "hw.memsize") && *oldlen == sizeof(uint64_t)) {
        if (query_failed == 1) return errno = EPERM, -1;
        const uint64_t bytes = (ram_mb << 20) + byte_slack;
        memcpy(old, &bytes, sizeof bytes);
        return 0;
    }
    if (!strcmp(name, "vm.swapusage") && *oldlen == sizeof(struct xsw_usage)) {
        if (query_failed == 2) return errno = ENOMEM, -1;
        struct xsw_usage usage;
        memset(&usage, 0, sizeof usage);
        usage.xsu_total = (swap_mb << 20) + byte_slack;
        usage.xsu_used = usage.xsu_total / 4;   /* (distinct from the total: the notice reports allocated swap) */
        usage.xsu_avail = usage.xsu_total - usage.xsu_used;
        memcpy(old, &usage, sizeof usage);
        return 0;
    }
    return ++bad_queries, errno = ENOENT, -1;
}
#define sysctlbyname spy_sysctlbyname
#else
struct sysinfo { unsigned long totalram, totalswap; unsigned mem_unit; };
static int sysinfo(struct sysinfo *m) {
    m->totalram = (ram_mb << 20) / mem_unit;
    m->totalswap = (swap_mb << 20) / mem_unit;
    m->mem_unit = mem_unit;
    return query_failed;
}
#endif
#define MB_OK 0
#define MB_ICONWARNING 0x30
static void hos_log(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    vsnprintf(last_log, sizeof last_log, fmt, a);
    va_end(a);
#if defined(__APPLE__)
    snprintf(logs + strlen(logs), sizeof logs - strlen(logs), "%s\n", last_log);
#endif
}
static int MessageBoxA(void *w, const char *text, const char *caption, unsigned type) {
    (void)w, (void)caption, (void)type;
    snprintf(box_text, sizeof box_text, "%s", text);
    return ++boxes;
}
#ifndef _WIN32
int hos_sdl_load(void) { ++sdl_loads; return 0; }
#endif
static void headless(const char *value) {
#ifdef TEST_NATIVE_WINDOWS
    _putenv_s("HOS_HEADLESS", value);
#else
    setenv("HOS_HEADLESS", value, 1);
#endif
}
#define CHECK(c, why) do { if (!(c)) return printf("FAIL: %s\n", why), 1; } while (0)
@@CODE@@
int main(void) {
    static const unsigned long long cases[][2] = {
        {7639, 32767}, {8192, 0}, {8192, 2047}, {8192, 2048},
        {10239, 0}, {10239, 32768}, {10240, 0}, {16384, 0}, {98304, 32768}};
    for (size_t i = 0; i < sizeof cases / sizeof *cases; ++i) {
        char text[512] = "";
        ram_mb = cases[i][0], swap_mb = cases[i][1];
#if defined(__linux__) && !defined(_WIN32)
        const int expected = ram_mb < 10240;
#elif defined(__APPLE__)
        const int expected = ram_mb < 10240;   /* (macOS too: physical RAM alone; swap is allocated as needed) */
#else
        const int expected = ram_mb + swap_mb < 10240;
#endif
        int got = memory_notice_text(ram_mb, swap_mb, text, sizeof text);
        CHECK(got == expected, "wrong warning decision (swap must not hide low RAM on Linux)");
        if (got) {
#if defined(__linux__) && !defined(_WIN32)
            CHECK(strstr(text, "The game starts anyway."), "Linux warning does not explain that startup continues");
            CHECK(strstr(text, "unresponsive") && strstr(text, "not a replacement for RAM"), "missing paging caution");
            CHECK(strstr(text, "16 GB") && strstr(text, "13 GiB"), "missing RAM guidance");
#elif defined(__APPLE__)
            CHECK(strstr(text, "The game starts anyway."), "macOS warning does not explain that startup continues");
            CHECK(strstr(text, "This Mac has") && strstr(text, "allocated swap") &&
                  strstr(text, "not a fixed memory limit") && strstr(text, "unresponsive"), "missing macOS swap caution");
            CHECK(strstr(text, "16 GB") && strstr(text, "13 GiB"), "missing RAM guidance");
            CHECK(!strstr(text, "the page file on Windows") && !strstr(text, "This PC"), "Windows or Linux guidance in the macOS warning");
#else
            CHECK(strstr(text, "It starts now anyway."), "legacy warning does not explain that startup continues");
            CHECK(strstr(text, "the page file on Windows") && strstr(text, "two together"), "missing commit-limit guidance");
            CHECK(!strstr(text, "unresponsive") && !strstr(text, "not a replacement for RAM") &&
                  !strstr(text, "13 GiB"), "Linux-only guidance in the legacy warning");
#endif
        } else {
            CHECK(!text[0], "a no-warning case wrote warning text");
        }
        for (int quiet = 0; quiet < 2; ++quiet) {
            boxes = 0; last_log[0] = box_text[0] = 0;
#ifndef _WIN32
            sdl_loads = 0;
#endif
#if defined(__APPLE__)
            logs[0] = 0;
#endif
            headless(quiet ? "1" : "0");
            memory_notice();
#if defined(__APPLE__)
            CHECK(!bad_queries, "production asked sysctl for another name or size, or wrote to it");
#endif
            CHECK(boxes == (expected && !quiet), "wrong message-box count");
            CHECK((strstr(last_log, "LOW MEMORY") != NULL) == expected, "wrong boot log decision");
            CHECK(strstr(last_log, "MB of memory") && strstr(last_log, "MB of swap"), "missing exact boot counts");
            if (boxes) CHECK(!strcmp(box_text, text), "message differs from the warning text");
#ifndef _WIN32
            CHECK(sdl_loads == boxes, "SDL loaded without showing a warning");
#endif
#if defined(__APPLE__)
            CHECK(strstr(logs, "boot: macOS manages swap dynamically; the swap figure is currently allocated, not a fixed commit limit\n"),
                  "macOS boot log does not qualify the allocated swap");
#endif
        }
        printf("PASS: %llu MiB RAM + %llu MiB swap, warning=%d, visible/headless\n", ram_mb, swap_mb, expected);
    }
    ram_mb = 7639, swap_mb = 32767;
#ifndef _WIN32
    mem_unit = 4096;
#endif
#if defined(__APPLE__)
    byte_slack = (1ull << 20) - 1;
#endif
    headless("1"); memory_notice();
    CHECK(strstr(last_log, "7639 MB of memory, 32767 MB of swap"), "memory-query units changed");
    query_failed = 1; last_log[0] = 0; boxes = 0;
#if defined(__APPLE__)
    logs[0] = 0;
#endif
    memory_notice();
#if defined(__APPLE__)
    /* (macOS logs why it cannot tell, and still invents no warning) */
    CHECK(strstr(logs, "boot: cannot query physical RAM on macOS (") && strstr(logs, strerror(EPERM)), "failed RAM query: no diagnostic");
    CHECK(!strstr(logs, "LOW MEMORY") && !strstr(logs, "MB of memory") && !boxes, "failed RAM query invents a warning");
    /* (vm.swapusage failing: physical RAM alone still decides and shows the notice; neither the notice nor a boot
       line claims a swap figure, and the reason is logged) */
    query_failed = 2;
    for (size_t i = 0; i < sizeof cases / sizeof *cases; ++i) {
        if (i && cases[i][0] == cases[i - 1][0]) continue;
        char text[512] = "", opening[128], reason[256], counts[96];
        ram_mb = cases[i][0];
        const int expected = ram_mb < 10240;
        CHECK(memory_notice_text(ram_mb, UINT64_MAX, text, sizeof text) == expected, "unknown swap changed the RAM decision");
        if (expected) {
            snprintf(opening, sizeof opening, "This Mac has %.1f GiB of RAM; its allocated swap could not be read. ", ram_mb / 1024.0);
            CHECK(!strncmp(text, opening, strlen(opening)), "unknown swap: the notice does not say the swap figure is missing");
            CHECK(!strstr(text, "GiB of allocated swap"), "unknown swap: the notice claims a swap figure");
            CHECK(strstr(text, "16 GB") && strstr(text, "13 GiB") && strstr(text, "unresponsive") &&
                  strstr(text, "The game starts anyway."), "unknown swap: the notice lost its guidance");
        } else {
            CHECK(!text[0], "unknown swap: a no-warning case wrote warning text");
        }
        snprintf(reason, sizeof reason, "boot: %llu MB of RAM; allocated swap is unavailable (%s)\n", ram_mb, strerror(ENOMEM));
        snprintf(counts, sizeof counts, "%llu MB of memory, allocated swap unavailable", ram_mb);
        for (int quiet = 0; quiet < 2; ++quiet) {
            boxes = 0, sdl_loads = 0; last_log[0] = box_text[0] = logs[0] = 0;
            headless(quiet ? "1" : "0");
            memory_notice();
            CHECK(strstr(logs, reason), "failed swap query: no diagnostic with the RAM it read");
            CHECK(boxes == (expected && !quiet), "failed swap query: wrong message-box count (physical RAM alone decides)");
            CHECK(sdl_loads == boxes, "failed swap query: SDL loaded without showing a warning");
            CHECK((strstr(logs, "LOW MEMORY") != NULL) == expected, "failed swap query: wrong boot log decision");
            CHECK(strstr(logs, counts), "failed swap query: the boot counts do not say swap is unavailable");
            CHECK(!strstr(logs, "MB of swap") && !strstr(logs, "macOS manages swap dynamically"),
                  "failed swap query: a boot line claims a swap figure");
            if (boxes) CHECK(!strcmp(box_text, text), "failed swap query: message differs from the warning text");
        }
        printf("PASS: %llu MiB RAM, swap query failed, warning=%d, visible/headless\n", ram_mb, expected);
    }
    CHECK(!bad_queries, "production asked sysctl for another name or size, or wrote to it");
#else
    CHECK(!last_log[0] && !boxes, "failed memory query invents a warning");
#endif
    puts("PASS: memory-query units and failure");
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--source", type=Path, default=ROOT / "host" / "hos_main.c")
    parser.add_argument("--arch", choices=("arm64", "x86_64"))
    args = parser.parse_args()
    if args.arch and sys.platform != "darwin":
        parser.error("--arch is macOS-only")
    src = args.source.read_text(encoding="utf-8")
    boot = src.index('hos_log("boot: image %zu MB')
    call = src.find("memory_notice();", boot)
    if call < 0 or call > src.index("hos_mods_init(image);", boot):
        raise SystemExit("FAIL: the game does not check its memory at boot")
    if "#include <sys/sysinfo.h>" not in src:
        raise SystemExit("FAIL: host/hos_main.c reads Linux's memory without <sys/sysinfo.h>")
    if "#include <sys/sysctl.h>" not in src:
        raise SystemExit("FAIL: host/hos_main.c reads macOS's memory without <sys/sysctl.h>")
    define = next(line for line in src.splitlines() if line.startswith("#define HOS_MEMORY_NEEDED_MB"))
    code = "\n".join((define, block(src, "static int memory_notice_text("), block(src, "static void memory_notice(void) {")))
    variants = [("native", [])]
    if sys.platform.startswith("linux"):
        variants.append(("windows-policy-on-linux", ["-DTEST_WINDOWS_POLICY"]))
    with tempfile.TemporaryDirectory(prefix="low-memory-") as tmp:
        for label, flags in variants:
            c, exe = Path(tmp) / (label + ".c"), Path(tmp) / (label + ".exe")
            c.write_text(FIXTURE.replace("@@CODE@@", code), encoding="utf-8")
            command = [args.cc, "-x", "c", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O1", *flags,
                       "-I", str(ROOT / "gpu" / "vk" / "compat"), str(c), "-o", str(exe)]
            if sys.platform == "darwin":
                command += ["-mmacosx-version-min=14.4"]
                if args.arch:
                    command += ["-arch", args.arch]
            r = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if r.returncode:
                print(r.stdout + r.stderr)
                raise SystemExit("the fixture did not build: " + label)
            run = (["arch", "-" + args.arch] if args.arch else []) + [str(exe)]
            r = subprocess.run(run, capture_output=True, text=True, timeout=30)
            print(label + (" (not Windows execution)" if flags else "") + (" [%s]" % args.arch if args.arch else "") + ":")
            print(r.stdout, end="")
            if r.returncode:
                raise SystemExit("FAIL: memory_notice " + label)
    print("PASS: production low-memory decisions, exact counts, message, headless and boot wiring")


if __name__ == "__main__":
    main()
