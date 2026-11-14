"""Native nn::fs mount separators reach real profile/export storage and signed manifests.
Authored files only. --mutation removes one guard in a temporary source copy;
compilation must succeed and a behavioral assertion must then fail.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
UID = "00000000000000000000000000000001"
MUTATIONS = ("export-path", "init-path", "missing-slot", "links", "parents", "file-list", "manifest", "normal-return")

FIXTURE = r'''
#include "coop_profile.h"
#include "coop_save.c"
#include <assert.h>
#include <stdio.h>
/* Single-thread fixture: exercise the real barrier predicates, not lock scheduling. */
#ifdef _WIN32
#include <windows.h>
#else
typedef int SRWLOCK;
typedef int LONG;
typedef int64_t LONG64;
#define SRWLOCK_INIT 0
#define AcquireSRWLockExclusive(p) ((void)(p))
#define ReleaseSRWLockExclusive(p) ((void)(p))
static LONG InterlockedCompareExchange(volatile LONG *p, LONG a, LONG b) { (void)a; (void)b; return *p; }
static LONG64 InterlockedCompareExchange64(volatile LONG64 *p, LONG64 a, LONG64 b) { (void)a; (void)b; return *p; }
#endif
static int64_t hos_now_ns(void) { return 1000000000; }
#define HOS_FS_PATH 1024
static const char *hos_user_dir;
static uint64_t args[3], result;
#define HLE(name) static void name(void)
#define ARG(n) args[n]
#define G64(a) (*(uint64_t *)(uintptr_t)(a))
#define RET(v) (result = (v))
#define R_NOT_MOUNTED 1
static const char *gstr(uint64_t a) { return (const char *)(uintptr_t)a; }
/* Mount table creation is not under test; actual HLE root construction is. */
static int user_mount(const char *name, const char *sub) { (void)name; (void)sub; return 1; }
/* ACTUAL */
static void hex(const void *data, size_t n) {
    const unsigned char *p = data;
    for (size_t i = 0; i < n; i++) printf("%02x", p[i]);
    putchar('\n');
}
static int binding(void *u, int member, CoopSaveBinding *out) {
    (void)u; assert(member == 2);
    uint8_t seed[32] = {1};
    *out = (CoopSaveBinding){.own_session = 101, .peer_session = 202, .epoch = 1};
    coop_sign_keypair(seed, out->sk, out->own);
    memset(out->peer, 2, 32); memset(out->room, 3, 16);
    return 1;
}
int main(int argc, char **argv) {
    assert(argc >= 4);
    hos_user_dir = argv[1];
    assert(coop_profile_init(argv[1], strcmp(argv[2], "-") ? argv[2] : NULL));
    if (!strcmp(argv[3], "prepare")) {
        assert(coop_profile_prepare()); puts(coop_profile_restart_token()); return 0;
    }
    if (!strcmp(argv[3], "read")) {
        assert(argc == 7);
        unsigned file = (unsigned)strtoul(argv[5], NULL, 10);
        size_t n = (size_t)strtoul(argv[6], NULL, 10);
        assert(n && n <= COOP_SAVE_MAX);
        uint8_t *bytes = malloc(n), hash[32]; assert(bytes);
        assert(coop_profile_export_read(argv[4], file, 0, bytes, n));
        coop_blake2b(hash, sizeof hash, NULL, 0, bytes, n); hex(hash, sizeof hash);
        free(bytes); return 0;
    }
    uint64_t uid[2] = {1, 0}; args[0] = (uintptr_t)"save"; args[1] = args[2] = (uintptr_t)uid;
    _ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE(); assert(!result);
    char first[HOS_FS_PATH]; strcpy(first, save_root);
    _ZN2nn2fs21MountSaveDataReadOnlyEPKcNS_13ApplicationIdERKNS_7account3UidE();
    assert(!result && !strcmp(first, save_root));
    assert(strstr(save_root, "\\save\\00000000000000000000000000000001"));
    if (argc > 4 && strcmp(argv[4], "-")) save_mounted(argv[4]);
    unsigned slot = argc > 5 ? (unsigned)strtoul(argv[5], NULL, 10) : 5;
    save_changed_ms = 1;
    printf("ROOT %s\nACTIVE %d\nSAFE %d\n", save_root, coop_profile_active(), coop_profile_path_safe(save_root));
    CoopSaveSource source = {0};
    int ok = hos_save_export(slot, &source);
    printf("EXPORT %d\n", ok); fflush(stdout);
    if (!strcmp(argv[3], "refuse")) { assert(!ok && "unsafe or missing slot must refuse"); return 0; }
    assert(ok && "export existing native slot");
    printf("ID %s\n", source.local_id);
    CoopSave save = {0}; save.ops.binding = binding;
    assert(coop_save_offer(&save, 2, &source, 1, 1));
    assert(coop_sign_check(save.out.offer + 288, save.out.binding.own, save.out.offer, 288));
    printf("MANIFEST "); hex(save.out.offer, sizeof save.out.offer);
    /* No normal-root return; exporting from normal is explicitly allowed and read-only. */
    assert(coop_profile_return() == coop_profile_active());
    return 0;
}
'''


def replace_once(text, before, after):
    assert text.count(before) == 1, before
    return text.replace(before, after, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--mutation", choices=MUTATIONS)
    args = parser.parse_args()
    root = args.source_root.resolve()
    spec = importlib.util.spec_from_file_location("fs_extract", ROOT / "tests/test_save_copy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fs = (root / "host/nn_fs.c").read_text()
    markers = ["static const char *fs_user_dir(", "#define SAVE_QUIET_MS", "static SRWLOCK save_lock",
               "static char save_root[", "static volatile LONG save_writers;", "static volatile LONG64 save_changed_ms;",
               "static void save_mounted(", "int hos_save_export(",
               "HLE(_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE)",
               "HLE(_ZN2nn2fs21MountSaveDataReadOnlyEPKcNS_13ApplicationIdERKNS_7account3UidE)"]
    profile = (root / "host/coop_profile.c").read_text()
    if args.mutation == "export-path":
        profile = replace_once(profile, "    native_path(base);", "    /* broken: keep the mount separators */")
    elif args.mutation == "init-path":
        profile = replace_once(profile, "    native_path(normal_root);", "    /* broken: keep the user-root separators */")
    elif args.mutation == "missing-slot":
        profile = replace_once(profile, 'snprintf(name, sizeof name, "slot_%02u", slot);',
                               'snprintf(name, sizeof name, "slot_%02u", slot == 99 ? 5 : slot);')
    elif args.mutation == "links":
        profile = replace_once(profile, "if (lstat(p, &s))", "if (stat(p, &s))")
        profile = replace_once(profile, "if (a & FILE_ATTRIBUTE_REPARSE_POINT) return -1;", "/* broken: follow reparse points */")
    elif args.mutation == "parents":
        profile = replace_once(profile, "        if (c - component == 2 && component[0] == '.' && component[1] == '.') return 0;",
                               "        (void)component; /* broken: allow parent components */")
    elif args.mutation == "file-list":
        profile = replace_once(profile, 'inventory(v, base, "", 0) && v->n == 2', 'inventory(v, base, "", 0) && v->n >= 2')
        profile = replace_once(profile, "        if (f < 0 || !v->e[i].size", "        if (f < 0) continue;\n        if (!v->e[i].size")
    elif args.mutation == "normal-return":
        profile = replace_once(profile, "    if (!coop_profile_active()) return 0;", "    /* broken: normal Return allowed */")
    elif args.mutation == "manifest":
        profile = replace_once(profile, "    src.generation = generation; *out = src;",
                               "    src.hash[0][0] ^= 1; src.generation = generation; *out = src;")
    with tempfile.TemporaryDirectory(prefix="coop-export-path-") as directory:
        tmp = Path(directory)
        (tmp / "profile.c").write_text(profile)
        (tmp / "fixture.c").write_text(FIXTURE.replace("/* ACTUAL */", "\n".join(module.piece(fs, m) for m in markers)))
        exe = tmp / "export-path.exe"
        libs = ["-lws2_32", "-lbcrypt", "-liphlpapi"] if os.name == "nt" else ["-pthread"]
        cmd = [args.cc, "-std=c11", "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
               "-I" + str(root / "host"), str(tmp / "fixture.c"), str(tmp / "profile.c"), str(root / "host/coop_net.c"),
               str(root / "host/coop_proto.c"), str(root / "third_party/monocypher/monocypher.c"), "-o", str(exe), *libs]
        print("compile:", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)
        print("compiled export-path fixture", flush=True)
        user = tmp / "normal" / "user"
        slot = user / "save" / UID / "slot_05"
        slot.mkdir(parents=True)
        authored = {"progress.sav": bytes(range(256)) * 40, "caption.sav": b"authored caption"}
        for name, data in authored.items(): (slot / name).write_bytes(data)
        (slot.parent / "account.sav").write_bytes(b"not a transferable file")

        def hashes(path):
            return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in path.rglob("*") if p.is_file()}

        original = hashes(user / "save")

        def run(action, token="-", mount="-", number=5, user_path=None):
            command = [str(exe), str(user_path or user), token, action, str(mount), str(number)]
            result = subprocess.run(command, capture_output=True, text=True)
            print(result.stdout, end="", flush=True)
            assert result.returncode == 0, (action, result.returncode, result.stdout, result.stderr)
            return dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)

        def capture(token="-", user_path=None):
            output = run("capture", token=token, user_path=user_path)
            manifest = bytes.fromhex(output["MANIFEST"])
            export = user / "coop-sessions" / output["ID"] / "export"
            assert sorted(p.name for p in export.iterdir()) == sorted(authored), "exact export file list"
            assert len(manifest) == 352 and struct.unpack_from("<Q", manifest, 140)[0] == 1
            sizes = tuple(len(authored[name]) for name in ("progress.sav", "caption.sav"))
            assert struct.unpack_from("<II", manifest, 156) == sizes
            for file, name in enumerate(("progress.sav", "caption.sav")):
                assert (export / name).read_bytes() == authored[name], "approved snapshot bytes"
                digest = hashlib.blake2b(authored[name], digest_size=32).digest()
                assert manifest[164 + 32 * file:196 + 32 * file] == digest, "manifest file hash"
                observed = subprocess.check_output([str(exe), str(user), "-", "read", output["ID"], str(file), str(sizes[file])], text=True).strip()
                assert observed == digest.hex(), "export read hashes"
            package = struct.pack("<II", *sizes) + authored["progress.sav"] + authored["caption.sav"]
            assert manifest[228:260] == hashlib.blake2b(package, digest_size=32).digest(), "manifest package hash"
            assert hashes(user / "save") == original, "normal save remains unchanged"
            print("PASS: exact signed file list, lengths, per-file/package hashes and read-only normal root", flush=True)
            return output, export

        capture()  # Both actual mount HLEs preserve their mixed separators into hos_save_export.
        # Profile initialization is the other external root boundary; native Linux and Windows forms.
        held_user = str(user).replace("/", "\\") if os.name != "nt" else str(user)
        capture(user_path=held_user)
        token = subprocess.check_output([str(exe), held_user, "-", "prepare"], text=True).strip()
        output, export = capture(token=token, user_path=held_user)
        assert output["ACTIVE"] == "1"
        copy_root = user / "coop-sessions" / token / "user" / "save" / UID
        (copy_root / "slot_05/progress.sav").write_bytes(b"later local save")
        assert (export / "progress.sav").read_bytes() == authored["progress.sav"], "immutable approval snapshot"
        run("refuse", number=99)
        outside = tmp / "outside"
        shutil_slot = outside / "slot_05"
        shutil_slot.mkdir(parents=True)
        for name, data in authored.items(): (shutil_slot / name).write_bytes(data)
        outside_before = hashes(outside)
        # Existing parent traversal must refuse even when it resolves to a complete slot.
        for mount in (str(user) + "/../../outside", str(user) + "\\..\\..\\outside"):
            run("refuse", mount=mount)
        if os.name != "nt":
            link = tmp / "linked-root"
            link.symlink_to(outside, target_is_directory=True)
            run("refuse", mount=str(link).replace("/", "\\"))
            (slot / "caption.sav").unlink()
            (slot / "caption.sav").symlink_to(shutil_slot / "caption.sav")
            run("refuse")
            (slot / "caption.sav").unlink()
            (slot / "caption.sav").write_bytes(authored["caption.sav"])
        (slot / "account.sav").write_bytes(b"must refuse a third slot file")
        run("refuse")
        (slot / "account.sav").unlink()
        assert hashes(outside) == outside_before and hashes(user / "save") == original
        print("PASS: missing slot, parent traversal and Linux root/file links refuse; copy/return and immutable snapshot", flush=True)


if __name__ == "__main__":
    main()
