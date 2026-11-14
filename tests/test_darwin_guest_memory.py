"""Darwin's guest reservation and byte-exact discard, on arm64 and x86-64/Rosetta.

Compiles darwin_mem.c and text-extracted shared memory functions, with real Mach,
mmap, protections and resident-size measurements. No game, renderer or device.
The fixture traps unrelated Windows OOM handling instead of faking a successful
memory call. --source selects an unmodified source tree for the failing-base test.

python -I -B tests/test_darwin_guest_memory.py [--cc clang] [--arch arm64|x86_64]
"""
import argparse
import ast
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "{cxx}"]
RUN_ALL_SKIP = {"windows": "Darwin Mach VM and real host-page semantics"}

ROOT = Path(__file__).resolve().parents[1]


def function(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def prepare(root, out):
    names = ("host/hos.h", "host/hos_main.c", "host/hos_mem.c", "host/hos_svc.c", "host/mkw_glue.c",
             "host/posix/win32_posix.c", "third_party/a64-kit/rt/a64.h", "nx2/rtgen.py")
    sources = {name: (root / name).read_text(encoding="utf-8") for name in names}

    def take(name, marker, until=None):
        source = sources[name]
        at = source.index(marker)
        text = source[at:source.index(until, at)] if until else function(source, marker)
        return '#line %d "%s"\n%s\n' % (source.count("\n", 0, at) + 1, name, text)

    # The real context/address declarations, with rtgen's literal 64-bit pointer
    # definition. No instruction/FP runtime or game-dependent kit import is needed.
    model = sources["third_party/a64-kit/rt/a64.h"]
    model = model[:model.index("/* Browser builds")]
    assignments = ast.parse(sources["nx2/rtgen.py"]).body
    addr64 = next(ast.literal_eval(node.value) for node in assignments
                  if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ADDR64" for t in node.targets))
    old = "#define A64_PTR(a) (A64_MEM + (uint32_t)(a))"
    assert model.count(old) == 1
    (out / "a64.h").write_text(model.replace(old, addr64) + "\n#endif\n", encoding="utf-8")

    posix = sources["host/posix/win32_posix.c"]
    constants = "\n".join(line for line in posix.splitlines() if line.startswith(("#define GUEST_LO ", "#define GUEST_HI ")))
    darwin = root / "host/posix/darwin_mem.c"
    includes = '#include <windows.h>\n#include <dlfcn.h>\n'
    if darwin.is_file():
        includes += '#include "darwin_mem.h"\n'
    (out / "posix_memory.c").write_text(includes + constants + "\n" +
        take("host/posix/win32_posix.c", "static __thread DWORD last_error;", "/* ---- handles ---- */") +
        take("host/posix/win32_posix.c", "static int prot_of(", "SIZE_T VirtualQuery("), encoding="utf-8")

    # This independent, real-memory case also runs against main, where the full
    # reservation fixture cannot compile (MAP_FIXED_NOREPLACE is not a Darwin API).
    direct = includes + constants + "\n" + take("host/posix/win32_posix.c", "static int discard_pages(") + r'''
int main(void) {
    const size_t page = (size_t)getpagesize();
    unsigned char *p = mmap(NULL, 3 * page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (p == MAP_FAILED) return 2;
    memset(p, 0x6d, 3 * page);
    const int rc = discard_pages(p + page, page);
    int zero = 1, neighbors = 1;
    for (size_t i = 0; i < page; i++) {
        if (p[page + i]) zero = 0;
        if (p[i] != 0x6d || p[2 * page + i] != 0x6d) neighbors = 0;
    }
    printf("DISCARD page=%zu rc=%d zero=%d neighbors=%d\n", page, rc, zero, neighbors);
    const int released = munmap(p, 3 * page) == 0;
    return rc || !zero || !neighbors || !released;
}
'''
    (out / "direct_discard.c").write_text(direct, encoding="utf-8")

    guest = r'''
#include <windows.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <inttypes.h>
#include <fcntl.h>
#include <sys/syscall.h>
#include <stdarg.h>
#include <time.h>
#include "hos.h"
#include "longshot_env.h"
'''
    if darwin.is_file():
        guest += '#include "darwin_mem.h"\n'
    guest += r'''
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)
static char last_log[512];
int hos_trace;
void hos_log(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vsnprintf(last_log, sizeof last_log, fmt, ap); va_end(ap);
}
void hos_log_at(int level, const char *fmt, ...) {
    (void)level; va_list ap; va_start(ap, fmt); vsnprintf(last_log, sizeof last_log, fmt, ap); va_end(ap);
}
void hos_fatal(Ctx *c, const char *fmt, ...) {
    (void)c; va_list ap; va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap);
    fputc('\n', stderr); exit(23);
}
int64_t hos_now_ns(void) {
    struct timespec t; CHECK(!clock_gettime(CLOCK_UPTIME_RAW, &t));
    return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static unsigned commit_init_calls, import_checks;
void hos_commit_init(void) { commit_init_calls++; }
int rx_guest_imported(uint64_t lo, uint64_t hi) {
    CHECK(lo <= hi && hi <= HOS_GUEST_SPACE); import_checks++;
    return 0; /* Every fixture range is privately owned; no renderer is loaded. */
}
static int commit_wait(void *p, SIZE_T n) {
    (void)p; (void)n; fprintf(stderr, "FAIL: unexpected Windows commitment-limit retry\n"); exit(1);
}
static void commit_refused(uint64_t lo, uint64_t n, DWORD err) {
    fprintf(stderr, "FAIL: commit refused lo=%" PRIu64 " n=%" PRIu64 " error=%u\n", lo, n, err); exit(1);
}
static uintptr_t committed_at;
static size_t committed_size;
static void *observe_commit(void *p, SIZE_T n, DWORD type, DWORD protect) {
    committed_at = (uintptr_t)p; committed_size = n;
    return VirtualAlloc(p, n, type, protect);
}
#define VirtualAlloc observe_commit
'''
    guest += take("host/mkw_glue.c", "void hos_commit(") + "#undef VirtualAlloc\n"
    guest += "#define GUEST_SPACE HOS_GUEST_SPACE\nstatic int startup_stderr = -1;\nint hos_guest_memfd = -1;\n"
    guest += take("host/hos_main.c", "static int reserve_guest(")
    guest += take("host/hos_main.c", "int hos_posix_guest_discard(")
    guest += take("host/hos_mem.c", "#if defined(_WIN32)", "/* nn::os::QueryMemoryInfo(")
    guest += '\n#undef R_OK\n#define R_OK 0\nstatic void physical_svc(Ctx *c, unsigned svc) {\nswitch (svc) {\n'
    guest += take("host/hos_svc.c", "    case 0x2C:", "    case 0x1F:")
    guest += "default: CHECK(0);\n}\n}\n"
    guest += '#line 1 "tests/darwin_guest_memory_test.c"\n' + (ROOT / "tests/darwin_guest_memory_test.c").read_text(encoding="utf-8")
    (out / "guest_memory.c").write_text(guest, encoding="utf-8")
    return [str(darwin)] if darwin.is_file() else []


def run(root, out, cc, arches):
    helpers = prepare(root, out)
    failures = []
    common = [cc, "-x", "c", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-mmacosx-version-min=14.4",
              "-I", str(out), "-I", str(root / "host/posix"), "-I", str(root / "host"),
              "-I", str(root / "gpu/vk/compat")]
    for arch in arches:
        for case, names in (("direct", ["direct_discard.c"]), ("integrated", ["posix_memory.c", "guest_memory.c"])):
            exe = out / (case + "-" + arch)
            commands = [common + ["-arch", arch, *[str(out / n) for n in names], *helpers, "-o", str(exe)],
                        ["arch", "-" + arch, str(exe)]]
            for stage, cmd in zip(("compile", "run"), commands):
                print("COMMAND", shlex.join(cmd), flush=True)
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                print(result.stdout + result.stderr, end="", flush=True)
                print("RESULT %s %s %s exit=%d" % (arch, case, stage, result.returncode), flush=True)
                if result.returncode:
                    failures.append("%s/%s/%s" % (arch, case, stage))
                    break
    if failures:
        raise SystemExit("FAIL: Darwin guest memory: " + ", ".join(failures))
    print("PASS: Darwin guest memory: " + ", ".join(arches))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cc", default="clang")
    ap.add_argument("--source", type=Path, default=ROOT)
    ap.add_argument("--arch", choices=("arm64", "x86_64"), action="append")
    ap.add_argument("--out", type=Path, help="retain generated fixture sources and binaries in a new directory")
    args = ap.parse_args()
    if sys.platform == "darwin":
        if args.out:
            args.out.mkdir(parents=True, exist_ok=False)
            run(args.source.resolve(), args.out.resolve(), args.cc, args.arch or ["arm64", "x86_64"])
        else:
            with tempfile.TemporaryDirectory(prefix="darwin-mem-") as tmp:
                run(args.source.resolve(), Path(tmp), args.cc, args.arch or ["arm64", "x86_64"])
    else:
        print("SKIP: Darwin guest memory requires macOS Mach VM (Windows and Linux unchanged)")


if __name__ == "__main__":
    main()
