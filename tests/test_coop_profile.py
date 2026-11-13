"""Temporary profile storage and actual nn::fs mount/path routing, without game data.
Run with --mutation links|ticket|mount to remove one guard; the same test must fail.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


FIXTURE = r'''
#include "coop_profile.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#include <strings.h>
typedef int SRWLOCK;
#define SRWLOCK_INIT 0
#define AcquireSRWLockExclusive(x) ((void)(x))
#define ReleaseSRWLockExclusive(x) ((void)(x))
#define AcquireSRWLockShared(x) ((void)(x))
#define ReleaseSRWLockShared(x) ((void)(x))
static int CreateDirectoryA(const char *p, void *unused) {
    (void)unused; char b[1024]; snprintf(b, sizeof b, "%s", p);
    for (char *c = b; *c; c++) if (*c == '\\') *c = '/';
    return mkdir(b, 0700) == 0;
}
#endif
#define HOS_FS_PATH 1024
#define FS_PATH_MAX 0x302
static const char *hos_user_dir;
static void hos_log(const char *fmt, ...) { (void)fmt; }
static int hos_mods_romfs(const char *rel, char *out, size_t cap) { (void)rel; (void)out; (void)cap; return 0; }
typedef struct { char name[32]; char root[HOS_FS_PATH]; int writable; } Mount;
static Mount mounts[16];
static int nmounts;
static SRWLOCK mount_lock = SRWLOCK_INIT;
/* ACTUAL */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    hos_user_dir = argv[1];
    if (!coop_profile_init(argv[1], argc > 3 ? argv[3] : NULL)) return 3;
    if (!strcmp(argv[2], "prepare")) {
        if (!coop_profile_prepare()) return 4;
        puts(coop_profile_restart_token());
    } else if (!strcmp(argv[2], "failed")) {
        if (!coop_profile_prepare()) return 4;
        puts(coop_profile_restart_token()); coop_profile_restart_failed();
    } else if (!strcmp(argv[2], "mount")) {
        char out[1024]; int writable;
        if (!coop_profile_active() || !user_mount("save", "save/profile") ||
            !host_path("save:/slot_00/progress.sav", out, sizeof out, &writable) || !writable) return 5;
        for (char *p = out; *p; p++) if (*p == '\\') *p = '/';
        puts(out);
        if (!user_mount("temp", "temp") || !user_mount("cache", "cache/0")) return 6;
        if (!host_path("temp:/new", out, sizeof out, &writable) || !strstr(out, "coop-sessions")) return 7;
        if (!host_path("cache:/new", out, sizeof out, &writable) || !strstr(out, "coop-sessions")) return 8;
        if (host_path("save:/../outside", out, sizeof out, &writable)) return 9;
        if (!coop_profile_return() || strcmp(coop_profile_restart_token(), "normal")) return 10;
    } else if (!strcmp(argv[2], "normal")) {
        if (coop_profile_active() || strcmp(coop_profile_root(), argv[1])) return 11;
        puts(coop_profile_root());
    } else if (!strcmp(argv[2], "safe")) {
        return coop_profile_path_safe(argv[4]) ? 0 : 12;
    } else return 2;
    return 0;
}
'''


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    p.add_argument("--mutation", choices=["links", "ticket", "mount"])
    a = p.parse_args()
    root = a.source_root
    fs = (root / "host/nn_fs.c").read_text()
    actual = "\n".join(block(fs, x) for x in ["static const char *fs_user_dir(", "static int mount(",
                         "static int guest_path_ok(", "static int host_path(", "static void make_dirs(", "static int user_mount("])
    source = (root / "host/coop_profile.c").read_text()
    if a.mutation == "links":
        source = source.replace("if (lstat(p, &s))", "if (stat(p, &s))").replace("if (a & FILE_ATTRIBUTE_REPARSE_POINT) return -1;", "")
    elif a.mutation == "ticket":
        source = source.replace(" || memcmp(want, got, 32)", "")
    elif a.mutation == "mount":
        actual = actual.replace("return p[0] ? p : hos_user_dir;", "(void)p; return hos_user_dir;")
    with tempfile.TemporaryDirectory(prefix="coop-profile-") as t:
        tmp = Path(t)
        c = tmp / "profile.c"
        c.write_text(source)
        fixture = tmp / "fixture.c"
        fixture.write_text(FIXTURE.replace("/* ACTUAL */", actual))
        exe = tmp / "profile.exe"
        libs = ["-lws2_32", "-lbcrypt", "-liphlpapi"] if os.name == "nt" else ["-pthread"]
        subprocess.run([a.cc, "-std=c11", "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(root / "host"), str(fixture), str(c), str(root / "host/coop_net.c"),
                        str(root / "host/coop_proto.c"), str(root / "third_party/monocypher/monocypher.c"),
                        "-o", str(exe)] + libs, check=True)
        user = tmp / "user-é"
        slot = user / "save/profile/slot_00"
        slot.mkdir(parents=True)
        (slot / "progress.sav").write_bytes(bytes(range(256)) * 720)
        (slot / "caption.sav").write_bytes(b"authored caption")
        (user / "save/profile/account.sav").write_bytes(b"local account")
        (user / "coop-keys").mkdir()
        (user / "coop-keys/private").write_bytes(b"must not copy")
        before = hashes(user / "save")

        def run(action, token=None, expect=0, extra=()):
            cmd = [str(exe), str(user), action]
            if token is not None: cmd.append(token)
            r = subprocess.run(cmd + list(extra), capture_output=True, text=True)
            assert (r.returncode == 0) == (expect == 0), (action, r.returncode, r.stdout, r.stderr)
            return r.stdout.strip()

        failed = run("failed")
        assert not (user / "coop-sessions" / failed / "restart.ticket").exists()
        run("normal")
        token = run("prepare")
        session = user / "coop-sessions" / token
        assert not (session / "user/coop-keys").exists()
        copied = Path(run("mount", token))
        assert copied != slot / "progress.sav" and copied.read_bytes() == (slot / "progress.sav").read_bytes()
        copied.write_bytes(b"temporary autosave")
        run("normal")
        run("mount", token, expect=1)  # one-use ticket cannot resume after an ordinary restart/crash
        token = run("prepare")
        (user / "coop-sessions" / token / "user/save/profile/slot_00/progress.sav").write_bytes(b"altered")
        run("mount", token, expect=1)
        run("normal")
        run("mount", "../outside", expect=1)
        if os.name != "nt":
            outside = tmp / "outside"
            outside.mkdir()
            (slot / "escape").symlink_to(outside, target_is_directory=True)
            run("prepare", expect=1)
            run("safe", "", expect=1, extra=[str(slot / "escape/new")])
            (slot / "escape").unlink()
        assert hashes(user / "save") == before
        print("PASS: copy/restart/return, original byte hashes, private keys excluded, tampered/one-use/crash tickets, all user mounts, links and traversal")


if __name__ == "__main__":
    main()
