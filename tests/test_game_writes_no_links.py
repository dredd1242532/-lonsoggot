"""The files the game writes by itself are not written through a symbolic link at their name.

A game folder unpacked from an archive someone shared can hold links (tar and unzip keep them on Linux). The game rewrites
longshot_options.ini when an option changes, and writes its pipeline and program caches, state lists, markers, log,
button-prompt textures, mouse_sens.txt, language.txt and the moved-saves note by itself: through such a link a plain
fopen("w") emptied and rewrote the file it named. host/hos_fopen_write.h opens those with O_NOFOLLOW (Linux), or after
checking the name's own reparse point (Windows), and only as regular files.

Checked here, without a game:
- every open for writing in host/ and gpu/vk/ (fopen and freopen with a mode that writes, open with O_WRONLY, O_RDWR,
  O_CREAT or O_APPEND, CreateFile with write access, ofstream, CopyFile, copy_file) goes through hos_fopen_write, or makes
  its file new (fopen's "x", O_EXCL or O_NOFOLLOW, CREATE_NEW), or is one of REVIEWED: the debugging writes a switch turns
  on, each a switch that longshot_env.txt, a file players share, cannot set, and the few that write no file of the
  game's folder by name. A new write that is none of these fails here, and a REVIEWED entry no longer in the sources
  does too. (A rename onto a name replaces a link there, it does not write through it: renames are not looked at.)
- hos_fopen_write refuses a link (to a file, or dangling: nothing is made where it points) and a FIFO (without waiting),
  writes and empties a regular file for "w"/"wb", appends for "a"/"ab", and updates an existing one for "r+b" (the
  program cache, cut back to its last whole record);
- hos_freopen_write puts a stream on a file the same way (the log);
- hos_fopen_read_nofollow, which the bug report reads the logs it copies with (the player sends them), refuses a link at
  the name, a FIFO and a folder, and reads a regular file;
- its Windows branch (with a stand-in windows.h): the name is opened as itself, and a name surrogate (symbolic link,
  junction, WSL's and the container links) is refused before anything opens it to write or read. A plain file is
  opened for writing once more as itself, and refused if a link or folder is there by then; no file index is compared
  for it (some file systems make them up for each open). OneDrive's files are opened again without the flag and kept
  only when the file index matches (a 0 index is refused). The bug report's read follows the same rule.
- with LONGSHOT_MINGW=<llvm-mingw>, the header builds against Windows' own headers, as C and as C++.

python -I -B tests/test_game_writes_no_links.py [--cc gcc]
"""
import argparse
import collections
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_SKIP = {"windows": "the links checked are Linux's (an archive's links are not made on Windows)"}

# (the small text files the game reads at the start, each through hos_fopen_read: file -> how many. vk_draw.cpp: with
# Longshot's own markers and lists; its pipeline caches are read by PipelineCacheFile::read, a regular file only too)
BINARY_READS = {"gpu/vk/vk_draw.cpp": 28, "host/prompts.c": 5, "gpu/vk/vk_main.cpp": 1, "gpu/vk/vk_game_font.h": 1,
                "host/hos_main.c": 2, "gpu/vk/vk_progcache.cpp": 2, "gpu/vk/load_default.h": 1,
                "gpu/vk/pcache_stamp.h": 1, "gpu/vk/module_ids.h": 1, "gpu/vk/vk_shader.cpp": 1}
# (the caches, lists and markers, RX_VK_DRAW_STATS' trigger file; the prompt files and the game's prompt files read back;
# the game's image, the shaders seed and the module identifiers)
# (the reads whose bytes leave the PC, through hos_fopen_read_nofollow: the bug report's copies of the two logs)
NOFOLLOW_READS = {"gpu/vk/vk_bug_report.cpp": 1}
READS = {"host/hos_main.c": 2, "host/nn_applet.c": 2, "host/nn_hid.c": 2, "host/discord_rpc.c": 1, "host/prompts.c": 2,
         "host/nn_audio.c": 1, "gpu/vk/vk_menu.cpp": 2, "gpu/vk/vk_draw.cpp": 2, "gpu/vk/vk_main.cpp": 2,
         "gpu/vk/vk_menu_i18n.cpp": 2}

# (what is looked through: the game's own sources, not the Linux stand-ins for Windows (host/posix), the vendored emulator
# and compiler code, or the renderer's unit tests; the two headers are the write and read themselves)
SOURCES = ("host", "gpu/vk")
NOT_LOOKED_AT = ("host/posix/", "gpu/vk/eden/", "gpu/vk/sirit/", "gpu/vk/compat/", "gpu/vk/tests/")
HELPERS = {"host/hos_fopen_write.h", "host/hos_fopen_read.h"}

