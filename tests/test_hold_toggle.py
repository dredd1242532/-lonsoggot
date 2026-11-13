"""Press edges latch once; menu/focus/pause reset requires release before rearming."""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    code = r'''
#include "host/hold_toggle.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    HoldToggle s = {0};
    const uint64_t r = 1 << 7, zl = 1 << 8;
    assert(hold_toggle_apply(&s, r, r, 0) == r);
    assert(hold_toggle_apply(&s, r, r, 0) == r); /* repeat is not a new press */
    assert(hold_toggle_apply(&s, 0, r, 0) == r);
    assert(hold_toggle_apply(&s, r, r, 0) == 0);
    hold_toggle_apply(&s, 0, r, 0);
    assert(hold_toggle_apply(&s, r | zl, r | zl, 0) == (r | zl));
    assert(hold_toggle_apply(&s, 0, r | zl, 1) == 0); /* menu hides physical input */
    assert(hold_toggle_apply(&s, r | zl, r | zl, 0) == 0); /* still held at resume */
    hold_toggle_apply(&s, 0, r | zl, 0);
    assert(hold_toggle_apply(&s, r, r | zl, 0) == r);
    assert(hold_toggle_apply(&s, 0, 0, 0) == 0); /* disabling drops the latch */
    assert(hold_toggle_apply(&s, r, 0, 0) == r); /* Off is ordinary input */
    assert(hold_toggle_apply(&s, 0, 0, 0) == 0);
    puts("toggle: press=down repeat=down release=down press=up; reset=up held-after-reset=up");
}'''
    with tempfile.TemporaryDirectory(prefix="hold-toggle-") as tmp:
        source, exe = Path(tmp) / "toggle.c", Path(tmp) / "toggle.exe"
        source.write_text(code, encoding="utf-8")
        subprocess.run([args.cc, "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT), str(source), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
