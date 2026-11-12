"""LONGSHOT_OBJ_STORE (the dev-loop proposal's A10): one object cache for every work folder.

Two work folders over one store compile the same sources (build.py compile_one, with a stand-in compiler): the second
compiles none, and its objects are the store's (links where the drive allows). An object goes into the store whole or not at
all (cache_put): a copy that stops halfway leaves no entry and no temporary, and two builds putting the same object leave
one. The store is split by the toolchain's stamp (make_game.object_cache); without a store, or without a stamp, the work
folder's own objcache is used as before. The lists a lift reads that git does not keep reach a new worktree from the store
(seed_work), and the store follows the checkout that has them. The store's folder reaches build.py alone, not the lift's
environment (its key).

Nothing here runs a real compiler or needs the game.

    python -I -B tests/test_obj_store.py
"""
import importlib.util
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "nx2"), str(ROOT / "third_party" / "a64-kit" / "tools")]
spec = importlib.util.spec_from_file_location("build_obj_store", ROOT / "nx2" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
import make_game  # noqa: E402

STAMP = "0123456789abcdef0123456789abcdef01234567"
FAKE_CC = r"""
import sys
src, obj = sys.argv[sys.argv.index("-c") + 1], sys.argv[sys.argv.index("-o") + 1]
open(obj, "wb").write(b"OBJ:" + open(src, "rb").read())
open(sys.argv[1], "a").write(src + "\n")
"""


def compile_all(work, sources, cc, log):
    """build.py's compile_one for each source, as a build of this work folder runs it; the files it compiled"""
    obj_dir = work / "sa" / "build" / "obj"
    obj_dir.mkdir(parents=True, exist_ok=True)
    before = log.read_text().splitlines() if log.exists() else []
    build.PLANNED.clear()
    for name, text in sources.items():
        src = work / "sa" / "gen" / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(text)
        _, _, err = build.compile_one((sys.executable, str(src), str(obj_dir / (name[:-2] + ".o")), [str(cc), str(log), "-O1"],
                                       b"/* headers */"))
        assert err is None, err
    return log.read_text().splitlines()[len(before):]


def check_two_work_folders(tmp):
    store = tmp / "store" / "objects" / STAMP[:16]
    cc, log = tmp / "cc.py", tmp / "compiled.txt"
    cc.write_text(FAKE_CC)
    sources = {"code_000.c": b"int a;", "code_001.c": b"int b;", "hos_main.c": b"int main(void) { return 0; }"}
    with mock.patch.object(build, "OBJ_CACHE", str(store)):
        first = compile_all(tmp / "wt1" / "work", sources, cc, log)
        assert len(first) == 3, first
        assert sorted(p.suffix for p in store.iterdir()) == [".o"] * 3, list(store.iterdir())
        second = compile_all(tmp / "wt2" / "work", sources, cc, log)
        assert second == [], "the second work folder compiled %s" % second
        for name in sources:
            obj = tmp / "wt2" / "work" / "sa" / "build" / "obj" / (name[:-2] + ".o")
            key = obj.with_suffix(".o.sha1").read_text()
            assert obj.read_bytes() == (store / (key + ".o")).read_bytes() == b"OBJ:" + sources[name]
            assert os.path.samefile(obj, store / (key + ".o")), "%s is not the store's object" % name
        # a source of one work folder changed: that one alone
        changed = dict(sources, **{"code_001.c": b"int b2;"})
        assert [Path(p).name for p in compile_all(tmp / "wt1" / "work", changed, cc, log)] == ["code_001.c"]


def check_put_whole(tmp):
    cache = tmp / "cache"
    obj = tmp / "x.o"
    obj.write_bytes(b"x" * 4096)
    real_copy = build.shutil.copyfile

    def half_then_fail(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes()[:100])
        raise OSError(28, "No space left on device")
    with mock.patch.object(build, "OBJ_CACHE", str(cache)), mock.patch.object(build.os, "link", side_effect=OSError(18, "cross")), \
            mock.patch.object(build.shutil, "copyfile", half_then_fail):
        build.cache_put(str(obj), "k1")
    assert list(cache.iterdir()) == [], "a copy that stopped left %s" % list(cache.iterdir())
    # (a copy across drives that works: the whole object, by its key)
    with mock.patch.object(build, "OBJ_CACHE", str(cache)), mock.patch.object(build.os, "link", side_effect=OSError(18, "cross")):
        build.cache_put(str(obj), "k2")
    assert [p.name for p in cache.iterdir()] == ["k2.o"] and (cache / "k2.o").read_bytes() == obj.read_bytes()
    assert build.shutil.copyfile is real_copy
    # two builds put the same object at once: one entry, no temporaries
    with mock.patch.object(build, "OBJ_CACHE", str(cache)):
        threads = [threading.Thread(target=build.cache_put, args=(str(obj), "k3")) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert sorted(p.name for p in cache.iterdir()) == ["k2.o", "k3.o"], list(cache.iterdir())


def check_object_cache(tmp):
    work = tmp / "w"
    work.mkdir()
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(make_game.OBJ_STORE, None)
        assert make_game.object_cache(str(work)) == os.path.join(str(work), "objcache")
        os.environ[make_game.OBJ_STORE] = str(tmp / "store")
        # (no stamp yet: the work folder's own)
        assert make_game.object_cache(str(work)) == os.path.join(str(work), "objcache")
        (work / "system_headers.txt").write_text(STAMP + "\n")
        assert make_game.object_cache(str(work)) == os.path.join(str(tmp / "store"), "objects", STAMP[:16])
        (work / "system_headers.txt").write_text("not a stamp\n")
        assert make_game.object_cache(str(work)) == os.path.join(str(work), "objcache")


def check_seeds(tmp):
    main, fresh, store = tmp / "main", tmp / "fresh", tmp / "seedstore"
    (main / "work" / "image").mkdir(parents=True)
    (main / "work" / "hot_funcs.txt").write_text("80001000 12\n")
    (main / "work" / "image" / "chunks.txt").write_text("0x80000000\n")
    for w in (main / "w", fresh / "w"):
        (w / "image").mkdir(parents=True)
    with mock.patch.dict(os.environ, {make_game.OBJ_STORE: str(store)}):
        with mock.patch.object(make_game, "ROOT", str(main)):
            make_game.seed_work(str(main / "w"))
        assert (main / "w" / "hot_funcs.txt").read_text() == "80001000 12\n"
        assert (store / "seed" / "hot_funcs.txt").read_text() == "80001000 12\n"
        assert (store / "seed" / "image" / "chunks.txt").read_text() == "0x80000000\n"
        # a new worktree: its checkout's work folder has none; the store's come
        with mock.patch.object(make_game, "ROOT", str(fresh)):
            make_game.seed_work(str(fresh / "w"))
        assert (fresh / "w" / "hot_funcs.txt").read_text() == "80001000 12\n"
        assert (fresh / "w" / "image" / "chunks.txt").read_text() == "0x80000000\n"
        # the checkout's newer list: the store follows it
        (main / "work" / "hot_funcs.txt").write_text("80002000 3\n")
        with mock.patch.object(make_game, "ROOT", str(main)):
            make_game.seed_work(str(main / "w"))
        assert (store / "seed" / "hot_funcs.txt").read_text() == "80002000 3\n"
    # no store: as before, the checkout's alone
    other = tmp / "other" / "w"
    (other / "image").mkdir(parents=True)
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(make_game.OBJ_STORE, None)
        with mock.patch.object(make_game, "ROOT", str(fresh)):
            make_game.seed_work(str(other))
    assert not (other / "hot_funcs.txt").exists()


def check_source():
    src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
    assert "build_env = dict(env, LONGSHOT_OBJ_CACHE=object_cache(work))" in src
    runs = [l for l in src.splitlines() if 'run(PY + ["nx2/build.py", "--standalone"' in l]
    assert len(runs) == 2 and all("build_env" in l for l in runs), runs
    assert "LONGSHOT_OBJ" not in src[src.index("def lift_key("):src.index("def write_lift_key(")]
    b = (ROOT / "nx2" / "build.py").read_text(encoding="utf-8")
    assert 'OBJ_CACHE = os.environ.get("LONGSHOT_OBJ_CACHE") or os.path.join(kit.WORK, "objcache")' in b
    # (forget_failed_build and prune_object_cache clear the work folder's own cache, never the store)
    import ast
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in ("forget_failed_build", "prune_object_cache"):
            body = ast.get_source_segment(src, node)
            assert not re.search(r"(?<!\w)object_cache\(", body) and "OBJ_STORE" not in body, node.name


def main():
    check_source()
    with tempfile.TemporaryDirectory(prefix="longshot-obj-store-") as d:
        tmp = Path(d)
        check_object_cache(tmp)
        check_put_whole(tmp)
        check_seeds(tmp)
        check_two_work_folders(tmp)
    print("object store: ok")


if __name__ == "__main__":
    main()