# (the reviewed writes that do not go through hos_fopen_write: file, the call as written (its arguments with their spaces
# collapsed), the switch that turns it on, why. A switch here must be one longshot_env.txt cannot set: checked below
# against env_line_allowed. None: no switch, and why it writes no file of the game's folder by its name)
REVIEWED = [
    ("host/nvn_native.c", 'fopen(path, "wb")', "HOS_SHADER_DUMP", "the game's shader code, into the folder the switch names"),
    ("host/nvn_native.c", 'fopen(path, "wb")', "HOS_SHADER_DUMP", "its control section, beside it"),
    ("host/nn_audio.c", 'fopen(d, "wb")', "HOS_AUDIO_DUMP", "the sound as a WAV, to the file the switch names"),
    ("host/nn_audio.c", 'fopen(path, "wb")', "HOS_AUDIO_WB_DUMP", "the first wave buffers, into the folder the switch names"),
    ("host/pgo_rt.c", 'fopen(path, "wb")', "HOS_PGO", "branch counts, only in a profiling build (RX_PGO=gen), never shipped"),
    ("host/nn_vi.c", 'fopen(name, "w")', "HOS_SCAN_DT", "the guest-memory scan, to files the switch's prefix names"),
    ("host/nn_vi.c", 'fopen(path, "w")', "HOS_PRESENT_TRACE", "the present trace, to the file the switch names"),
    ("host/mkw_glue.c", 'fopen(getenv("HOS_SAMPLE_FILE"), "w")', "HOS_SAMPLE_FILE", "the sampled stacks"),
    ("host/mkw_glue.c", 'fopen(out, "a")', "HOS_WATCH", "the watched addresses' writers, beside the file the switch names"),
    ("host/mkw_glue.c", 'fopen(path, "a")', "HOS_SAMPLE_ALL", "every thread's samples, to the file the switch names"),
    ("host/hos_chain.c", 'fopen(path, "wb")', "HOS_CHAIN", "the call chains, to the file the switch names"),
    ("gpu/vk/vk_shader.cpp", 'fopen(path, "wb")', "RX_VK_DUMP_STAGES", "the game's programs, into the folder the switch names"),
    ("gpu/vk/vk_shader.cpp", 'fopen(path, "wb")', "RX_VK_DUMP_NATIVE", "the native code of the programs the switch names"),
    ("gpu/vk/vk_shader.cpp", 'fopen(path, "wb")', "RX_VK_DUMP_FAILED", "programs that failed, into the folder the switch names"),
    ("gpu/vk/vk_shader.cpp", 'fopen(path, "w")', "RX_VK_IR", "the IR of the programs the switch names"),
    ("gpu/vk/vk_shader.cpp", 'fopen(path, "wb")', "RX_VK_DUMP_SPV", "SPIR-V, into the folder the switch names"),
    ("gpu/vk/vk_shader.cpp", 'fopen(name, "wb")', "RX_VK_IR", "the SPIR-V of the programs the switch names"),
    ("gpu/vk/vk_shader.cpp", 'fopen(path, "wb")', "RX_VK_DUMP_MESH_SPV", "mesh SPIR-V, into the folder the switch names"),
    ("gpu/vk/vk_replay.cpp", 'fopen((dir.substr(0, dir.find_last_of("\\\\/") + 1) + "longshot_frames.csv").c_str(), "w")',
     "RX_VK_FRAME_TRACE", "a line each frame, longshot_frames.csv"),
    ("gpu/vk/vk_replay.cpp", 'std::fopen(getenv("RX_VK_FRAME_LOG"), "w")', "RX_VK_FRAME_LOG", "the frame times"),
    ("gpu/vk/vk_replay.cpp", 'fopen(framelog_path(), "wb")', "RX_VK_FRAMELOG", "every frame shown, to the file the switch names"),
    ("gpu/vk/vk_replay.cpp", 'fopen(name, "w")', "RX_VK_FRAMELOG", "a dark frame's conditions, from the frame log only"),
    ("gpu/vk/vk_hangdump.cpp", 'fopen(path, "w")', "RX_VK_HANG_DUMP", "the threads' stacks of a hang, to the file the switch names"),
    ("gpu/vk/vk_dump.cpp", 'fopen(rp.c_str(), "wb")', "RX_VK_DUMP", "raw float pictures, into the folder the switch names"),
    ("host/nn_fs.c", 'CreateFileA(path, GENERIC_READ | (want_write ? GENERIC_WRITE : 0), FILE_SHARE_READ | FILE_SHARE_WRITE, '
     'NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL)', None,
     "the game's own save file system (nn::fs) in the player's user folder: a save file the game made before (CREATE_NEW), "
     "opened again; no name the game folder ships"),
    ("host/discord_rpc.c", 'CreateFileA(name, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, SECURITY_SQOS_PRESENT | '
     'SECURITY_IDENTIFICATION, NULL)', None, "Discord's named pipe, \\\\.\\pipe\\discord-ipc-N: not a file"),
    ("gpu/vk/vk_main.cpp", 'CreateFileA("CONIN$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, '
     'OPEN_EXISTING, 0, nullptr)', None, "the console's input: not a file"),
    ("gpu/vk/vk_main.cpp", 'CreateFileA("CONOUT$", GENERIC_WRITE, FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr)', None,
     "the console's output: not a file"),
    ("host/hos_main.c", 'open("/dev/udmabuf", O_RDWR | O_CLOEXEC)', None, "a device, to ask whether udmabuf is there"),
    ("gpu/vk/vk_memory.cpp", 'open("/dev/udmabuf", O_RDWR | O_CLOEXEC)', None, "a device: udmabuf's memory"),
]

CALL = re.compile(r'(?<![\w.>])(?:std::|::)?(fopen|_wfopen|fopen_s|_wfopen_s|freopen|_wfreopen|open|_open|_wopen|creat|'
                  r'CreateFile[AW]?|CopyFile(?:Ex)?[AW]?|copy_file)\s*\(')
