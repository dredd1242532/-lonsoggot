"""Link the real Darwin waveOut shim strictly, then resolve only the executable's SDL audio service.

No SDL library, audio device, window, Vulkan, or game is used. The executable's SDL endpoint is an authored spy;
the shim, provider forwarding body, queue callback and MMRESULT paths are the actual changed source.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

RUN_ALL_SKIP = {"windows": "Darwin RTLD_MAIN_ONLY and Mach-O strict-link contract", "linux": "Darwin RTLD_MAIN_ONLY and Mach-O strict-link contract"}
ROOT = Path(__file__).resolve().parents[1]


MODULE = r'''
#include <windows.h>
#include <mmsystem.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
/* ACTUAL */
/* A renderer-local lookalike must NOT become the executable-owned service. */
__attribute__((visibility("default")))
int hos_posix_audio_open(int freq, int channels, int s16, void (*pull)(void*, uint8_t*, int), void* user) {
    (void)freq; (void)channels; (void)s16; (void)pull; (void)user;
    return 1;
}
__attribute__((visibility("default")))
void fixture_release(HWAVEOUT hw) {
    WaveOut* w = (WaveOut*)hw;
    pthread_mutex_destroy(&w->mu);
    free(w);
}
'''

HOST = r'''
#include <windows.h>
#include <mmsystem.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int calls, provider_ok = 1;
static void check(int yes, const char* what) { if (!yes) { fprintf(stderr, "FAIL: %s\n", what); exit(1); } }
#ifdef PROVIDER
static int hos_sdl_audio_open(int freq, int channels, int s16, void (*pull)(void*, uint8_t*, int), void* user) {
    ++calls;
    check(freq == 48000 && channels == 2 && s16 == 1 && pull && user, "provider argument ABI");
    uint8_t data[16]; memset(data, 0x7f, sizeof data);
    pull(user, data, sizeof data);
    for (unsigned i = 0; i < sizeof data; ++i) check(data[i] == 0, "actual wave callback without queued frames");
    return provider_ok;
}
/* PROVIDER BODY */
#endif
int main(int argc, char** argv) {
    check(argc == 3, "fixture args");
    provider_ok = strcmp(argv[2], "failure") != 0;
    void* module = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    check(module != NULL, "strict-linked shim load");
    typedef MMRESULT (*Open)(HWAVEOUT*, UINT, const WAVEFORMATEX*, DWORD_PTR, DWORD_PTR, DWORD);
    Open open_wave = (Open)dlsym(module, "waveOutOpen");
    void (*release)(HWAVEOUT) = (void (*)(HWAVEOUT))dlsym(module, "fixture_release");
    check(open_wave && release, "fixture exports");
    WAVEFORMATEX format = {0}; format.nSamplesPerSec = 48000; format.nChannels = 2; format.wBitsPerSample = 16;
    HWAVEOUT wave = (HWAVEOUT)(uintptr_t)0x1234;
    MMRESULT result = open_wave(&wave, 0, &format, 0, 0, 0);
#ifdef PROVIDER
    check(calls == 1, "canonical executable provider not called exactly once");
    if (provider_ok) {
        check(result == MMSYSERR_NOERROR && wave != (HWAVEOUT)(uintptr_t)0x1234, "successful audio service not forwarded");
        release(wave);
    } else check(result == MMSYSERR_ERROR && wave == (HWAVEOUT)(uintptr_t)0x1234, "failed audio service pretended success");
#else
    check(result == MMSYSERR_ERROR && calls == 0 && wave == (HWAVEOUT)(uintptr_t)0x1234,
          "missing executable provider used a renderer-local lookalike");
#endif
    check(dlclose(module) == 0, "fixture module close");
    puts("PASS: strict Mach-O waveOut service, executable-only resolution, callback ABI and failure result; no SDL/audio opened");
    return 0;
}
'''


def run_checks(args, out):
    helper_path = ROOT / "tests/test_vk_darwin_admission.py"
    spec = importlib.util.spec_from_file_location("admission_helpers", helper_path)
    if spec is None or spec.loader is None:
        raise ImportError("admission source helper unavailable")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    root = args.source.resolve()
    shim = root / "host/posix/win32_posix.c"
    sdl = root / "host/hos_sdl.c"
    text = shim.read_text(encoding="utf-8")
    start = text.index("typedef int (*WaveAudioOpenFn)")
    actual = text[start:text.index("MMRESULT waveOutPrepareHeader(", start)]
    provider = helper.block(sdl.read_text(encoding="utf-8"), '__attribute__((visibility("default")))\nint hos_posix_audio_open(')
    module_source, host_source = out / "audio-shim.c", out / "audio-host.c"
    module_source.write_text(MODULE.replace("/* ACTUAL */", actual), encoding="utf-8")
    host_source.write_text(HOST.replace("/* PROVIDER BODY */", provider), encoding="utf-8")
    paths = [shim, sdl, root / "host/hos_sdl.h", root / "host/posix/windows.h", root / "host/posix/mmsystem.h", helper_path, Path(__file__)]
    receipt = {"inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}, "commands": []}
    try:
        for arch in args.arch or [None]:
            tag = arch or "native"
            module, host, missing = out / ("audio-shim-" + tag + ".dylib"), out / ("audio-host-" + tag), out / ("audio-missing-" + tag)
            common = [args.cc, "-x", "c", "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-mmacosx-version-min=14.4",
                      "-I" + str(root / "host/posix")]
            if arch:
                common += ["-arch", arch]
            commands = [("link-shim", common + ["-dynamiclib", "-Wl,-undefined,error", str(module_source), "-o", str(module)]),
                        ("link-host", common + ["-fvisibility=hidden", "-DPROVIDER", "-Wl,-exported_symbol,_hos_posix_audio_open",
                                               str(host_source), "-o", str(host)]),
                        ("link-missing", common + ["-fvisibility=hidden", str(host_source), "-o", str(missing)])]
            prefix = ["arch", "-" + arch] if arch else []
            commands += [("run-success", prefix + [str(host), str(module), "success"]),
                         ("run-failure", prefix + [str(host), str(module), "failure"]),
                         ("run-missing", prefix + [str(missing), str(module), "missing"])]
            for label, command in commands:
                print("COMMAND:", " ".join(command), flush=True)
                result = subprocess.run(command, cwd=out, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                log = tag + "-" + label + ".log"
                (out / log).write_text(result.stdout, encoding="utf-8")
                receipt["commands"].append({"argv": command, "exit": result.returncode, "log": log})
                print(result.stdout, end="", flush=True)
                print("EXIT:", result.returncode, flush=True)
                if result.returncode:
                    return result.returncode
        return 0
    finally:
        (out / "manifest.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--cc", default="clang")
    parser.add_argument("--arch", choices=("arm64", "x86_64"), action="append")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("this strict two-image service test is macOS-only")
    if args.out:
        out = args.out.resolve()
        out.mkdir(parents=True, exist_ok=False)
        return run_checks(args, out)
    with tempfile.TemporaryDirectory(prefix="darwin-audio-") as folder:
        return run_checks(args, Path(folder))


if __name__ == "__main__":
    raise SystemExit(main())
