"""Registered feature rules become menu rows without a per-feature menu change.

Compile the real registry lookup, host metadata callback, row construction/mapping, named options reader, save(),
and startup load/migration. A synthetic registry includes a legacy five-field entry, labelled numeric/boolean rules,
and a newly appended rule with long display text. Also compile an empty registry. Unknown and row-less lines must
survive both an ordinary save and the startup migration followed by a save, unchanged; visible keys occur once.
The fixture shares the existing environment-save test's platform-independent setup, not a second serializer.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]

MAIN = r'''
std::string mic_key(const char* s) { return s; }
void refresh_mics() {}
void mic_tell_host() {}
int rumble_step(int v) { return v; }
void startup_read() {
    coop_feature_init();
    bool has_options = false, has_vram_budget = false, has_memory_budget = false, has_output = false;
    @@LOAD@@
    (void)has_options; (void)has_vram_budget; (void)has_memory_budget; (void)has_output;
}
static int occurrences(const char* key) {
    const std::string all = slurp(), prefix = std::string(key) + "=";
    int n = 0;
    for (size_t at = 0; (at = all.find(prefix, at)) != std::string::npos; at += prefix.size())
        n += at == 0 || all[at - 1] == '\n';
    return n;
}
static void seed_file() {
    FILE* f = std::fopen(ini_file.c_str(), "wb");
    if (!f) std::abort();
    // Row-less and unknown lines deliberately have formatting/value text a menu must not normalize.
    std::fputs("options_version=3\nmenu_shortcut=0\nultrawide=0\n"
               "coop_labels=2\ncoop_amount_fixture=19\ncoop_switch_fixture=0\ncoop_added_fixture=-5\n"
               "coop_legacy_fixture=  +009  \ncoop_future_fixture=009 keep me\n", f);
    std::fclose(f);
}
int main(int argc, char** argv) {
    CHECK(argc == 2);
    ini_file = argv[1];
    CHECK(opts.coop_labels == 1);
    coop_feature_init();
    CHECK(coop_feature_count == (NAMED_REGISTRY ? 3 : 0));
    CHECK(menu_row_count() == kRows + coop_feature_count);
    CHECK(tab_row_count(TAB_RULES) == 1 + coop_feature_count);
    CHECK(!coop_feature_saved_key("coop_legacy_fixture") && !coop_feature_saved_key("coop_future_fixture"));
    MkwVkCoopFeature raw{};
    if (NAMED_REGISTRY) {
        CHECK(host->coop_feature(0, &raw) && raw.id == 249 && !raw.name && !raw.help);
        int visible = 0;
        for (int i = 0; host->coop_feature(i, &raw); i++) {
            if (!raw.name || !*raw.name) continue;
            Row& row = menu_row(tab_row_to_global(TAB_RULES, ++visible));
            CHECK(row.value && *row.value == raw.def && row.lo == raw.lo && row.hi == raw.hi && row.step == 1);
            CHECK(!row.restart && row.preset_exempt && restart_slot_of(row) == -1);
            CHECK(!std::strcmp(coop_feature_row_key(row), raw.key));
            CHECK(std::strlen(row.name) <= 63 && std::strlen(row.help) <= 255);
            CHECK(!std::strncmp(row.name, raw.name, std::strlen(row.name)));
        }
        CHECK(visible == 3);
        CHECK(std::strlen(coop_feature_rows[2].name) == 63 && std::strlen(coop_feature_rows[2].help) == 255);
        CHECK(!std::strcmp(coop_feature_rows[1].row.text(0), "Off") && !std::strcmp(coop_feature_rows[1].row.text(1), "On"));
        CHECK(!std::strcmp(coop_feature_rows[0].row.text(-10), "-10"));
        CHECK(coop_feature_option_line("coop_amount_fixture", "1000") && coop_feature_rows[0].value == 7);
        CHECK(coop_feature_option_line("coop_amount_fixture", "-10") && coop_feature_rows[0].value == -10);
        CHECK(coop_feature_option_line("coop_amount_fixture", "100") && coop_feature_rows[0].value == 100);
        CHECK(!coop_feature_option_line("coop_legacy_fixture", "2"));
        MkwVkSetting setting{};
        int found = 0;
        for (int i = 0; menu_setting(i, &setting); i++)
            if (!std::strcmp(setting.tab, "room rules") && std::strncmp(setting.key, "coop_", 5) == 0 &&
                std::strcmp(setting.key, "coop")) found++;
        CHECK(found == 3);
    }
    seed_file();
    opts.menu_shortcut = 0, opts.ultrawide = 0;
    startup_read();
    CHECK(opts.menu_shortcut == 2 && opts.ultrawide == 1);   // the real version-3 startup migrations ran
    CHECK(opts.coop_labels == 2); // local tab value loads by its named key, not the row's display name
    opts.coop_labels = 0;
    if (NAMED_REGISTRY) {
        CHECK(coop_feature_rows[0].value == 19 && coop_feature_rows[1].value == 0 && coop_feature_rows[2].value == -5);
        coop_feature_rows[0].value = 23;
    }
    save();
    CHECK(has("coop_legacy_fixture=  +009  \n") && has("coop_future_fixture=009 keep me\n"));
    CHECK(has("coop_labels=0\n") && occurrences("coop_labels") == 1);
    CHECK(has(NAMED_REGISTRY ? "coop_amount_fixture=23\n" : "coop_amount_fixture=19\n"));
    CHECK(occurrences("coop_amount_fixture") == 1 && occurrences("coop_switch_fixture") == 1 &&
          occurrences("coop_added_fixture") == 1 && occurrences("coop_legacy_fixture") == 1 && occurrences("coop_future_fixture") == 1);
    // Another ordinary menu save must neither duplicate a row-owned key nor rewrite opaque lines.
    opts.sharpness = 40;
    save();
    CHECK(has("sharpness=40\n") && has("coop_legacy_fixture=  +009  \n") && has("coop_future_fixture=009 keep me\n"));
    CHECK(occurrences("coop_amount_fixture") == 1 && occurrences("coop_legacy_fixture") == 1 && occurrences("coop_future_fixture") == 1);
    // A fresh startup/migration of the old file followed by save is a separate persistence case.
    seed_file();
    opts.menu_shortcut = 0, opts.ultrawide = 0;
    startup_read();
    save();
    CHECK(opts.menu_shortcut == 2 && opts.ultrawide == 1);
    CHECK(opts.coop_labels == 2 && has("coop_labels=2\n") && occurrences("coop_labels") == 1);
    CHECK(has("coop_amount_fixture=19\n") && has("coop_switch_fixture=0\n") && has("coop_added_fixture=-5\n"));
    CHECK(has("coop_legacy_fixture=  +009  \n") && has("coop_future_fixture=009 keep me\n"));
    CHECK(occurrences("coop_amount_fixture") == 1 && occurrences("coop_switch_fixture") == 1 &&
          occurrences("coop_added_fixture") == 1 && occurrences("coop_legacy_fixture") == 1 && occurrences("coop_future_fixture") == 1);
    std::puts(NAMED_REGISTRY ? "PASS: registry-defined rows, defaults/ranges, legacy entry hidden, bounded text, bridge listing; visible keys saved once, row-less/unknown lines preserved through save and startup migration"
                            : "PASS: empty registry shows no rows and preserves every feature key through save and startup migration");
}
'''


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit("FAIL: fixture anchor is not unique: " + old[:90])
    return text.replace(old, new, 1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    p.add_argument("--cc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    args = p.parse_args()
    root = args.source_root.resolve()
    cc = args.cc or ("clang" if "clang" in Path(args.cxx).name else "gcc")
    menu = (root / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    api = (root / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    if "struct CoopFeatureRow {" not in menu or "MkwVkCoopFeature" not in api:
        raise SystemExit("FAIL: no registry-backed feature rows and host metadata interface")
    spec = importlib.util.spec_from_file_location("menu_env_fixture", root / "tests/test_menu_env_overrides.py")
    env_fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env_fixture)
    block, definition = env_fixture.block, env_fixture.definition
    cpp = env_fixture.fixture(root)
    # This fixture replaces the environment test's main and never calls its Windows-only setenv helper.
    cpp = cpp.replace(definition(cpp, "static int setenv("), "")
    feature_type = block(api, api.index("typedef struct MkwVkCoopFeature {")) + " MkwVkCoopFeature;"
    setting_type = block(api, api.index("typedef struct MkwVkSetting {")) + " MkwVkSetting;"
    max_features = re.search(r"enum \{ HOS_COOP_RULE_CATEGORIES = \d+, HOS_COOP_FEATURES = \d+ \};", api).group(0)
    host = '''@@TYPES@@
extern "C" int hos_coop_menu_feature(int, MkwVkCoopFeature*);
static float test_aspect() { return 4.0f / 3.0f; }
struct Host { int (*binding)(int, int, int); int (*coop_command)(int, const char*, uint32_t);
              int (*coop_feature)(int, MkwVkCoopFeature*); float (*aspect_ratio)(); }
    host_value{nullptr, nullptr, hos_coop_menu_feature, test_aspect}, *host = &host_value;'''
    cpp = replace_once(cpp, "struct Host { int (*binding)(int, int, int); int (*coop_command)(int, const char*, uint32_t); } host_value{nullptr, nullptr}, *host = &host_value;",
                       host.replace("@@TYPES@@", max_features + "\n" + feature_type + "\n" + setting_type))
    cpp = replace_once(cpp, '    {TAB_GAMEPLAY, "Game speed", &opts.realtime, 0, 1, 1, tx, ""},',
                       '    {TAB_GAMEPLAY, "Game speed", &opts.realtime, 0, 1, 1, tx, ""},\n'
                       '    {TAB_RULES, "Co-op at start", &opts.coop, 0, 1, 1, tx, "", nullptr, false, true},\n'
                       '    {TAB_COOP, "Friend labels", &opts.coop_labels, 0, 2, 1, tx, "", nullptr, false, true},')
    cpp = replace_once(cpp, "std::string ini_file;", "constexpr int kRows = sizeof rows / sizeof rows[0];\nint restart_slot[kRows]{};\nstd::string ini_file;")
    text_field = block(menu, menu.index("struct TextField {")) + ";"
    cpp = replace_once(cpp, 'struct { int len; const char* text; } coop_name_field{0, ""}, coop_relay_field{0, ""};',
                       text_field + "\nTextField coop_name_field{}, coop_relay_field{};\n" + definition(menu, "void text_set"))
    feature_helpers = menu[menu.index("struct CoopTermRow {"):menu.index("\nint cur_tab =")]
    options = menu[menu.index("const struct { int* value; const char* key; } coop_options[]"):menu.index("// (longshot) the row's choice to the host:")]
    helpers = definition(menu, "const char* tx_onoff") + "\n" + feature_helpers + "\n" + options
    helpers += "\nbool row_shown(int) { return true; }\n" + definition(menu, "int tab_row_count") + "\n" + definition(menu, "int tab_row_to_global")
    helpers += "\n" + definition(menu, "int restart_slot_of")
    helpers += "\nbool row_fixed(const int*) { return false; }\nstd::mutex menu_mu;\nvoid bridge_probe_first() {}\nvoid coop_refresh(bool*) {}\n"
    helpers += "\n".join(definition(menu, n) for n in ("bool bridge_row", "void bridge_fill", "int menu_setting"))
    cpp = replace_once(cpp, 'const char* row_key(const Row&, const char* named) { return named; }', helpers)
    cpp = replace_once(cpp, "bool coop_feature_saved_key(const char*) { return false; }", "")
    cpp = replace_once(cpp, "void coop_feature_save(FILE*) {}", "")
    init = definition(menu, "void menu_init")
    load_at = init.index('    if (FILE* f = fopen(ini_path().c_str(), "r")) {')
    load = block(init, load_at)
    old_main = definition(cpp, "int main")
    cpp = replace_once(cpp, old_main, MAIN.replace("@@LOAD@@", load))
    state = (root / "host/coop_state.c").read_text(encoding="utf-8")
    registry = state[state.index("// Add each lane's definitions"):state.index("static int feature_value(")]
    begin = registry.index("static const CoopFeatureRule feature_rules[] = {")
    end = registry.index("\n};", begin) + 3
    long_name, long_help = "N" * 90, "H" * 300
    table = '''static const CoopFeatureRule feature_rules[] = {
    {0, NULL, 0, 0, 0},
#if NAMED_REGISTRY
    {249, "coop_legacy_fixture", 0, 100, 3},
    {250, "coop_amount_fixture", -10, 100, 7, "Fixture amount", "Fixture amount help."},
    {251, "coop_switch_fixture", 0, 1, 1, "Fixture switch", NULL},
    {252, "coop_added_fixture", -6, 9, -2, "@@NAME@@", "@@HELP@@"},
#endif
};'''.replace("@@NAME@@", long_name).replace("@@HELP@@", long_help)
    registry = registry[:begin] + table + registry[end:]
    host_source = (root / "host/coop_menu.c").read_text(encoding="utf-8")
    metadata = '#include <stddef.h>\n#include "coop_proto.h"\n#include "coop_state.h"\n#include "gpu/vk/vk_api.h"\n' + registry + definition(host_source, "int hos_coop_menu_feature")
    with tempfile.TemporaryDirectory(prefix="menu-feature-rules-") as name:
        folder = Path(name)
        (folder / "metadata.c").write_text(metadata, encoding="utf-8")
        (folder / "fixture.cpp").write_text(cpp, encoding="utf-8")
        for named in (0, 1):
            obj, exe = folder / "metadata.o", folder / ("fixture.exe" if os.name == "nt" else "fixture")
            subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-DNAMED_REGISTRY=" + str(named), "-I" + str(root),
                            "-I" + str(root / "host"), "-c", str(folder / "metadata.c"), "-o", str(obj)], check=True)
            subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS",
                            "-DNAMED_REGISTRY=" + str(named), "-I" + str(root / "host"), "-I" + str(root / "gpu/vk/compat"),
                            "-I" + str(root / "gpu/vk"),
                            str(folder / "fixture.cpp"), str(obj), "-o", str(exe)], check=True)
            subprocess.run([str(exe), str(folder / "longshot_options.ini")], check=True)


if __name__ == "__main__":
    main()