STREAM = re.compile(r'\b(?:std::)?(?:w?ofstream|w?fstream|basic_ofstream|basic_fstream)\b')
MODE = re.compile(r'^[rwa][rwabtx+]*$')


def strip(text):
    """text without its comments (the newlines kept, so lines still count) and string literals as they were"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * text.count("\n", i, j))
            i = j
        elif text.startswith('R"', i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            k = text.find("(", i)
            j = text.find(")" + text[i + 2:k] + '"', k)
            j = n if j < 0 else j + len(text[i + 2:k]) + 2
            out.append(text[i:j])
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def arguments(code, at):
    """the text between the parenthesis at `at` and its match"""
    depth, i = 0, at
    while i < len(code):
        c = code[i]
        if c in "\"'":
            j = i + 1
            while j < len(code) and code[j] != c:
                j += 2 if code[j] == "\\" else 1
            i = j
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if not depth:
                return code[at + 1:i]
        i += 1
    return code[at + 1:]


def writes(name, args):
    """whether this call opens a file for writing over what is at its name: True, False, or "new" (it makes the file new)"""
    if name in ("CopyFile", "CopyFileA", "CopyFileW", "CopyFileEx", "CopyFileExA", "CopyFileExW", "copy_file"):
        return True
    if name.startswith("CreateFile"):
        if not re.search(r"\b(GENERIC_WRITE|GENERIC_ALL|FILE_APPEND_DATA|FILE_WRITE_DATA)\b", args):
            return False
        return "new" if re.search(r"\b(CREATE_NEW|FILE_FLAG_OPEN_REPARSE_POINT)\b", args) else True
    if name in ("open", "_open", "_wopen", "creat"):
        if name != "creat" and not re.search(r"\b_?O_(WRONLY|RDWR|CREAT|APPEND|TRUNC)\b", args):
            return False
        return "new" if re.search(r"\bO_(EXCL|NOFOLLOW)\b", args) else True
    modes = [m for m in re.findall(r'"((?:[^"\\]|\\.)*)"', args) if MODE.match(m)]
    if not modes:
        return True   # (a mode that is not written out: looked at as a write)
    written = [m for m in modes if re.search(r"[wa+]", m)]
    if not written:
        return False
    return "new" if all("x" in m for m in written) else True


def sources():
    for folder in SOURCES:
        for f in sorted((ROOT / folder).rglob("*")):
            rel = f.relative_to(ROOT).as_posix()
            if f.suffix in (".c", ".cc", ".cpp", ".h", ".hpp", ".inc") and not rel.startswith(NOT_LOOKED_AT) \
                    and rel not in HELPERS:
                yield rel, f


def found_writes():
    """(file, call as written, line) of every write that does not go through hos_fopen_write and does not make its file new"""
    found = []
    for rel, f in sources():
        code = strip(f.read_text(encoding="utf-8", errors="replace"))
        for m in CALL.finditer(code):
            args = arguments(code, m.end() - 1)
            if writes(m.group(1), args) is True:
                call = re.sub(r"\s+", " ", code[m.start():m.end()].replace(" (", "(") + args + ")").strip()
                call = re.sub(r"\(\s+", "(", call)
                found.append((rel, call, code.count("\n", 0, m.start()) + 1))
        for m in STREAM.finditer(code):
            found.append((rel, m.group(0), code.count("\n", 0, m.start()) + 1))
    return found


def check_scanner():
    """the scanner itself, on calls it must see and calls it must let pass (the check would pass on nothing otherwise)"""
    cases = [('fopen(p, "w")', True), ('std::fopen(p.c_str(), "ab")', True), ('fopen(p, "r+b")', True),
             ('fopen(p, fresh ? "wb" : "ab")', True), ('fopen(p, mode)', True), ('freopen(p, "w", stderr)', True),
             ('_wfopen(p, L"wb")', True), ('open(p, O_WRONLY | O_CREAT, 0644)', True), ('creat(p, 0644)', True),
             ('CreateFileW(p, GENERIC_WRITE, 0, NULL, OPEN_ALWAYS, 0, NULL)', True), ('CopyFileW(a, b, FALSE)', True),
             ('fopen(p, "rb")', False), ('fopen(p, "r")', False), ('fopen(p, "wbx")', "new"),
             ('open(p, O_RDONLY | O_CLOEXEC)', False), ('open(p, O_WRONLY | O_CREAT | O_EXCL, 0644)', "new"),
             ('open(p, O_WRONLY | O_CREAT | O_NOFOLLOW, 0644)', "new"),
             ('CreateFileW(p, GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL)', "new"),
             ('CreateFileW(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL)', False)]
    bad = []
    for text, want in cases:
        m = CALL.search(text)
        got = writes(m.group(1), arguments(text, m.end() - 1)) if m else None
        if got != want:
            bad.append("%s: %r, expected %r" % (text, got, want))
    for text in ('f.open(p, "w")', 'hos_fopen_write(p, "w")', 'hos_fopen_read(p)', '// fopen(p, "w")'):
        stripped = strip(text)
        m = CALL.search(stripped)
        if m and writes(m.group(1), arguments(stripped, m.end() - 1)) is True:
            bad.append("%s: taken for a plain write" % text)
    if bad:
        sys.exit("the write scanner: " + "; ".join(bad))


def check_reviewed_switches(cc, tmp):
    """each REVIEWED switch is one a shared longshot_env.txt cannot set, with any value (env_line_allowed, host/hos_main.c)"""
    main_c = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    start = main_c.index("static int env_line_allowed(")
    end, depth = main_c.index("{", start) + 1, 1
    while depth:
        depth += (main_c[end] == "{") - (main_c[end] == "}")
        end += 1
    src = tmp / "reviewed_switches.c"
    src.write_text("#include <stdio.h>\n#include <string.h>\n" + main_c[start:end] +
                   '\nint main(int c, char **v) { for (int i = 1; i < c; i++) if (env_line_allowed(v[i], "1") || '
                   'env_line_allowed(v[i], "x")) puts(v[i]); return 0; }\n', encoding="utf-8")
    subprocess.run([cc, "-std=c11", "-Wall", "-Werror", str(src), "-o", str(tmp / "reviewed_switches")], check=True)
    switches = sorted({s for _, _, s, _ in REVIEWED if s})
    taken = subprocess.run([str(tmp / "reviewed_switches"), *switches], capture_output=True, text=True,
                           check=True).stdout.split()
    if taken:
        sys.exit("longshot_env.txt can turn on these reviewed writes: %s. Refuse the switch in env_line_allowed, or write "
                 "through hos_fopen_write" % ", ".join(taken))
    print("PASS: none of the %d switches of the reviewed writes can be set from longshot_env.txt" % len(switches))


def check_sources():
    check_scanner()
    found = found_writes()
    have = collections.Counter((rel, call) for rel, call, _ in found)
    want = collections.Counter((rel, call) for rel, call, _, _ in REVIEWED)
    bad = []
    for rel, call, line in found:
        if have[(rel, call)] > want[(rel, call)]:
            bad.append("%s:%d: %s writes through a link at its name: use hos_fopen_write (host/hos_fopen_write.h), or, "
                       "for a write a switch turns on, add it to REVIEWED with that switch" % (rel, line, call))
    for (rel, call), n in want.items():
        if have[(rel, call)] < n:
            bad.append("%s: REVIEWED has %d of %s, the source %d: take the stale entry out" % (rel, n, call, have[(rel, call)]))
    for rel, _, switch, why in REVIEWED:
        if not why:
            bad.append("%s: a REVIEWED entry without its reason" % rel)
        if switch and '"%s"' % switch not in (ROOT / rel).read_text(encoding="utf-8", errors="replace"):
            bad.append("%s: does not read %s, the switch REVIEWED gives it" % (rel, switch))
    files = sum(1 for _ in sources())
    if files < 150 or not any(rel == "gpu/vk/vk_progcache.cpp" for rel, _ in sources()):
        bad.append("only %d sources looked through: SOURCES or NOT_LOOKED_AT is wrong" % files)
    for rel, want_n in READS.items():
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        got = text.count("hos_fopen_read(")
        if got != want_n:
            bad.append("%s: %d reads through hos_fopen_read, %d expected" % (rel, got, want_n))
        for name in ("longshot_options.ini", "longshot_env.txt", "language.txt", "mouse_sens.txt"):
            for m in re.finditer(r'"%s"\);\s*\n\s*(?:#endif\s*\n\s*)?(?:FILE \*)?\w+ = fopen\(path, "r"\)' % re.escape(name), text):
                bad.append("%s: %s still read with fopen" % (rel, name))
    for rel, want_n in BINARY_READS.items():
        got = (ROOT / rel).read_text(encoding="utf-8", errors="replace").count("hos_fopen_read_binary(")
        if got != want_n:
            bad.append("%s: %d reads through hos_fopen_read_binary, %d expected" % (rel, got, want_n))
    for rel, want_n in NOFOLLOW_READS.items():
        got = (ROOT / rel).read_text(encoding="utf-8", errors="replace").count("hos_fopen_read_nofollow(")
        if got != want_n:
            bad.append("%s: %d reads through hos_fopen_read_nofollow, %d expected" % (rel, got, want_n))
    report = strip((ROOT / "gpu/vk/vk_bug_report.cpp").read_text(encoding="utf-8"))
    copy_log = report[report.index("LogFacts copy_log("):]
    copy_log = copy_log[:copy_log.index("\n}\n")]
    if "hos_fopen_read_nofollow(from.c_str())" not in copy_log or re.search(r"\bfopen\(", copy_log):
        bad.append("vk_bug_report.cpp: copy_log reads the logs (sent with the report) other than through hos_fopen_read_nofollow")
    if bad:
        sys.exit("\n".join(bad))
    print("PASS: in %d sources under host/ and gpu/vk/, every write goes through hos_fopen_write, makes its file new, or "
          "is one of the %d reviewed" % (files, len(REVIEWED)))


FIXTURE = r'''
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "hos_fopen_write.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); return 1; } } while (0)
static int text_is(const char *path, const char *want) {
    char got[64] = {0};
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    size_t n = fread(got, 1, sizeof got - 1, f);
    fclose(f);
    return n == strlen(want) && !memcmp(got, want, n);
}
int main(int argc, char **argv) {
    const char *dir = argv[1];   /* (a folder: hos_fopen_read refuses it) */
    char target[512], link[512], dangling[512], nowhere[512], plain[512], fifo[512], missing[512], log[512], other[512];
    snprintf(target, sizeof target, "%s/target", dir);
    snprintf(link, sizeof link, "%s/longshot_options.ini", dir);
    snprintf(dangling, sizeof dangling, "%s/language.txt", dir);
    snprintf(nowhere, sizeof nowhere, "%s/made_by_the_link", dir);
    snprintf(plain, sizeof plain, "%s/mouse_sens.txt", dir);
    snprintf(fifo, sizeof fifo, "%s/fifo", dir);
    snprintf(missing, sizeof missing, "%s/longshot_programs.bin", dir);
    snprintf(log, sizeof log, "%s/longshot_log.txt", dir);
    snprintf(other, sizeof other, "%s/other.txt", dir);
    const char *modes[] = {"w", "wb", "a", "ab", "r+", "r+b"};
    for (int i = 0; i < 6; i++) {
        CHECK(hos_fopen_write(link, modes[i]) == NULL);
        CHECK(text_is(target, "the player's file"));
        CHECK(hos_fopen_write(dangling, modes[i]) == NULL);
        struct stat st;
        CHECK(lstat(nowhere, &st) != 0);
        CHECK(hos_fopen_write(fifo, modes[i]) == NULL);   /* (returns: a FIFO is not waited on) */
    }
    FILE *f = hos_fopen_write(plain, "w");
    CHECK(f && fputs("first", f) >= 0 && fclose(f) == 0 && text_is(plain, "first"));
    f = hos_fopen_write(plain, "ab");
    CHECK(f && fputs("+more", f) >= 0 && fclose(f) == 0 && text_is(plain, "first+more"));
    f = hos_fopen_write(plain, "wb");
    CHECK(f && fputs("new", f) >= 0 && fclose(f) == 0 && text_is(plain, "new"));
    CHECK(hos_fopen_write(plain, "r") == NULL && hos_fopen_write(plain, "rb") == NULL);
    /* ("r+b": an existing file, read and written where it stands, not emptied; cut back as the program cache does) */
    CHECK(hos_fopen_write(missing, "r+b") == NULL && access(missing, F_OK) != 0);
    f = hos_fopen_write(plain, "r+b");
    char head[4] = {0};
    CHECK(f && fread(head, 1, 3, f) == 3 && !memcmp(head, "new", 3) && ftruncate(fileno(f), 2) == 0 && fclose(f) == 0);
    CHECK(text_is(plain, "ne"));
    puts("PASS: hos_fopen_write refuses links and FIFOs, writes, empties, appends to and updates regular files");
    /* (hos_freopen_write: the stream goes to the file, or stays as it was) */
    FILE *s = fopen(other, "w");
    CHECK(s && !hos_freopen_write(link, "w", s) && fputs("kept", s) >= 0 && fflush(s) == 0 && text_is(other, "kept"));
    CHECK(text_is(target, "the player's file"));
    CHECK(hos_freopen_write(log, "w", s) && fputs("logged", s) >= 0 && fflush(s) == 0 && fclose(s) == 0);
    CHECK(text_is(log, "logged") && text_is(other, "kept"));
    puts("PASS: hos_freopen_write puts a stream on a regular file, not through a link");
    /* (hos_fopen_read: a regular file, also through a link, but never a FIFO, which it does not wait on) */
    f = hos_fopen_read(plain);
    char got[64] = {0};
    CHECK(f && fgets(got, sizeof got, f) && !strcmp(got, "ne") && fclose(f) == 0);
    f = hos_fopen_read(link);
    CHECK(f && fgets(got, sizeof got, f) && !strcmp(got, "the player's file") && fclose(f) == 0);
    CHECK(hos_fopen_read(fifo) == NULL && hos_fopen_read(dir) == NULL && hos_fopen_read_binary(fifo) == NULL);
    puts("PASS: hos_fopen_read reads regular files and refuses FIFOs and folders without waiting");
    /* (hos_fopen_read_nofollow, the bug report's copies of the logs: a regular file only, and never through a link at its
       name, to a file or dangling) */
    f = hos_fopen_read_nofollow(plain);
    CHECK(f && fgets(got, sizeof got, f) && !strcmp(got, "ne") && fclose(f) == 0);
    CHECK(hos_fopen_read_nofollow(link) == NULL && hos_fopen_read_nofollow(dangling) == NULL);
    CHECK(hos_fopen_read_nofollow(fifo) == NULL && hos_fopen_read_nofollow(dir) == NULL);
    CHECK(hos_fopen_read_nofollow(missing) == NULL && access(missing, F_OK) != 0);
    puts("PASS: hos_fopen_read_nofollow reads regular files, not through a link, and refuses FIFOs and folders without waiting");
    return 0;
}
'''


# (the Windows branch, built here with stand-ins for windows.h and io.h around one name: what is at it (nothing, a
# plain file, a reparse point with its tag, a folder), its file ID, and the ID a second open finds there)
WINDOWS_H = r'''#pragma once
#include <stddef.h>
#include <stdint.h>
#include <wchar.h>
typedef unsigned long DWORD;
typedef int BOOL;
typedef unsigned int UINT;
typedef void *HANDLE;
typedef void *LPSECURITY_ATTRIBUTES;
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define GENERIC_READ 0x80000000UL
#define GENERIC_WRITE 0x40000000UL
#define FILE_READ_ATTRIBUTES 0x80UL
#define FILE_SHARE_READ 1UL
#define FILE_SHARE_WRITE 2UL
#define FILE_SHARE_DELETE 4UL
#define CREATE_NEW 1UL
#define OPEN_EXISTING 3UL
#define FILE_ATTRIBUTE_DIRECTORY 0x10UL
#define FILE_ATTRIBUTE_NORMAL 0x80UL
#define FILE_ATTRIBUTE_REPARSE_POINT 0x400UL
#define FILE_FLAG_OPEN_REPARSE_POINT 0x00200000UL
#define FILE_FLAG_BACKUP_SEMANTICS 0x02000000UL
#define FILE_TYPE_DISK 1UL
#define ERROR_FILE_NOT_FOUND 2UL
#define ERROR_FILE_EXISTS 80UL
#define CP_UTF8 65001U
#define MB_ERR_INVALID_CHARS 8UL
typedef enum { FileAttributeTagInfo = 9 } FILE_INFO_BY_HANDLE_CLASS;
typedef struct { DWORD FileAttributes, ReparseTag; } FILE_ATTRIBUTE_TAG_INFO;
typedef struct { DWORD dwFileAttributes, dwVolumeSerialNumber, nFileIndexHigh, nFileIndexLow; } BY_HANDLE_FILE_INFORMATION;
struct fake_name { int there; DWORD attributes, tag, id, attributes_again, tag_again, id_again; };
extern struct fake_name fake;
extern int fake_write_opens, fake_reparse_write_opens, fake_data_opens, fake_creates, fake_emptied, fake_closes, fake_osf_flags,
    fake_utf8;
extern DWORD fake_error, fake_access;
extern const char *fake_file;
#define LOOK ((HANDLE)(intptr_t)1)
#define FILE_H ((HANDLE)(intptr_t)2)
static DWORD GetLastError(void) { return fake_error; }
static int MultiByteToWideChar(UINT cp, DWORD flags, const char *s, int n, wchar_t *w, int cap) {
    (void)n;
    fake_utf8 = cp == CP_UTF8 && (flags & MB_ERR_INVALID_CHARS);
    const int len = (int)strlen(s) + 1;
    if (w) for (int i = 0; i < len && i < cap; i++) w[i] = (wchar_t)s[i];
    return len;
}
/* (LOOK: an open for attributes only, what was at the name first; FILE_H: an open to read or write, what is there then) */
static HANDLE CreateFileW(const wchar_t *w, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD how, DWORD flags, HANDLE t) {
    (void)w, (void)share, (void)sa, (void)t;
    const int data = (access & (GENERIC_READ | GENERIC_WRITE)) != 0;
    if (data) fake_data_opens++, fake_access = access;
    if (access & GENERIC_WRITE) fake_write_opens++;
    if ((access & GENERIC_WRITE) && (flags & FILE_FLAG_OPEN_REPARSE_POINT)) fake_reparse_write_opens++;
    if (how == CREATE_NEW) {
        if (fake.there) { fake_error = ERROR_FILE_EXISTS; return INVALID_HANDLE_VALUE; }
        fake.there = 1, fake.attributes = fake.attributes_again = 0x20, fake.tag = fake.tag_again = 0, fake_creates++;
        return FILE_H;
    }
    if (!fake.there) { fake_error = ERROR_FILE_NOT_FOUND; return INVALID_HANDLE_VALUE; }
    return data ? FILE_H : LOOK;
}
static DWORD GetFileType(HANDLE h) { (void)h; return FILE_TYPE_DISK; }
static BOOL GetFileInformationByHandleEx(HANDLE h, FILE_INFO_BY_HANDLE_CLASS c, void *out, DWORD n) {
    (void)n;
    if (c != FileAttributeTagInfo) return 0;
    FILE_ATTRIBUTE_TAG_INFO *i = (FILE_ATTRIBUTE_TAG_INFO *)out;
    i->FileAttributes = h == LOOK ? fake.attributes : fake.attributes_again, i->ReparseTag = h == LOOK ? fake.tag : fake.tag_again;
    return 1;
}
static BOOL GetFileInformationByHandle(HANDLE h, BY_HANDLE_FILE_INFORMATION *i) {
    i->dwVolumeSerialNumber = 7, i->nFileIndexHigh = 0, i->nFileIndexLow = h == LOOK ? fake.id : fake.id_again;
    return 1;
}
static BOOL SetEndOfFile(HANDLE h) { (void)h; fake_emptied++; return 1; }
static BOOL CloseHandle(HANDLE h) { (void)h; fake_closes++; return 1; }
'''
IO_H = r'''#pragma once
#include <stdint.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#define _O_RDONLY 0x0000
#define _O_APPEND 0x0008
#define _O_TEXT 0x4000
static int _open_osfhandle(intptr_t h, int flags) {
    (void)h;
    fake_osf_flags = flags;
    return open(fake_file, O_RDWR | O_CREAT, 0644);
}
static FILE *_fdopen(int fd, const char *mode) { return fdopen(fd, mode); }
static int _close(int fd) { return close(fd); }
'''
WINDOWS_FIXTURE = r'''#include <stdio.h>
#include <string.h>
#include <windows.h>
#include <io.h>
#include "hos_fopen_write.h"
struct fake_name fake;
int fake_write_opens, fake_reparse_write_opens, fake_data_opens, fake_creates, fake_emptied, fake_closes, fake_osf_flags, fake_utf8;
DWORD fake_error, fake_access;
const char *fake_file;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); return 1; } } while (0)
/* (what is at the name when it is looked at, and when it is opened to write or read; mode "R": hos_fopen_read_nofollow) */
static int opens(int there, DWORD attributes, DWORD tag, DWORD id, DWORD attributes_again, DWORD tag_again, DWORD id_again,
                 const char *mode) {
    fake.there = there, fake.attributes = attributes, fake.tag = tag, fake.id = id;
    fake.attributes_again = attributes_again, fake.tag_again = tag_again, fake.id_again = id_again;
    fake_write_opens = fake_reparse_write_opens = fake_data_opens = fake_creates = fake_emptied = fake_closes = 0;
    fake_osf_flags = -1, fake_error = 0, fake_access = 0;
    FILE *f = !strcmp(mode, "R") ? hos_fopen_read_nofollow("longshot_log.txt") : hos_fopen_write("longshot_options.ini", mode);
    if (f) fclose(f);
    return f != NULL;
}
static int writes(int there, DWORD attributes, DWORD tag, DWORD id_again, const char *mode) {
    return opens(there, attributes, tag, 5, attributes, tag, id_again, mode);
}
int main(int argc, char **argv) {
    (void)argc;
    fake_file = argv[1];
    /* (nothing at the name: made new, and only new) */
    CHECK(writes(0, 0, 0, 5, "w") && fake_creates == 1 && fake_utf8);
    /* (a plain file: opened for writing once, as itself; emptied for "w", appended to for "a", written where it stands for
       "r+b") */
    CHECK(writes(1, 0x20, 0, 5, "w") && fake_creates == 0 && fake_emptied == 1 && fake_osf_flags == _O_TEXT);
    CHECK(fake_write_opens == 1 && fake_reparse_write_opens == 1);
    CHECK(writes(1, 0x20, 0, 5, "ab") && fake_emptied == 0 && fake_osf_flags == _O_APPEND);
    CHECK(writes(1, 0x20, 0, 5, "r+b") && fake_emptied == 0 && fake_osf_flags == 0 && (fake_access & GENERIC_READ));
    CHECK(!writes(0, 0, 0, 5, "r+b") && fake_creates == 0 && fake_write_opens == 0);
    /* (a file system that makes up the file index for each open (redirectors, FUSE, shared folders): plain files still
       written, as no index is compared for them) */
    CHECK(writes(1, 0x20, 0, 6, "w") && fake_emptied == 1);
    CHECK(opens(1, 0x20, 0, 0, 0x20, 0, 0, "w") && fake_emptied == 1);
    /* (OneDrive's files, reparse points that are not name surrogates, are written, opened again without the flag) */
    CHECK(writes(1, 0x420, 0x9000001AUL, 5, "w") && fake_write_opens == 1 && fake_reparse_write_opens == 0);   /* (CLOUD_6) */
    CHECK(writes(1, 0x420, 0x9000701AUL, 5, "w"));                                       /* (a cloud tag's other flavour) */
    /* (name surrogates are refused before anything opens the name to write or read: symbolic link, junction, WSL's link,
       WCI_LINK_1, GLOBAL_REPARSE) */
    const DWORD links[] = {0xA000000CUL, 0xA0000003UL, 0xA000001DUL, 0xA0000027UL, 0xA0000019UL};
    for (int i = 0; i < 5; i++) {
        CHECK(!writes(1, 0x420, links[i], 5, "w") && fake_data_opens == 0 && fake_emptied == 0);
        CHECK(!writes(1, 0x420, links[i], 5, "ab") && fake_data_opens == 0);
        CHECK(!writes(1, 0x420, links[i], 5, "R") && fake_data_opens == 0);
    }
    /* (something else put at the name between the look and the open: refused, closed, not emptied. A plain file that is a
       link by the time it is opened (the open, as itself, sees it); a OneDrive file that is another file by then (its
       index); a OneDrive file whose index is 0, which proves nothing, is not opened at all) */
    CHECK(!opens(1, 0x20, 0, 5, 0x420, 0xA000000CUL, 5, "w") && fake_emptied == 0 && fake_closes == 2);
    CHECK(!opens(1, 0x20, 0, 5, 0x10, 0, 5, "w") && fake_emptied == 0 && fake_closes == 2);
    CHECK(!writes(1, 0x420, 0x9000001AUL, 6, "w") && fake_emptied == 0 && fake_closes == 2);
    CHECK(!opens(1, 0x420, 0x9000001AUL, 0, 0x420, 0x9000001AUL, 0, "w") && fake_write_opens == 0);
    /* (a folder; a mode that does not write) */
    CHECK(!writes(1, 0x10, 0, 5, "w") && fake_write_opens == 0);
    CHECK(!writes(1, 0x20, 0, 5, "r") && fake_write_opens == 0);
    /* (hos_fopen_read_nofollow, the bug report's logs: a plain file and OneDrive's are read, opened for reading only; nothing
       is made where nothing is; a link put at the name in between is refused) */
    CHECK(writes(1, 0x20, 0, 5, "R") && fake_write_opens == 0 && fake_access == (GENERIC_READ | FILE_READ_ATTRIBUTES));
    CHECK(fake_osf_flags == _O_RDONLY);
    CHECK(writes(1, 0x420, 0x9000001AUL, 5, "R") && fake_write_opens == 0);
    CHECK(!writes(0, 0, 0, 5, "R") && fake_creates == 0 && fake_data_opens == 0);
    CHECK(!opens(1, 0x20, 0, 5, 0x420, 0xA0000003UL, 5, "R") && fake_closes == 2);
    puts("PASS: on Windows, hos_fopen_write and hos_fopen_read_nofollow refuse name surrogates and a name swapped under them, "
         "write and read OneDrive's and plain files, and need no stable file index for plain ones");
    return 0;
}
'''


def check_windows(cc, tmp):
    fake = tmp / "fake"
    fake.mkdir()
    (fake / "windows.h").write_text(WINDOWS_H, encoding="utf-8")
    (fake / "io.h").write_text(IO_H, encoding="utf-8")
    (tmp / "win.c").write_text(WINDOWS_FIXTURE, encoding="utf-8")
    exe = tmp / "win"
    subprocess.run([cc, "-std=c11", "-D_WIN32", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Werror", "-Wno-unused-function",
                    "-include", "string.h", "-I", str(fake), "-I", str(ROOT / "host"), str(tmp / "win.c"), "-o", str(exe)],
                   check=True)
    subprocess.run([str(exe), str(tmp / "plain_win.txt")], check=True, timeout=30)


# (with Windows' own headers: the header builds as C and as C++, as the host and the renderer include it, with the CRT calls
# the program cache makes on what it returns)
REAL_WINDOWS = r'''#include "hos_fopen_write.h"
int main(int argc, char **argv) {
    FILE *f = hos_fopen_write(argc > 1 ? argv[1] : "x", "r+b");
    if (f) _chsize_s(_fileno(f), 2), fclose(f);
    return !hos_freopen_write("longshot_log.txt", "w", stderr);
}
'''


def check_real_windows(tmp):
    """LONGSHOT_MINGW=<llvm-mingw> (or clang on Windows): built against the real windows.h, warnings as errors"""
    mingw = os.environ.get("LONGSHOT_MINGW", "")
    tools = [os.path.join(mingw, "bin", "x86_64-w64-mingw32-" + n) for n in ("clang", "clang++")] if mingw else []
    if not tools or not all(os.path.exists(c) for c in tools):
        print("SKIP: hos_fopen_write.h against Windows' headers (LONGSHOT_MINGW=<llvm-mingw> builds it)")
        return
    for cc, std, ext in zip(tools, ("-std=c11", "-std=c++20"), (".c", ".cpp")):
        src = tmp / ("real_windows" + ext)
        src.write_text(REAL_WINDOWS, encoding="utf-8")
        subprocess.run([cc, std, "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "host"), str(src), "-o",
                        str(tmp / ("real_windows" + ext + ".exe"))], check=True)
    print("PASS: hos_fopen_write.h builds against Windows' headers, as C and as C++")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    check_sources()
    # (the back end's objects are keyed by the headers they see: the host's it includes must be among them, or a change
    # to hos_fopen_write.h left vk_draw.cpp's object with the old code)
    included = set()
    for f in (ROOT / "gpu" / "vk").glob("*.[ch]*"):
        included.update(re.findall(r'#include "\.\./\.\./host/([\w.]+)"', f.read_text(encoding="utf-8", errors="replace")))
    for h in list(included):   # (and the host headers those include)
        if (ROOT / "host" / h).exists():
            included.update(re.findall(r'#include "([\w.]+)"', (ROOT / "host" / h).read_text(encoding="utf-8")))
    build = (ROOT / "nx2" / "build.py").read_text(encoding="utf-8")
    keyed = re.search(r'vdeps \+= b"".join\(open\(os\.path\.join\(HOST, h\), "rb"\)\.read\(\) for h in \(([^)]*)\)\)', build)
    keyed = set(re.findall(r'"([\w.]+)"', keyed.group(1))) if keyed else set()
    if not included or not included <= keyed:
        sys.exit("nx2/build.py: the back end's object keys leave out the host headers it includes: %s"
                 % ", ".join(sorted(included - keyed)))
    print("PASS: the back end's object keys hold the %d host headers it includes" % len(included))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        check_reviewed_switches(a.cc, tmp)
        src = tmp / "fopen_write.c"
        src.write_text(FIXTURE, encoding="utf-8")
        exe = tmp / "fopen_write"
        subprocess.run([a.cc, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Werror", "-I", str(ROOT / "host"),
                        str(src), "-o", str(exe)], check=True)
        files = tmp / "game"
        files.mkdir()
        (files / "target").write_text("the player's file", encoding="utf-8")
        os.symlink(files / "target", files / "longshot_options.ini")
        os.symlink(files / "made_by_the_link", files / "language.txt")
        os.mkfifo(files / "fifo")
        r = subprocess.run([str(exe), str(files)], capture_output=True, text=True, timeout=30)
        print(r.stdout, end="")
        if r.returncode:
            sys.exit(r.stdout + r.stderr)
        # (a refusal is said in the log, once, naming the file)
        said = [l for l in r.stderr.splitlines() if "the game does not write through it" in l]
        if len(said) != 1 or "longshot_options.ini" not in said[0]:
            sys.exit("hos_fopen_write: a refusal is not said once in the log: %r" % r.stderr)
        print("PASS: a refused write is said once in the log, with the file's name")
        check_windows(a.cc, tmp)
        check_real_windows(tmp)


if __name__ == "__main__":
    main()
