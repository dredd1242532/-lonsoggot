"""Compile actual Apple import/retirement, ABI wiring and startup error paths without a GPU or game.

The Vulkan calls in these two consumers are recording/failure-injection fixtures, not a driver.
--legacy-source separately executes the original LS import/retirement topology; that is not an observed LS frame.
--out retains the exact excerpts, compiler commands, logs, exits and full input SHA-256s.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_headers}"]
ROOT = Path(__file__).resolve().parents[1]


def source_helpers():
    spec = importlib.util.spec_from_file_location("darwin_admission_helpers", ROOT / "tests/test_vk_darwin_admission.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load the existing Darwin admission fixture helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.block, module.select


def import_excerpts(root, legacy=False):
    block, select = source_helpers()
    memory = (root / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    replay = (root / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    internal = (root / "gpu/vk/vk_internal.h").read_text(encoding="utf-8")
    typed = ["struct Pool {", "struct GuestPool {", "struct DeadImport {"]
    if not legacy:
        typed[0:0] = ["struct DarwinImportBacking {", "struct DarwinImportView {"]
    types = "void destroy_import_buffer(VkBuffer);\n" + "\n".join(block(memory, marker) + ";" for marker in typed)
    types += "\n" + block(internal, "struct BufferRef {") + ";\n"
    if not legacy:
        apple = select(memory, True, False)
        for name in ("imported", "bg_ready", "retired_pools", "dead_imports", "evicted_pools"):
            start = apple.index("auto& " + name + " = *new ")
            types += apple[start:apple.index(";", start) + 1] + "\n"
        types += "int darwin_guest_imported(uint64_t lo, uint64_t hi);\n"
    start = memory.index("constexpr VkBufferUsageFlags kUsage =")
    code = memory[start:memory.index(";", start) + 1] + "\n"
    code += block(memory, "void destroy_import_buffer(") + "\n"
    if not legacy:
        code += block(memory, "template<class T> void destroy_import(") + "\n"
        for marker in (
            "bool darwin_import_commit(",
            "std::shared_ptr<DarwinImportBacking> darwin_pin_backing(",
            "bool darwin_allocate_backing(", "bool darwin_import_view(", "bool darwin_import_pool(",
        ):
            code += block(memory, marker) + "\n"
    for marker in (
        "bool import_on_hold(", "void import_remember_failure(", "bool import_pool(",
        "void guest_pages_set(", "bool memory_init(",
        "void memory_sync(",
        "void bg_drain(", "void span_game_pools(", "Pool* pool_lookup(uint64_t guest, uint64_t size) {",
        "Pool* pool_of(", "void retire_pool(std::map<uint64_t, Pool>::iterator it) {", "BufferRef gpu_buffer(",
        "int memory_guest_imported(", "void reap_mirrors(", "retired_give_back::Mode retired_mode(",
        "void retired_imports_give_back(",
    ):
        code += block(memory, marker) + "\n"
    if not legacy:
        code += block(memory, "int darwin_guest_imported(uint64_t lo, uint64_t hi) {") + "\n"
        sync = block(memory, "void memory_sync() {")
        if "memory_import_quiesce(" in sync or "vkWait" in sync:
            raise AssertionError("memory_sync must never drain GPU work under sync_mu or from an arbitrary caller")
        submit = block(replay, "void submit_memory_work(")
        if not submit.index("memory_sync();") < submit.index("memory_take_capture(") < submit.index("memory_collect_written();"):
            raise AssertionError("submit-time pool/capture/dirty collection order changed")
        for marker, target in (("void retire_pool(std::map<uint64_t, Pool>::iterator it) {", "retired_pools"),
                               ("void imports_give_back() {", "evicted_pools")):
            body = block(memory, marker)
            if body.index(target + ".emplace_back();") > body.index("imported.extract("):
                raise AssertionError("retirement moved an in-flight owner before allocating its queue slot")
        retired = block(memory, "void retired_imports_give_back() {")
        if retired.index("dead_imports.emplace_back()") > retired.index("std::move(p.import_view)"):
            raise AssertionError("dead-import transfer can destroy ownership on failed queue allocation")
    # Keep the real raw-import eviction gate, without the unrelated pressure-selection policy that follows it.
    eviction = block(memory, "void imports_give_back() {")
    code += eviction[:eviction.index("    // (longshot) Off by default")] + "}\n"
    code = select(code, True, False).replace("#ifdef __linux__", "#if 0")
    return select(types, True, False), code


def startup_excerpts(root):
    block, select = source_helpers()
    replay = (root / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    vma = (root / "gpu/vk/vk_vma.cpp").read_text(encoding="utf-8")
    backend = (root / "host/rx_backend.c").read_text(encoding="utf-8")
    api = (root / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    api = select(api, True, False).replace('"../render/rx_nvn.h"', '"gpu/render/rx_nvn.h"')
    types = block(replay, "struct Recording {") + ";\n" + block(replay, "struct Frame {") + ";\n"
    code = "\n".join(block(replay, m) for m in ("void init_pools_destroy() {", "bool init_pools_make(",
                                                "bool replay_start_rollback() {", "bool replay_start() {"))
    code += "\n" + block(vma, "bool vma_init(") + "\n" + block(vma, "void vma_startup_rollback(")
    # The host table is positional. All untouched callbacks have their actual declared types; pin/unpin are executable spies.
    import re
    host_struct = block(api, "typedef struct MkwVkHost {")
    fields = re.findall(r"\(\*(\w+)\)\s*\(", host_struct)
    initializer = select(block(backend, "static const MkwVkHost vk_host ="), True, False) + ";"
    names = [x.strip() for x in initializer[initializer.index("{") + 1:initializer.rindex("}")].split(",") if x.strip()][1:]
    if len(fields) != len(names):
        raise ValueError("host callback initializer does not match its declared interface")
    wiring = "\n".join(f"static decltype(MkwVkHost::{f}) {n} = nullptr;" for f, n in zip(fields, names)
                       if f not in ("guest_import_pin", "guest_import_unpin"))
    wiring += "\n" + initializer
    message = block((root / "host/posix/win32_posix.c").read_text(encoding="utf-8"), "int MessageBoxA(")
    messages = {"message_apple.inc": select(message, True, False), "message_linux.inc": select(message, False, False)}
    return api, select(types, True, False), select(code, True, False), wiring, messages


def inputs(root, legacy):
    paths = [root / p for p in ("gpu/vk/vk_memory.cpp", "gpu/vk/vk_replay.cpp", "gpu/vk/vk_internal.h", "gpu/vk/import_hold.h",
                               "gpu/vk/retired_give_back.h")]
    paths += [ROOT / "tests/test_darwin_renderer.py", ROOT / "tests/darwin_import_lifetime_test.cpp",
              ROOT / "tests/test_vk_darwin_admission.py", ROOT / "gpu/vk/darwin_import.h"]
    if not legacy:
        paths += [root / p for p in ("gpu/vk/vk_api.h", "gpu/vk/vk_vma.cpp", "host/rx_backend.c", "host/posix/win32_posix.c")]
        paths += [ROOT / "tests/darwin_renderer_startup_test.cpp"]
    return {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def run_checks(args, out):
    root = (args.legacy_source or args.source).resolve()
    legacy = args.legacy_source is not None
    manifest = {"source": str(root), "legacy_topology_only": legacy, "inputs": inputs(root, legacy), "commands": []}
    types, code = import_excerpts(root, legacy)
    (out / "import_types.inc").write_text(types, encoding="utf-8")
    (out / "import_actual.inc").write_text(code, encoding="utf-8")
    fixtures = [ROOT / "tests/darwin_import_lifetime_test.cpp"]
    if not legacy:
        api, types, code, wiring, messages = startup_excerpts(root)
        for name, text in (("apple_api.h", api), ("startup_types.inc", types), ("startup_actual.inc", code), ("host_wiring.inc", wiring)):
            (out / name).write_text(text, encoding="utf-8")
        for name, text in messages.items():
            (out / name).write_text(text, encoding="utf-8")
        fixtures.append(ROOT / "tests/darwin_renderer_startup_test.cpp")
    arches = args.arch or [None]
    try:
        for arch in arches:
            for fixture in fixtures:
                label = fixture.stem + "-" + (arch or "native")
                exe = out / (label + ".exe")
                command = [args.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                           "-Wno-unused-variable", "-Wno-missing-field-initializers", "-D_CRT_SECURE_NO_WARNINGS", "-pthread",
                           "-DHAVE_DARWIN_IMPORTS=" + str(int(not legacy)), "-I" + str(args.headers.resolve()),
                           "-I" + str(out), "-I" + str(ROOT), "-I" + str(root), str(fixture), "-o", str(exe)]
                if sys.platform == "darwin":
                    command += ["-mmacosx-version-min=14.4"]
                    if arch:
                        command += ["-arch", arch]
                run = (["arch", "-" + arch] if arch else []) + [str(exe)]
                steps = [("compile", command, 180), ("run", run, 45)]
                if not legacy and fixture.name == "darwin_import_lifetime_test.cpp":
                    steps.append(("run-process-exit", run + ["--process-exit"], 15))
                for stage, cmd, timeout in steps:
                    print(stage.upper() + ": " + " ".join(cmd), flush=True)
                    result = subprocess.run(cmd, cwd=out, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
                    log = out / (label + "-" + stage + ".log")
                    log.write_text(result.stdout, encoding="utf-8")
                    manifest["commands"].append({"argv": cmd, "cwd": str(out), "exit": result.returncode, "log": log.name})
                    print(result.stdout, end="", flush=True)
                    print("EXIT:", result.returncode, flush=True)
                    if result.returncode:
                        return result.returncode
        return 0
    finally:
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--legacy-source", type=Path)
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--headers", type=Path, required=True)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), action="append")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.arch and sys.platform != "darwin":
        parser.error("--arch is a macOS-only route; compilers remain native")
    if args.out:
        out = args.out.resolve()
        out.mkdir(parents=True, exist_ok=False)
        return run_checks(args, out)
    with tempfile.TemporaryDirectory(prefix="darwin-renderer-") as temp:
        return run_checks(args, Path(temp))


if __name__ == "__main__":
    raise SystemExit(main())
