"""The optional official ANR FG-V.B15 addon, downloaded directly for Windows x64.

    python -I scripts/anr_sdk.py --fetch-anr-runtime [--game <game folder>] [<cache folder>]

Off without --fetch-anr-runtime. Only the addon's dlssg subtree goes into <game>/anr; no installer is run, no frame
generator is selected, and no ANR binaries or models are bundled or mirrored. The upstream binary/model redistribution
terms have not been established. Existing modified packages and nr_fg.ini are preserved.
"""
import argparse
import hashlib
import importlib.util
import os
import platform
import shutil
import stat
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("anr_tools", os.path.join(ROOT, "scripts", "tools.py"))
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)

VERSION = "FG-V.B15"
URL = "https://github.com/apex-compute/ANR/releases/download/FG-V.B15/anr-fg-V.B15-addon.zip"
PREFIX = "Apex Neural Rendering FG/dlssg"
TOOL = tools.Tool("anr", VERSION, "windows-x64", "717e93b3e63cb08dbaf6240cd585bae94d498c2749d8603d800d17ad97ef9e41",
                  3231158, "anr", PREFIX, [URL])
FILES = ("nvngx_dlssg.dll", "nr_fg.ini", "path_table.json", "size_table.json", "adapters/pack.nrx", "kernels/pack.nrx",
         "kernels-portable/pack.nrx") + tuple("programs/fg-lite-any-%s-%s/pack.nrx" % (kind, colour)
         for kind in ("coopmat", "dp4a", "packed", "portable-linalg", "portable", "portable-xmx")
         for colour in ("pq10", "srgb8"))
NOTICE = "ANR FG: optional official %s addon, downloaded directly from %s; no installer is run or generator selected." % (VERSION, URL)


def supported():
    return os.name == "nt" and platform.machine().lower() in ("amd64", "x86_64")


def archive_files(archive):
    """Validate the pinned archive and its complete regular-file subtree before extracting anything."""
    if os.path.getsize(archive) != TOOL.size or tools.sha256_file(archive) != TOOL.sha256:
        raise ValueError("ANR addon is not the expected size and SHA-256: not used")
    digests, seen = {}, set()
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if not member.filename.startswith(PREFIX + "/") or member.is_dir():
                continue
            name = member.filename[len(PREFIX) + 1:]
            mode = stat.S_IFMT(member.external_attr >> 16)
            if name not in FILES or name.casefold() in seen or mode not in (0, stat.S_IFREG):
                raise ValueError("ANR addon has an unexpected, duplicate or non-regular file: %s" % member.filename)
            seen.add(name.casefold())
            digests[name] = hashlib.sha256(z.read(member)).hexdigest()
    if set(digests) != set(FILES):
        raise ValueError("ANR addon is missing required files in %s" % PREFIX)
    return digests


def fetch(folder, getter=None, say=print):
    """Reuse the builder's size/SHA-256-checked ZIP cache; never leave a partial download."""
    if not supported():
        raise ValueError("ANR's optional provider is supported on Windows x64 only; nothing downloaded")
    partial = os.path.join(folder, TOOL.sha256 + ".zip.part")
    try:
        try:
            archive = tools.fetch(TOOL, folder, getter=getter)
        except SystemExit as e:
            raise ValueError(str(e)) from e
        archive_files(archive)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    say("anr-sdk: verified %s addon in %s" % (VERSION, archive))
    return archive


def linked(path):
    return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) if os.name == "nt" else os.path.islink(path)


def install(archive, game, say=print):
    """Stage the whole pinned subtree, then replace it as a unit. Preserve a modified package and the player's ini."""
    def unreadable(error):
        raise error

    digests = archive_files(archive)
    dst = os.path.join(game, "anr")
    if os.path.lexists(dst):
        modified = linked(dst) or not os.path.isdir(dst)
        logs = False
        if not modified:
            for directory, dirs, files in os.walk(dst, followlinks=False, onerror=unreadable):
                for name in dirs:
                    path = os.path.join(directory, name)
                    rel = os.path.relpath(path, dst).replace(os.sep, "/")
                    if linked(path) or not any(n.startswith(rel + "/") for n in FILES):
                        modified = True
                        break
                if modified:
                    break
                for name in files:
                    path = os.path.join(directory, name)
                    rel = os.path.relpath(path, dst).replace(os.sep, "/")
                    if linked(path) or not stat.S_ISREG(os.lstat(path).st_mode):
                        modified = True
                        break
                    if "/" not in rel and rel.startswith("nr_fg-") and rel.endswith(".log"):
                        logs = True
                        continue
                    if rel not in digests or (rel != "nr_fg.ini" and tools.sha256_file(path) != digests[rel]):
                        modified = True
                        break
                if modified:
                    break
        if modified:
            say("anr-sdk: %s is a modified or unrecognized package: left unchanged" % dst)
            return None
        if all(os.path.isfile(os.path.join(dst, *name.split("/"))) for name in FILES):
            say("anr-sdk: %s already has %s; nr_fg.ini kept" % (dst, VERSION))
            return dst
        if logs:
            say("anr-sdk: %s has missing package files and provider logs: left unchanged; install the complete addon manually" % dst)
            return None
    os.makedirs(game, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".anr-", dir=game)
    staged, backup = os.path.join(stage, "package"), os.path.join(stage, "previous")
    keep_backup = False
    try:
        tools.unpack(archive, PREFIX, staged)
        config = os.path.join(dst, "nr_fg.ini")
        if os.path.isfile(config):
            shutil.copyfile(config, os.path.join(staged, "nr_fg.ini"))
        if os.path.isdir(dst):
            os.replace(dst, backup)
        try:
            os.replace(staged, dst)
        except OSError:
            if os.path.isdir(backup):
                try:
                    os.replace(backup, dst)
                except OSError as e:
                    keep_backup = True
                    raise OSError("ANR replacement failed; the original package is preserved in %s" % backup) from e
            raise
    finally:
        if not keep_backup:
            shutil.rmtree(stage, ignore_errors=True)
    say("anr-sdk: %s installed in %s; choose ANR in the game's menu to use it" % (VERSION, dst))
    return dst


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folder", nargs="?", default=os.path.join(ROOT, "work", "anr-sdk"))
    p.add_argument("--fetch-anr-runtime", action="store_true", help="download the optional official ANR addon")
    p.add_argument("--game", help="install only the dlssg subtree into this game folder's anr folder")
    a = p.parse_args(argv)
    if not a.fetch_anr_runtime:
        return 0
    print(NOTICE, flush=True)
    try:
        archive = fetch(os.path.abspath(a.folder))
        if a.game:
            install(archive, os.path.abspath(a.game))
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        print("anr-sdk: %s. Close any game using the package, check the connection and retry." % e, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
