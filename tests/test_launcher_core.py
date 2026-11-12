"""The launcher's core library (launcher/Core): tests/launcher_core compiles its sources and checks them over authored inputs, with
no game, no network and no window. The version vectors (TRACKS, ORDER, UNORDERED, LIST) are this file's own; the hand-over samples
are tests/fixtures/launcher's (docs/launcher-upgrade.md). Its scratch folder is named "ゲーム Zélda 🎮", as tests/test_utf8_paths.py's.

    python -I tests/test_launcher_core.py [--dotnet <dotnet>] [--strict]

With no dotnet here the check is skipped (exit 0, saying why), unless --strict.
"""
import argparse
import ast
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--dotnet", "{dotnet}"]


# The version order the launcher keeps (launcher/Core/Versions.cs, docs/versioning.md): (version, channel)
TRACKS = [
    ("v2.0.7", "release"), ("v2.1.0", "release"),
    ("v2.1.0-rc.1", "rc"), ("v2.1.0-rc.12", "rc"),
    ("v2.1.0-rc.1-3-g1a2b3c4", "nightly"), ("v2.1.0-rc.1-0-g1a2b3c4", "nightly"), ("v2.0.14-12-gabcdef01", "nightly"),
    ("v2.0.5-3-g1a2b3c4-dirty", "nightly"),
    ("nightly-2029-c544828f", "nightly"), ("nightly-2029-c544828f-dirty", "nightly"), ("nightly-1-00000000", "nightly"),
    # (not a nightly's name: the count, then 8 lowercase hex)
    ("nightly-2029", "release"), ("nightly-2029-c544828", "release"), ("nightly-2029-C544828F", "release"),
    ("nightly-x-c544828f", "release"), ("nightly-1234567890-c544828f", "release"),
]
# newer, older (semver: a release candidate before its release, rc.N by number; nightlies by their count, a changed
# checkout after the build of its commit, and every one after the git describe names of a checkout's own build)
ORDER = [
    ("v2.1.0-rc.2", "v2.1.0-rc.1"), ("v2.1.0-rc.10", "v2.1.0-rc.9"), ("v2.1.0", "v2.1.0-rc.2"), ("v2.1.0-rc.1", "v2.0.14"),
    ("v2.0.10", "v2.0.9"), ("v3.0.0-rc.1", "v2.9.9"),
    ("nightly-2030-0a1b2c3d", "nightly-2029-c544828f"), ("nightly-10000-aaaaaaaa", "nightly-9999-bbbbbbbb"),
    ("nightly-2029-c544828f-dirty", "nightly-2029-c544828f"), ("nightly-2-aaaaaaaa", "v2.1.0-rc.3-248-gc544828f"),
    ("nightly-1-aaaaaaaa", "v9.0.0-rc.1-999-gabcdef01-dirty"),
]
# neither newer than the other: a nightly and a release or release candidate (a nightly's name has no version in it),
# the git describe names among themselves, and a name that is no version
UNORDERED = [
    ("nightly-2029-c544828f", "v2.1.0"), ("nightly-2029-c544828f", "v2.1.0-rc.3"), ("nightly-1-aaaaaaaa", "v0.0.1"),
    ("v2.1.0-rc.3-248-gc544828f", "v2.1.0"), ("v2.1.0-rc.3-248-gc544828f", "v2.0.14-12-gabcdef01"),
    ("nightly-2029-c544828f", "dev"), ("v2.1.0", "dev"), ("nightly-2029-c544828f", "nightly"),
]
# (a releases list: the rolling releases and the parts releases are not versions, and every channel's newest leaves them out)
LIST = """[{"tag_name":"nightly","draft":false},{"tag_name":"rc","draft":false,"prerelease":true},
{"tag_name":"v2.1.0-rc.2","draft":false},{"tag_name":"linux-parts"},
{"tag_name":"v2.1.0-rc.10","draft":false},{"tag_name":"v2.0.14","draft":false},{"tag_name":"v2.1.0-rc.9"}]"""


def vectors():
    return {"tracks": TRACKS, "order": ORDER, "unordered": UNORDERED, "list": LIST}


def check_languages():
    """launcher/Core/Languages.cs lists make_game.py's LANGUAGES, in its order (the codes --language takes)"""
    tree = ast.parse((ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8"))
    want = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "LANGUAGES")
    source = (ROOT / "launcher" / "Core" / "Languages.cs").read_text(encoding="utf-8")
    body = source[source.index("All = new[]"):source.index("};", source.index("All = new[]"))]
    have = re.findall(r'\("([^"]+)", "([^"]+)"\)', body)
    if have != [tuple(x) for x in want]:
        raise SystemExit("launcher/Core/Languages.cs differs from make_game.py's LANGUAGES:\n%s\n%s" % (have, want))


def check_licences():
    """The DLSS and FSR 4 licences the launcher shows before a build fetches the SDKs link the commits scripts/ngx_sdk.py and
    scripts/ffx_sdk.py fetch (CONTRIBUTING.md: a change to the pinned release changes the licence text the player accepts)"""
    source = (ROOT / "launcher" / "App" / "ViewModels" / "SettingsStepViewModel.cs").read_text(encoding="utf-8")
    for script in ("ngx_sdk.py", "ffx_sdk.py"):
        tree = ast.parse((ROOT / "scripts" / script).read_text(encoding="utf-8"))
        commit = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and len(n.targets) == 1
                      and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "COMMIT")
        if commit not in source:
            raise SystemExit("launcher/App/ViewModels/SettingsStepViewModel.cs's licence text does not name scripts/%s's COMMIT %s"
                             % (script, commit))


def main():
    check_languages()
    check_licences()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotnet", default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    dotnet = args.dotnet or shutil.which("dotnet") or (str(Path.home() / ".dotnet" / "dotnet")
                                                       if (Path.home() / ".dotnet" / "dotnet").is_file() else None)
    if not dotnet:
        if args.strict:
            raise SystemExit("no dotnet found (pass --dotnet)")
        print("skipped: no dotnet here (pass --dotnet <path>)")
        return
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1", DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1")
    if not env.get("DOTNET_ROOT") and os.path.isabs(dotnet):
        env["DOTNET_ROOT"] = os.path.dirname(os.path.realpath(dotnet))
    with tempfile.TemporaryDirectory(prefix="launcher-core-") as folder:
        root = Path(folder)
        (root / "vectors.json").write_text(json.dumps(vectors(), ensure_ascii=False), encoding="utf-8")
        out = root / "build"
        subprocess.run([dotnet, "build", str(ROOT / "tests" / "launcher_core" / "LauncherCoreTests.csproj"), "--configuration", "Release",
                        "--artifacts-path", str(out), "--disable-build-servers", "-nologo", "-v:q"], cwd=root, env=env, check=True)
        dll = next((out / "bin").rglob("LauncherCoreTests.dll"))
        scratch = root / "ゲーム Zélda 🎮"
        subprocess.run([dotnet, str(dll), str(root / "vectors.json"), str(ROOT / "tests" / "fixtures" / "launcher"), str(scratch)],
                       cwd=root, env=env, check=True, timeout=300)


if __name__ == "__main__":
    sys.exit(main())
