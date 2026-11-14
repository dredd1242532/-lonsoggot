"""Darwin's canonical guest memory, native-page watches and import lifetime.

Compiles real Mach VM helpers and source-extracted callers. Two independent
dylibs resolve the executable's canonical guest services. No game or GPU runs.
All memory operations are real; wrappers supply deterministic barriers only.
--watch-protection-only uses the actual Win32 vectored dispatch and real Mach
maximum-permission failures. --behavior-only carries prior RSS experiments;
the ordinary test retains its reclaim check.

python -I -B tests/test_darwin_guest_memory.py [--cc clang] [--arch arm64|x86_64]
The default is native; both --arch options explicitly require Rosetta.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import platform
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
             "host/rx_backend.c", "host/nvn_native.c", "host/posix/win32_posix.c",
             "host/posix/darwin_mem.c", "host/posix/darwin_mem.h", "host/posix/darwin_guest_mem.c",
             "host/posix/darwin_fault.c", "host/posix/darwin_fault.h", "host/posix/windows.h",
             "third_party/a64-kit/rt/a64.h", "nx2/rtgen.py")
    sources = {name: (root / name).read_text(encoding="utf-8") for name in names}

    def take(name, marker, until=None):
        source = sources[name]
        at = source.index(marker)
        text = source[at:source.index(until, at)] if until else function(source, marker)
        return '#line %d "%s"\n%s\n' % (source.count("\n", 0, at) + 1, name, text)

    # Public runtime declarations and the literal 64-bit pointer/dirty definitions
    # from rtgen, not any game-generated code or instruction runtime.
    model = sources["third_party/a64-kit/rt/a64.h"]
    model = model[:model.index("/* Browser builds")]
    assignments = ast.parse(sources["nx2/rtgen.py"]).body

    def literal(name):
        return next(ast.literal_eval(node.value) for node in assignments
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets))

    old = "#define A64_PTR(a) (A64_MEM + (uint32_t)(a))"
    assert model.count(old) == 1
    (out / "a64.h").write_text(model.replace(old, literal("ADDR64")) + "\n" + literal("MARK_NATIVE_64") + "\n#endif\n", encoding="utf-8")

    posix = sources["host/posix/win32_posix.c"]
    constants = "\n".join(line for line in posix.splitlines() if line.startswith(("#define GUEST_LO ", "#define GUEST_HI ")))
    includes = '#include <windows.h>\n#include <dlfcn.h>\n#include "darwin_mem.h"\n'
    (out / "posix_memory.c").write_text(includes + constants + "\n" +
        take("host/posix/win32_posix.c", "static __thread DWORD last_error;", "/* ---- handles ---- */") +
        take("host/posix/win32_posix.c", "static int prot_of(", "/* ---- modules ---- */"), encoding="utf-8")

    # Missing-provider refusal is real too: the direct executable has no guest
    # registry, although it and each dylib contain the shared memory primitives.
    direct = includes + constants + r'''
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    const size_t page = (size_t)getpagesize();
    unsigned char *p = darwin_mem_map(NULL, 3 * page, PROT_READ | PROT_WRITE);
    if (p == MAP_FAILED) return 2;
    memset(p, 0x6d, 3 * page);
    const int rc = VirtualFree(p + page, page, MEM_DECOMMIT) ? 0 : -1;
    int zero = 1, neighbors = 1;
    for (size_t i = 0; i < page; i++) {
        if (p[page + i]) zero = 0;
        if (p[i] != 0x6d || p[2 * page + i] != 0x6d) neighbors = 0;
    }
    printf("DISCARD page=%zu rc=%d zero=%d neighbors=%d\n", page, rc, zero, neighbors);
    if (rc || !zero || !neighbors || munmap(p, 3 * page)) return 1;
    p = darwin_mem_map((void *)GUEST_LO, page, PROT_READ | PROT_WRITE);
    if (p == MAP_FAILED) return 2;
    memset(p, 0x5a, page);
    unsigned refused = 0;
    for (int i = 1; i <= 2; i++) {
        void *lib = dlopen(argv[i], RTLD_NOW | RTLD_LOCAL);
        if (!lib) { fprintf(stderr, "%s\n", dlerror()); return 2; }
        BOOL (*release)(void *, SIZE_T, DWORD) = (BOOL (*)(void *, SIZE_T, DWORD))dlsym(lib, "VirtualFree");
        void *(*commit)(void *, SIZE_T, DWORD, DWORD) = (void *(*)(void *, SIZE_T, DWORD, DWORD))dlsym(lib, "VirtualAlloc");
        BOOL (*protect)(void *, SIZE_T, DWORD, DWORD *) = (BOOL (*)(void *, SIZE_T, DWORD, DWORD *))dlsym(lib, "VirtualProtect");
        if (!release || !commit || !protect) return 2;
        DWORD old = 0;
        refused += !release(p, page, MEM_DECOMMIT);
        refused += commit(p, page, MEM_COMMIT, PAGE_READWRITE) == NULL;
        refused += !protect(p, page, PAGE_READONLY, &old);
        for (size_t j = 0; j < page; j++) if (p[j] != 0x5a) return 1;
        if (dlclose(lib)) return 2;
    }
    printf("MISSING_PROVIDER images=2 refused=%u expected=6 bytes=preserved\n", refused);
    return refused != 6 || munmap(p, page);
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
#include <dlfcn.h>
#include <setjmp.h>
#include "hos.h"
#include "longshot_env.h"
#include "darwin_mem.h"
#include "darwin_fault.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)
static char last_log[512];
uint8_t a64_dirty[A64_DIRTY_PAGES];
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
int64_t darwin_now_ns(void) { return hos_now_ns(); }
static unsigned commit_init_calls, import_checks;
void hos_commit_init(void) { commit_init_calls++; }
int rx_guest_imported(uint64_t lo, uint64_t hi) {
    (void)lo; (void)hi; import_checks++;
    fprintf(stderr, "FAIL: querying a renderer is not an import lifetime pin\n"); exit(1);
}
static uintptr_t committed_at;
static size_t committed_size;
static void *observe_commit(void *p, SIZE_T n, DWORD type, DWORD protect) {
    committed_at = (uintptr_t)p; committed_size = n;
    return VirtualAlloc(p, n, type, protect);
}
#define VirtualAlloc observe_commit
'''
    guest += take("host/mkw_glue.c", "int hos_commit(") + "#undef VirtualAlloc\n"
    guest += "#define GUEST_SPACE HOS_GUEST_SPACE\nstatic int startup_stderr = -1;\nint hos_guest_memfd = -1;\n"
    guest += take("host/hos_main.c", "static int reserve_guest(")
    guest += take("host/hos_mem.c", "#if defined(_WIN32)", "/* nn::os::QueryMemoryInfo(")
    guest += '\n#undef R_OK\n#define R_OK 0\nstatic void physical_svc(Ctx *c, unsigned svc) {\nswitch (svc) {\n'
    guest += take("host/hos_svc.c", "    case 0x2C:", "    case 0x1F:")
    guest += "default: CHECK(0);\n}\n}\n"
    guest += r'''
uint64_t hos_guest_size(void) { return HOS_GUEST_SPACE; }
HosThread *hos_self(void) { return NULL; } /* The fixture runs no guest thread. */
const char *hos_thread_name(HosThread *t) { (void)t; return "fixture"; }
static struct { uintptr_t addr, pc; char name[64]; unsigned ready; } wts_events[12];
static unsigned wts_reports;
static int wts_all_threads = 1;
static ULONGLONG wts_after;
'''
    guest += take("host/nvn_native.c", "static LONG CALLBACK wts_handler(")
    guest += take("host/posix/win32_posix.c", "/* ---- exceptions ---- */", "/* ---- the window's state")
    guest += take("host/rx_backend.c", "static inline int marks_clear(")
    guest += take("host/rx_backend.c", "static int64_t collect_in(")
    guest += take("host/rx_backend.c", "static int64_t host_collect_in(")
    guest += r'''
static int observed_discard(void *addr, size_t size);
static int observed_protect(void *addr, size_t size, int protect);
#define darwin_mem_discard observed_discard
#define darwin_mem_protect observed_protect
'''
    guest += '#include "%s"\n#undef darwin_mem_discard\n#undef darwin_mem_protect\n' % (root / "host/posix/darwin_guest_mem.c")
    guest += '#line 1 "tests/darwin_guest_memory_test.c"\n' + (ROOT / "tests/darwin_guest_memory_test.c").read_text(encoding="utf-8")
    (out / "guest_memory.c").write_text(guest, encoding="utf-8")
    manifest = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    for name in ("tests/test_darwin_guest_memory.py", "tests/darwin_guest_memory_test.c"):
        manifest[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    manifest["function:darwin_mem_discard"] = hashlib.sha256(function(sources["host/posix/darwin_mem.c"], "int darwin_mem_discard(").encode()).hexdigest()
    for name, marker in (("host/posix/darwin_guest_mem.c", "int hos_posix_guest_protect("),
                         ("host/posix/darwin_guest_mem.c", "int hos_posix_guest_watch_fault("),
                         ("host/posix/win32_posix.c", "static void on_signal("),
                         ("host/posix/win32_posix.c", "void *AddVectoredExceptionHandler(")):
        manifest["function:" + marker] = hashlib.sha256(function(sources[name], marker).encode()).hexdigest()
    (out / "source-hashes.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return [str(root / "host/posix/darwin_mem.c")], str(root / "host/posix/darwin_fault.c")


def run(root, out, cc, arches, behavior_only, watch_protection_only):
    helpers, fault = prepare(root, out)
    failures = []
    common = [cc, "-x", "c", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-mmacosx-version-min=14.4",
              "-DA64_WRITE_TRACK=1", "-I", str(out), "-I", str(root / "host/posix"), "-I", str(root / "host"),
              "-I", str(root / "gpu/vk/compat")]

    def command(arch, case, stage, cmd):
        print("COMMAND", shlex.join(cmd), flush=True)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        print(result.stdout + result.stderr, end="", flush=True)
        print("RESULT %s %s %s exit=%d" % (arch, case, stage, result.returncode), flush=True)
        if result.returncode:
            failures.append("%s/%s/%s" % (arch, case, stage))
        return result.returncode == 0

    for arch in arches:
        libraries = []
        for image in ("one", "two"):
            lib = out / ("memory-" + image + "-" + arch + ".dylib")
            cmd = common + ["-arch", arch, "-dynamiclib", "-Wl,-undefined,error", "-Wl,-twolevel_namespace",
                            "-Wl,-install_name,@rpath/" + lib.name, str(out / "posix_memory.c"), *helpers, "-o", str(lib)]
            if command(arch, image, "compile-dylib", cmd):
                libraries.append(str(lib))
        if len(libraries) != 2:
            continue
        cases = [("integrated", ["posix_memory.c", "guest_memory.c"])] if watch_protection_only else [
            ("direct", ["posix_memory.c", "direct_discard.c"]), ("integrated", ["posix_memory.c", "guest_memory.c"])]
        for case, names in cases:
            exe = out / (case + "-" + arch)
            extra = [fault, "-Wl,-exported_symbol,_hos_posix_*"] if case == "integrated" else []
            cmd = common + ["-arch", arch, *[str(out / n) for n in names], *helpers, *extra, "-o", str(exe)]
            if command(arch, case, "compile", cmd):
                args = ["--watch-protection-only"] if watch_protection_only else ["--behavior-only"] if behavior_only and case == "integrated" else []
                command(arch, case, "run", ["arch", "-" + arch, str(exe), *libraries, *args])
    if failures:
        raise SystemExit("FAIL: Darwin guest memory: " + ", ".join(failures))
    scope = "Darwin guest watch/protection: " if watch_protection_only else "Darwin guest memory: "
    print("PASS: " + scope + ", ".join(arches))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cc", default="clang")
    ap.add_argument("--source", type=Path, default=ROOT)
    ap.add_argument("--arch", choices=("arm64", "x86_64"), action="append")
    ap.add_argument("--out", type=Path, help="retain generated fixture sources and binaries in a new directory")
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--behavior-only", action="store_true", help="carry prior RSS proof; run the memory/lifetime/dirty checks without repeating its 128 MiB experiment")
    scope.add_argument("--watch-protection-only", action="store_true", help="only real-dispatch pending-fault and whole-range protection-failure regressions; no RSS or other memory experiments")
    args = ap.parse_args()
    if sys.platform == "darwin":
        arches = args.arch or [platform.machine()]
        if any(arch not in ("arm64", "x86_64") for arch in arches):
            ap.error("unsupported Darwin architecture: " + ", ".join(arches))
        if args.out:
            args.out.mkdir(parents=True, exist_ok=False)
            run(args.source.resolve(), args.out.resolve(), args.cc, arches, args.behavior_only, args.watch_protection_only)
        else:
            with tempfile.TemporaryDirectory(prefix="darwin-mem-") as tmp:
                run(args.source.resolve(), Path(tmp), args.cc, arches, args.behavior_only, args.watch_protection_only)
    else:
        print("SKIP: Darwin guest memory requires macOS Mach VM (Windows and Linux unchanged)")


if __name__ == "__main__":
    main()
