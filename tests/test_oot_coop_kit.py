"""Temporary sword kits: execute the native adapter and shared give/count wrappers.
Authored fake guest memory, real signed-rule registry; no game files. --mutations
compiles deliberately broken copies and requires a behavioral failure from each.
"""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    p.add_argument("--mutations", action="store_true")
    a = p.parse_args()
    root = a.source_root.resolve()
    spec = importlib.util.spec_from_file_location("oot_fixture", ROOT / "tests/test_oot_coop.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    main_source = (root / "host/hos_main.c").read_text()
    pouch = "\n".join(helper.piece(main_source, x) for x in ("int oot_pouch_change(", "int oot_pouch_count("))
    native = (root / "host/oot_coop_kit.c").read_text().replace('#include "oot_coop.h"', '#include "oot_coop_kit.h"')
    header = (root / "host/oot_coop_kit.h").read_text()
    fixture = (ROOT / "tests/oot_coop_kit_test.c").read_text().replace("/* @@POUCH@@ */", pouch)
    mutations = {
        "mount": ('if (!kit_mount())', 'if (0 && !kit_mount())'),
        "age": ('age ? "Player_Adult" : "Player_Child"', 'age < 2 ? "Player_Child" : "Player_Child"'),
        "capacity": ('kit_capacity(c, item->name) < 1', 'kit_capacity(c, item->name) < -100'),
        "unknown": ('(value & ~15)', '(value & ~32767)'),
        "readback": ('if (!kit_equipped(c, unit, item))', 'if (0 && !kit_equipped(c, unit, item))'),
        "repeat": ('if (r->gave[i]) return 0;', 'if (0 && r->gave[i]) return 0;'),
        "epoch": ('r->epoch != w->epoch', '0'),
        "off": ('|| !w->kit.version', '|| 0'),
        "queue": ('|| n || !kit_range', '|| (n && 0) || !kit_range'),
    }
    with tempfile.TemporaryDirectory(prefix="coop-kit-") as temp:
        folder = Path(temp)
        (folder / "a64.h").write_text('#pragma once\n#include <stdint.h>\ntypedef struct { uint64_t x[32], v[8]; } Ctx;\ntypedef void (*A64Fn)(Ctx *);\n')
        (folder / "hos.h").write_text('#pragma once\n#include "a64.h"\n')
        (folder / "fixture.c").write_text(fixture)
        cases = list(mutations) if a.mutations else ["native"]
        for case in cases:
            code, contract = native, header
            if case in mutations:
                old, new = mutations[case]
                assert code.count(old) + contract.count(old) == 1, case
                code, contract = code.replace(old, new), contract.replace(old, new)
            (folder / "kit.c").write_text(code)
            (folder / "oot_coop_kit.h").write_text(contract)
            exe = folder / "fixture.exe"
            command = [a.cc, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS",
                       "-I" + str(folder), "-I" + str(root / "host"), "-I" + str(root / "gpu/vk"),
                       str(folder / "fixture.c"), str(root / "host/coop_state.c"), str(root / "host/coop_proto.c"),
                       str(root / "third_party/monocypher/monocypher.c"), "-o", str(exe)]
            print("COMPILE", case, " ".join(command), flush=True)
            subprocess.run(command, check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            print(result.stdout + result.stderr, end="", flush=True)
            if case == "native":
                if result.returncode:
                    raise SystemExit(result.returncode)
            else:
                if not result.returncode or "Assertion" not in result.stderr and "assertion" not in result.stderr:
                    raise SystemExit("FAIL: mutation was not caught behaviorally: " + case)
                print("CAUGHT", case, "exit", result.returncode, flush=True)


if __name__ == "__main__":
    main()
