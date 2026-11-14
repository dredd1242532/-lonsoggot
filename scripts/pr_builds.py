"""Pull requests' builds (docs/releases.md, "Pull request builds"; issue #337): a pull request that is not a draft is built by
.forgejo/workflows/pr-build.yml, in its own run (a fork's: a read-only token and no secret), and published by
.forgejo/workflows/pr-publish.yml, which runs main's code only: it takes that run's two builders, signs them with the pull
request key and publishes them as a pre-release pr-<number>-<first 8 hex of the head> that the Longshot Launcher offers to
testers (Build another version > A pull request), never on a channel.

    python -I scripts/pr_builds.py name <event.json>             the tag a pull_request event's build gets; nothing when it
                                                                 builds nothing (a draft; an edit of a head built already)
    python -I scripts/pr_builds.py pending                       the builds to publish: <number> <tag> <run id>, one a line
    python -I scripts/pr_builds.py publish --key <pem> --work <dir> [--dry-run]
                                                                 each pending build: its files, SHA256SUMS signed, published
    python -I scripts/pr_builds.py prune [--keep 2] [--dry-run]  the builds of pull requests closed, merged or back to draft,
                                                                 and each one's past its newest keep
    python -I scripts/pr_builds.py verify <SHA256SUMS> --files <dir>    checked with the pull request keys

The pull request keys are scripts/release_keys.json's "pull_request_keys" (the launcher's ReleaseKeys.PullRequestBuiltIn):
never the release key, which signs the channels' versions alone. RELEASE_TOKEN: the job's token (forgejo.token);
FORGEJO_UPLOAD_API as release.py takes it.
"""
import argparse
import base64
import datetime
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _module(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


release = _module("release")
desc = _module("check_pr_description")

TAG = re.compile(r"^pr-([1-9][0-9]{0,8})-([0-9a-f]{8})$")
WORKFLOW = "pr-build.yml"
# (the builders' artifacts, as build-linux.yml and build-windows.yml upload them, and the file each holds)
ARTIFACTS = {
    "Longshot-linux-x86_64": "Longshot-{tag}-x86_64.AppImage",
    "LongshotBuilder-windows": "LongshotBuilder-{tag}-windows-x64.zip",
}
# (a run that is building, or will: an edit of its head builds nothing more. A run that passed counts only with its builders'
# artifacts: an edit's run that built nothing passes too)
BUILDING = ("running", "waiting", "blocked")
MAX_FILE = 4 << 30
DRAFT_AGE = 6 * 3600   # (a pull request build's draft older than this was left by a failed run)


def fail(message):
    raise SystemExit("pr_builds.py: " + message)


def tag_of(number, head):
    if not isinstance(number, int) or number <= 0 or not re.match(r"^[0-9a-f]{8,}$", head or ""):
        fail("not a pull request's number and head: %r %r" % (number, head))
    return "pr-%d-%s" % (number, head[:8])


def is_draft(pr):
    """Work in progress: Forgejo's draft mark (a WIP: or [WIP] title), as the PR description check reads it"""
    return desc.is_draft(pr.get("title"), pr.get("draft"))


def repo():
    return release.repo_of("longshot")


def api(path, missing_ok=False):
    return release.api("GET", path, missing_ok=missing_ok)


def runs_of(head):
    """pr-build.yml's runs of the commit head, newest first"""
    got = api("/repos/%s/actions/runs?workflow_id=%s&head_sha=%s&limit=50" % (repo(), WORKFLOW, head)) or {}
    return sorted(got.get("workflow_runs") or [], key=lambda r: r["id"], reverse=True)


def payload_of(run):
    try:
        return json.loads(run.get("event_payload") or "{}")
    except ValueError:
        return {}


def has_builders(run_id, artifacts=None):
    have = {a["name"] for a in (artifacts or run_artifacts)(run_id) if not a.get("expired")}
    return set(ARTIFACTS) <= have


def built_run(run, number):
    """A run of pull request number that built (it was not a draft then): its payload's pull request"""
    pr = payload_of(run).get("pull_request") or {}
    return pr.get("number") == number and not is_draft(pr)


def name(event, runs=None, released=None, artifacts=None):
    """The tag a pull_request event's run builds (pr-<number>-<8 hex>), or None: a draft builds nothing, and an edit (of the
    title or the description: Forgejo's payload does not say which) builds only a head that has no build yet, so taking WIP:
    off builds it once and editing the description afterwards builds nothing. runs, released: the head's runs and whether its
    release is out, artifacts(run id) (the tests pass theirs; else asked of the server)"""
    pr = event.get("pull_request") or {}
    if is_draft(pr) or event.get("action") not in ("opened", "synchronize", "reopened", "edited"):
        return None
    tag = tag_of(pr.get("number"), (pr.get("head") or {}).get("sha"))
    if event.get("action") != "edited":
        return tag
    if released is None:
        released = bool(api("/repos/%s/releases/tags/%s" % (repo(), tag), missing_ok=True))
    if released:
        return None
    this = int(os.environ.get("GITHUB_RUN_ID") or 0)
    runs = runs_of(pr["head"]["sha"]) if runs is None else runs
    for r in runs:
        if r["id"] == this or not built_run(r, pr["number"]):
            continue
        if r.get("status") in BUILDING or (r.get("status") == "success" and has_builders(r["id"], artifacts)):
            return None
    return tag


def open_pulls(pages=1000):
    """Every open pull request, page by page until an empty one (the server may cap a page below the limit asked: a short page
    is not the last, and prune takes a pull request missing here for closed)"""
    out, seen = [], set()
    for page in range(1, pages + 1):
        new = [p for p in api("/repos/%s/pulls?state=open&limit=50&page=%d" % (repo(), page)) or [] if p["number"] not in seen]
        if not new:
            return out
        seen.update(p["number"] for p in new)
        out += new
    fail("more than %d pages of open pull requests" % pages)


def pending(pulls=None, releases=None, runs=None, artifacts=None):
    """The builds to publish, [(number, tag, run id)]: each open pull request that is not a draft, whose head has no published
    release yet (a draft a failed run left is published again: release.publish reuses it) and a successful pr-build.yml run
    that built it, with both builders' artifacts there (not expired). The arguments are
    the server's answers (the tests pass theirs): pulls, releases (tags), runs(head), artifacts(run id)"""
    pulls = open_pulls() if pulls is None else pulls
    tags = {r["tag_name"] for r in (release.releases(repo()) if releases is None else releases) if not r.get("draft")}
    runs = runs or runs_of
    artifacts = artifacts or run_artifacts
    out = []
    for pr in pulls:
        head = (pr.get("head") or {}).get("sha") or ""
        if is_draft(pr) or not re.match(r"^[0-9a-f]{40}$", head):
            continue
        tag = tag_of(pr["number"], head)
        if tag in tags:
            continue
        done = [r for r in runs(head) if r.get("status") == "success" and r.get("commit_sha") == head and built_run(r, pr["number"])]
        for run in done:
            if has_builders(run["id"], artifacts):
                out.append((pr["number"], tag, run["id"]))
                break
    return out


def run_artifacts(run_id):
    return (api("/repos/%s/actions/runs/%d/artifacts" % (repo(), run_id)) or {}).get("artifacts") or []


def take(zip_path, want, folder):
    """The one file want out of an artifact's zip into folder: nothing else is taken, and a name with a folder in it is
    refused (an artifact is the pull request's own code's output)"""
    with zipfile.ZipFile(zip_path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        if want not in names:
            fail("%s holds %s, not %s" % (os.path.basename(zip_path), names, want))
        info = z.getinfo(want)
        if info.file_size > MAX_FILE:
            fail("%s: larger than %d bytes" % (want, MAX_FILE))
        with z.open(info) as src, open(os.path.join(folder, want), "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
    return os.path.join(folder, want)


def fetch(number, tag, run_id, folder):
    """A pending build's files into folder: both builders from its run's artifacts, and tools.lock from its head (what its
    Windows builder installs, and the launcher on Linux); the pull request and the paths"""
    pr = api("/repos/%s/pulls/%d" % (repo(), number))
    head = pr["head"]["sha"]
    if tag_of(number, head) != tag:
        fail("pull request %d moved on to %s: %s is not its head any more" % (number, head[:8], tag))
    files = []
    found = {a["name"]: a for a in run_artifacts(run_id) if not a.get("expired")}
    for artifact, file in ARTIFACTS.items():
        if artifact not in found:
            fail("run %d has no %s" % (run_id, artifact))
        zip_path = os.path.join(folder, artifact + ".zip")
        release.download("%s/api/v1/repos/%s/actions/artifacts/%d/zip" % (release.SERVER, repo(), found[artifact]["id"]), MAX_FILE,
                         to=zip_path)
        files.append(take(zip_path, file.format(tag=tag), folder))
        os.remove(zip_path)
    lock = os.path.join(folder, "tools.lock")
    release.download("%s/api/v1/repos/%s/raw/tools.lock?ref=%s" % (release.SERVER, repo(), head), to=lock)
    files.append(lock)
    return pr, files


def pull_request_keys(root=ROOT):
    """The keys a pull request's build may be signed with: scripts/release_keys.json's "pull_request_keys" (the launcher lists
    the same in ReleaseKeys.PullRequestBuiltIn), never one of its release keys"""
    with open(os.path.join(root, "scripts", "release_keys.json"), encoding="utf-8") as f:
        data = json.load(f)
    keys = [base64.b64decode(k) for k in data.get("pull_request_keys", [])]
    if set(keys) & {base64.b64decode(k) for k in data.get("keys", [])}:
        fail("scripts/release_keys.json lists a release key as a pull request key: they must differ")
    return keys


def sums(files, out):
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for path in sorted(files, key=os.path.basename):
            f.write("%s  %s\n" % (release.sha256_file(path), os.path.basename(path)))


def verify(sums_path, folder, keys=None):
    """SHA256SUMS signed by a pull request key, and naming each file in folder by its SHA-256"""
    keys = pull_request_keys() if keys is None else keys
    if not keys:
        fail("scripts/release_keys.json lists no pull request key (RELEASING.md, The pull request key)")
    with open(sums_path, "rb") as f:
        data = f.read()
    with open(sums_path + ".sig", encoding="ascii") as f:
        der = base64.b64decode(f.read().strip())
    if release.verify_sig(data, der, keys) is None:
        fail("SHA256SUMS is not signed by a pull request key the launcher trusts")
    for name, want in release.read_sums(data.decode("utf-8")).items():
        if release.sha256_file(os.path.join(folder, name)) != want:
            fail("%s is not the file SHA256SUMS names" % name)


def plain(text):
    """text as a Markdown code span, shown as written: a pull request's title is its author's, and no link (a bare address is
    one too), image or emphasis of theirs goes on a release page of Longshot's"""
    text = " ".join(text.split())
    fence = "`" * (max((len(r) for r in re.findall(r"`+", text)), default=0) + 1)
    return "%s %s %s" % (fence, text, fence)


def notes(number, head, pr):
    title = plain(desc.strip_wip(pr.get("title") or ""))
    return "\n".join([
        "A build of [pull request #%d](%s/%s/pulls/%d), %s, at %s, for testers: the Longshot Launcher installs it from "
        "*Build another version > A pull request*. Nobody has reviewed this code yet." % (number, release.SERVER, repo(), number, title, head),
        "", "It is on no channel, and nothing updates to it: it stays until it is deleted. A newer push of the pull request is "
        "built as a release of its own; this one is deleted with the pull request's older builds, or when it is merged or closed.", ""])


def publish(key, work, dry_run=False):
    """Each pending build published; one that fails is said and the others go on. The tags published"""
    todo = pending()
    if not todo:
        print("nothing to publish")
    done, failed = [], []
    for number, tag, run_id in todo:
        print("%s: pull request %d, run %d" % (tag, number, run_id))
        if dry_run:
            continue
        folder = tempfile.mkdtemp(prefix=tag + "-", dir=work)
        try:
            pr, files = fetch(number, tag, run_id, folder)
            head = pr["head"]["sha"]
            sums_path = os.path.join(folder, "SHA256SUMS")
            sums(files, sums_path)
            with open(sums_path + ".sig", "w", encoding="ascii") as f:
                f.write(release.sign(sums_path, key))
            verify(sums_path, folder)
            notes_path = os.path.join(folder, "notes.md")
            with open(notes_path, "w", encoding="utf-8") as f:
                f.write(notes(number, head, pr))
            release.publish(tag, head, notes_path, files + [sums_path, sums_path + ".sig"], "longshot")
            done.append(tag)
        except (SystemExit, Exception) as e:   # (a bad zip, a timeout, a field missing: this build fails, the others go on)
            print("::error::%s: %s" % (tag, e))
            failed.append(tag)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    if failed:
        fail("not published: " + ", ".join(failed))
    return done


def to_prune(releases, pulls, keep=2, now=None):
    """The pull requests' builds to delete: those of a pull request that is not open or is a draft again, each one's
    published builds past its newest keep, and drafts a failed run left (older than DRAFT_AGE). Never anything else"""
    now = time.time() if now is None else now
    alive = {p["number"] for p in pulls if not is_draft(p)}
    by_pr = {}
    for r in releases:
        m = TAG.match(r.get("tag_name") or "")
        if m:
            by_pr.setdefault(int(m.group(1)), []).append(r)
    gone = []
    for number, rs in by_pr.items():
        if number not in alive:
            gone += rs
            continue
        published = sorted((r for r in rs if not r.get("draft")), key=lambda r: r.get("published_at") or "", reverse=True)
        gone += published[keep:]
        gone += [r for r in rs if r.get("draft") and now - _when(r.get("created_at")) > DRAFT_AGE]
    return gone


def _when(stamp):
    """A time of Forgejo's API (2026-11-13T10:00:00-05:00, or Z): with its offset, which is the server's; one without is UTC"""
    try:
        t = datetime.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return 0
    return (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).timestamp()


def prune(keep, dry_run=False):
    for r in to_prune(release.releases(repo()), open_pulls(), keep):
        print("pruned %s" % r["tag_name"] if not dry_run else "would prune %s" % r["tag_name"])
        if dry_run:
            continue
        release.api("DELETE", "/repos/%s/releases/%d" % (repo(), r["id"]))
        release.api("DELETE", "/repos/%s/tags/%s" % (repo(), urllib.parse.quote(r["tag_name"])), missing_ok=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("name")
    s.add_argument("event")
    sub.add_parser("pending")
    s = sub.add_parser("publish")
    s.add_argument("--key", required=True)
    s.add_argument("--work", required=True)
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("prune")
    s.add_argument("--keep", type=int, default=2)
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("verify")
    s.add_argument("sums")
    s.add_argument("--files", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "name":
        with open(a.event, encoding="utf-8") as f:
            print(name(json.load(f)) or "")
    elif a.cmd == "pending":
        for number, tag, run_id in pending():
            print(number, tag, run_id)
    elif a.cmd == "publish":
        os.makedirs(a.work, exist_ok=True)
        publish(a.key, a.work, a.dry_run)
    elif a.cmd == "prune":
        if a.keep < 1:
            fail("--keep is at least 1")
        prune(a.keep, a.dry_run)
    elif a.cmd == "verify":
        verify(a.sums, a.files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
