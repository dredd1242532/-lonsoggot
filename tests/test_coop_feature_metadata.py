"""Optional feature-rule presentation metadata preserves old five-field C entries.

Compile the real registry scope and lookup functions with a legacy entry and a labelled entry. No game, GPU,
session or cryptography is needed. The generic menu regression separately checks that the legacy entry has no row.
--source-root selects the parent's source for a negative run.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "coop_proto.h"
#include "coop_state.h"
@@REGISTRY@@
int main(void) {
    const CoopFeatureRule *old = coop_feature_at(0), *shown = coop_feature_at(1);
    assert(old && old->id == 250 && old->lo == -10 && old->hi == 100 && old->def == 7);
    assert(!strcmp(old->key, "coop_legacy_fixture"));
    assert(old->name == NULL && old->help == NULL);
    assert(shown && shown->id == 251 && shown->lo == 0 && shown->hi == 1 && shown->def == 1);
    assert(!strcmp(shown->key, "coop_visible_fixture"));
    assert(!strcmp(shown->name, "Fixture switch") && !strcmp(shown->help, "Fixture help."));
    assert(coop_feature_definition(250) == old && coop_feature_definition(251) == shown);
    assert(!coop_feature_at(-1) && !coop_feature_at(2) && !coop_feature_definition(252));
    puts("PASS: old five-field entries compile unchanged with null display metadata; labelled entries keep their text");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    args = p.parse_args()
    root = args.source_root.resolve()
    state = (root / "host/coop_state.c").read_text(encoding="utf-8")
    start = state.index("// Add each lane's definitions")
    end = state.index("static int feature_value(", start)
    registry = state[start:end]
    begin = registry.index("static const CoopFeatureRule feature_rules[] = {")
    end = registry.index("\n};", begin) + 3
    # The first real entry deliberately remains the old, positional five-field form, under -Wextra -Werror.
    table = '''static const CoopFeatureRule feature_rules[] = {
    {0, NULL, 0, 0, 0},
    {250, "coop_legacy_fixture", -10, 100, 7},
    {251, "coop_visible_fixture", 0, 1, 1, "Fixture switch", "Fixture help."},
};'''
    registry = registry[:begin] + table + registry[end:]
    with tempfile.TemporaryDirectory(prefix="coop-feature-metadata-") as name:
        folder = Path(name)
        source, exe = folder / "fixture.c", folder / "fixture"
        source.write_text(FIXTURE.replace("@@REGISTRY@@", registry), encoding="utf-8")
        subprocess.run([args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I" + str(root / "host"),
                        str(source), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
