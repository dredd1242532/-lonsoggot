"""scripts/pr_builds.py and its workflows (docs/releases.md, "Pull request builds"; issue #337), with no network: the tag a
pull_request event builds (none for a draft, none for an edit of a head built already), the builds to publish (a non-draft
pull request's head with a run that passed and both builders), what is pruned (never anything but a pull request's build),
the one file taken from an artifact, SHA256SUMS signed with a pull request key and never a release key, and the workflows'
split: the pull request's run has no secret, the publishing run runs main's code alone. Needs the openssl command line.

    python -I tests/test_pr_builds.py
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pr_builds", ROOT / "scripts" / "pr_builds.py")
pr_builds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr_builds)
WORKFLOWS = ROOT / ".forgejo" / "workflows"

HEAD = "1a2b3c4d" + "5" * 32
OTHER = "9f3e21ab" + "6" * 32


def event(action="synchronized", number=222, title="fix(vk): HUD and lighting", head=HEAD, draft=False):
    return {"action": action, "pull_request": {"number": number, "title": title, "draft": draft, "head": {"sha": head}}}


def run(run_id, number=222, head=HEAD, status="success", title="fix(vk): HUD and lighting", draft=False):
    payload = event(number=number, head=head, title=title, draft=draft)
    return {"id": run_id, "status": status, "commit_sha": head, "event_payload": json.dumps(payload)}


def pull(number=222, head=HEAD, title="fix(vk): HUD and lighting", draft=False):
    return {"number": number, "title": title, "draft": draft, "head": {"sha": head}}


BOTH = [{"name": "Longshot-linux-x86_64"}, {"name": "LongshotBuilder-windows"}, {"name": "longshot_vk"}]


class NameTests(unittest.TestCase):
    def test_a_push_builds_its_head(self):
        # (Forgejo's payload says "synchronized" for a push; GitHub's, and the workflow's types, "synchronize")
        for action in ("opened", "synchronized", "synchronize", "reopened"):
            self.assertEqual(pr_builds.name(event(action)), "pr-222-1a2b3c4d")

    def test_a_draft_builds_nothing(self):
        for title in ("WIP: fix(vk): HUD", "[WIP] fix(vk): HUD", "wip: fix(vk): HUD"):
            self.assertIsNone(pr_builds.name(event(title=title)), title)
        self.assertIsNone(pr_builds.name(event(draft=True)))

    def test_other_actions_build_nothing(self):
        for action in ("closed", "labeled", "assigned", None):
            self.assertIsNone(pr_builds.name(event(action)))

    def test_an_edit_builds_a_head_once(self):
        # (taking WIP: off: the head's only run was a draft's, which built nothing)
        self.assertEqual(pr_builds.name(event("edited"), runs=[run(5, title="WIP: fix(vk)")], released=False), "pr-222-1a2b3c4d")
        self.assertEqual(pr_builds.name(event("edited"), runs=[], released=False), "pr-222-1a2b3c4d")
        # (the description edited afterwards: its build passed, runs, or is published)
        for status in ("success", "running", "waiting", "blocked"):
            self.assertIsNone(pr_builds.name(event("edited"), runs=[run(5, status=status)], released=False,
                                             artifacts=lambda rid: BOTH), status)
        self.assertIsNone(pr_builds.name(event("edited"), runs=[], released=True))
        # (a build that failed or was cancelled is built again on an edit; another pull request's run of the same commit is
        # not this one's)
        self.assertEqual(pr_builds.name(event("edited"), runs=[run(5, status="failure"), run(6, status="cancelled")], released=False),
                         "pr-222-1a2b3c4d")
        self.assertEqual(pr_builds.name(event("edited"), runs=[run(5, number=223)], released=False), "pr-222-1a2b3c4d")

    def test_an_edit_that_built_nothing_is_no_build(self):
        # (push run 5 builds; an edit's run 6 sees it and builds nothing, and passes; 5 fails: the next edit builds the head)
        runs = [run(5, status="failure"), run(6)]
        builders = {5: [], 6: BOTH[2:]}
        self.assertEqual(pr_builds.name(event("edited"), runs=runs, released=False, artifacts=builders.get), "pr-222-1a2b3c4d")

    def test_an_edit_does_not_count_its_own_run(self):
        os.environ["GITHUB_RUN_ID"] = "7"
        try:
            self.assertEqual(pr_builds.name(event("edited"), runs=[run(7, status="running")], released=False), "pr-222-1a2b3c4d")
        finally:
            del os.environ["GITHUB_RUN_ID"]

    def test_the_tag(self):
        self.assertEqual(pr_builds.tag_of(5, "ABCDEF0123".lower()), "pr-5-abcdef01")
        for number, head in ((0, HEAD), (-1, HEAD), ("5", HEAD), (5, "xyz"), (5, "1a2b3c4"), (5, None)):
            with self.assertRaises(SystemExit):
                pr_builds.tag_of(number, head)
        self.assertRegex(pr_builds.tag_of(2147483, HEAD), pr_builds.TAG)


class PendingTests(unittest.TestCase):
    def pending(self, pulls, releases=(), runs=None, artifacts=None):
        runs = runs or {HEAD: [run(10)], OTHER: [run(11, number=223, head=OTHER)]}
        releases = [r if isinstance(r, dict) else {"tag_name": r} for r in releases]
        return pr_builds.pending(pulls=pulls, releases=releases,
                                 runs=lambda h: runs.get(h, []), artifacts=artifacts or (lambda rid: BOTH))

    def test_a_built_head_is_published(self):
        self.assertEqual(self.pending([pull(), pull(223, OTHER)]), [(222, "pr-222-1a2b3c4d", 10), (223, "pr-223-9f3e21ab", 11)])

    def test_not_a_draft_nor_one_published(self):
        self.assertEqual(self.pending([pull(title="WIP: fix"), pull(223, OTHER, draft=True)]), [])
        self.assertEqual(self.pending([pull()], releases=["pr-222-1a2b3c4d"]), [])

    def test_a_failed_runs_draft_is_published_again(self):
        # (release.publish reuses the draft: waiting for the prune would leave the head without a build for hours)
        self.assertEqual(self.pending([pull()], releases=[{"tag_name": "pr-222-1a2b3c4d", "draft": True}]),
                         [(222, "pr-222-1a2b3c4d", 10)])

    def test_only_a_run_that_passed_and_built_both(self):
        def only(*runs):
            return self.pending([pull()], runs={HEAD: list(runs)})
        self.assertEqual(only(run(10, status="failure"), run(9, status="running")), [])
        self.assertEqual(only(run(10, title="WIP: fix")), [], "a draft's run built nothing")
        self.assertEqual(only(run(10, number=999)), [], "another pull request's run")
        wrong = run(10)
        wrong["commit_sha"] = OTHER
        self.assertEqual(only(wrong), [], "a run of another commit")
        self.assertEqual(only(run(12), run(10)), [(222, "pr-222-1a2b3c4d", 12)], "the newest run that passed")
        self.assertEqual(self.pending([pull()], artifacts=lambda rid: BOTH[:1]), [], "the Windows builder is missing")
        expired = [dict(a, expired=True) for a in BOTH]
        self.assertEqual(self.pending([pull()], artifacts=lambda rid: expired), [], "expired artifacts")

    def test_a_head_that_is_not_a_commit(self):
        self.assertEqual(self.pending([pull(head="1a2b3c4d")]), [])


class OpenPullsTests(unittest.TestCase):
    def test_every_page_even_short_ones(self):
        # (a server whose pages hold 30 at most: a short page is not the last, or prune deletes the builds of open ones)
        everyone = [pull(n) for n in range(1, 71)]
        asked = []

        def api(path, missing_ok=False):
            page = int(re.search(r"page=(\d+)", path).group(1))
            asked.append(page)
            return everyone[(page - 1) * 30:page * 30]
        real = pr_builds.api
        pr_builds.api = api
        try:
            self.assertEqual([p["number"] for p in pr_builds.open_pulls()], list(range(1, 71)))
        finally:
            pr_builds.api = real
        self.assertEqual(asked, [1, 2, 3, 4])


class PublishTests(unittest.TestCase):
    def test_one_build_failing_lets_the_others_go_on(self):
        # (a bad zip, a timeout, a field missing: that build fails, the next is published, and no folder is left)
        tried = []

        def fetch(number, tag, run_id, folder):
            tried.append(tag)
            Path(folder, "big.AppImage").write_bytes(b"x")
            if number == 222:
                raise zipfile.BadZipFile("truncated")
            raise SystemExit("pr_builds.py: run %d has no Longshot-linux-x86_64" % run_id)
        saved = pr_builds.pending, pr_builds.fetch
        pr_builds.pending = lambda: [(222, "pr-222-1a2b3c4d", 10), (223, "pr-223-9f3e21ab", 11)]
        pr_builds.fetch = fetch
        try:
            with tempfile.TemporaryDirectory() as work:
                with self.assertRaises(SystemExit) as e:
                    pr_builds.publish("key.pem", work)
                self.assertEqual(os.listdir(work), [])
        finally:
            pr_builds.pending, pr_builds.fetch = saved
        self.assertEqual(tried, ["pr-222-1a2b3c4d", "pr-223-9f3e21ab"])
        self.assertIn("pr-222-1a2b3c4d, pr-223-9f3e21ab", str(e.exception))

    def test_the_title_is_text_on_the_release_page(self):
        text = pr_builds.notes(222, HEAD, {"title": "fix: [Download the official build](https://evil.example/x) ``a``"})
        self.assertIn("``` fix: [Download the official build](https://evil.example/x) ``a`` ```", text)
        self.assertEqual(pr_builds.plain("WIP:  two\nlines"), "` WIP: two lines `")


class CountsTests(unittest.TestCase):
    def test_who_has_looked(self):
        # (the author, a bot and a deleted account are no one; a login in another case is the same person; a review counts)
        pr = {"number": 222, "title": "fix(vk): HUD", "user": {"login": "dukenheimer"}}
        comments = [{"user": {"login": "DukeNheimer"}}, {"user": {"login": "crash-bot"}}, {"user": {"login": "renovate[bot]"}},
                    {"user": {"login": "ghost"}}, {"user": {"login": "Amateurwizrd"}}, {"user": {"login": "amateurwizrd"}}, {"user": None}]
        reviews = [{"user": {"login": "mooki"}}, {"user": {"login": "dukenheimer"}}]
        self.assertEqual(pr_builds.commenters(pr, comments, reviews), {"amateurwizrd", "mooki"})

    def test_each_pull_request_out_of_draft(self):
        pulls = [pull(222), pull(223, OTHER, title="WIP: later"), pull(224, OTHER, draft=True), pull(16, OTHER)]
        for p in pulls:
            p["user"] = {"login": "author"}
        looked = {222: [{"user": {"login": "a"}}, {"user": {"login": "b"}}], 16: []}
        got = pr_builds.counts(pulls, comments=lambda n: looked.get(n, []), reviews=lambda n: [], now=1_800_000_000)
        self.assertEqual(got, {"generated": "2027-01-15T08:00:00Z", "pulls": {"222": {"commenters": 2}, "16": {"commenters": 0}}})


class PruneTests(unittest.TestCase):
    NOW = 1_800_000_000

    def rel(self, tag, published="2026-11-13T10:00:00Z", draft=False, created="2026-11-13T10:00:00Z"):
        return {"tag_name": tag, "published_at": published, "draft": draft, "created_at": created}

    def gone(self, releases, pulls, keep=2):
        return [r["tag_name"] for r in pr_builds.to_prune(releases, pulls, keep, now=self.NOW)]

    def test_closed_merged_or_draft_again(self):
        rels = [self.rel("pr-222-aaaaaaaa"), self.rel("pr-223-bbbbbbbb"), self.rel("pr-224-cccccccc")]
        self.assertEqual(self.gone(rels, [pull(222), pull(224, title="WIP: back")]), ["pr-223-bbbbbbbb", "pr-224-cccccccc"])

    def test_each_ones_newest_kept(self):
        rels = [self.rel("pr-222-0000000%d" % i, published="2026-11-1%dT10:00:00Z" % i) for i in range(1, 5)]
        self.assertEqual(sorted(self.gone(rels, [pull(222)])), ["pr-222-00000001", "pr-222-00000002"])
        self.assertEqual(sorted(self.gone(rels, [pull(222)], keep=3)), ["pr-222-00000001"])

    def test_a_failed_runs_draft(self):
        old = self.rel("pr-222-dddddddd", draft=True, published=None, created="2020-01-01T00:00:00Z")
        new = self.rel("pr-222-eeeeeeee", draft=True, published=None,
                       created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.NOW - 60)))
        self.assertEqual(self.gone([old, new], [pull(222)]), ["pr-222-dddddddd"])

    def test_a_drafts_age_with_the_servers_offset(self):
        # (Forgejo's times carry its own offset: a draft made a minute ago on a server at UTC-08:00 is not 8 hours old)
        made = time.strftime("%Y-%m-%dT%H:%M:%S-08:00", time.gmtime(self.NOW - 60 - 8 * 3600))
        new = self.rel("pr-222-eeeeeeee", draft=True, published=None, created=made)
        self.assertEqual(self.gone([new], [pull(222)]), [])
        self.assertAlmostEqual(pr_builds._when(made), self.NOW - 60)

    def test_never_the_counts(self):
        self.assertEqual(self.gone([self.rel(pr_builds.COUNTS_TAG)], []), [])

    def test_never_anything_else(self):
        rels = [self.rel(t) for t in ("v3.0.0", "v3.1.0-rc.1", "nightly-12-bbbbbbbb", "pr-1a2b3c4d", "pr-222-1A2B3C4D", "tools")]
        self.assertEqual(self.gone(rels, []), [])


class ArtifactTests(unittest.TestCase):
    def test_the_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = os.path.join(tmp, "a.zip")
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("Longshot-pr-222-1a2b3c4d-x86_64.AppImage", b"\x7fELF")
                f.writestr("other.txt", b"not taken")
            out = os.path.join(tmp, "out")
            os.mkdir(out)
            got = pr_builds.take(z, "Longshot-pr-222-1a2b3c4d-x86_64.AppImage", out)
            self.assertEqual(Path(got).read_bytes(), b"\x7fELF")
            self.assertEqual(os.listdir(out), ["Longshot-pr-222-1a2b3c4d-x86_64.AppImage"])

    def test_a_missing_or_nested_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = os.path.join(tmp, "a.zip")
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("../Longshot-pr-222-1a2b3c4d-x86_64.AppImage", b"x")
                f.writestr("sub/Longshot-pr-222-1a2b3c4d-x86_64.AppImage", b"x")
            with self.assertRaises(SystemExit):
                pr_builds.take(z, "Longshot-pr-222-1a2b3c4d-x86_64.AppImage", tmp)
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(tmp), "Longshot-pr-222-1a2b3c4d-x86_64.AppImage")))


class KeyTests(unittest.TestCase):
    def key(self, folder, name):
        pem = os.path.join(folder, name)
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", pem], check=True, capture_output=True)
        return pem, pr_builds.release.spki_der(pem)

    def test_signed_with_a_pull_request_key_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            pr_pem, pr_spki = self.key(tmp, "pr.pem")
            rel_pem, rel_spki = self.key(tmp, "release.pem")
            files = os.path.join(tmp, "files")
            os.mkdir(files)
            for n in ("Longshot-pr-222-1a2b3c4d-x86_64.AppImage", "tools.lock"):
                Path(files, n).write_bytes(n.encode())
            sums = os.path.join(files, "SHA256SUMS")
            pr_builds.sums([os.path.join(files, n) for n in sorted(os.listdir(files))], sums)
            self.assertRegex(Path(sums).read_text(), r"^[0-9a-f]{64}  Longshot-pr-222-1a2b3c4d-x86_64\.AppImage\n[0-9a-f]{64}  tools\.lock\n$")
            Path(sums + ".sig").write_text(pr_builds.release.sign(sums, pr_pem))
            pr_builds.verify(sums, files, keys=[pr_spki])
            with self.assertRaises(SystemExit):
                pr_builds.verify(sums, files, keys=[rel_spki])
            with self.assertRaises(SystemExit):
                pr_builds.verify(sums, files, keys=[])
            Path(files, "tools.lock").write_bytes(b"changed")
            with self.assertRaises(SystemExit):
                pr_builds.verify(sums, files, keys=[pr_spki])

    def test_the_lists_differ(self):
        data = json.loads((ROOT / "scripts" / "release_keys.json").read_text(encoding="utf-8"))
        self.assertIn("pull_request_keys", data)
        self.assertFalse(set(data["pull_request_keys"]) & set(data["keys"]))
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "scripts"))
            Path(tmp, "scripts", "release_keys.json").write_text(json.dumps({"keys": ["REVG"], "pull_request_keys": ["REVG"]}))
            with self.assertRaises(SystemExit):
                pr_builds.pull_request_keys(tmp)


class WorkflowTests(unittest.TestCase):
    def test_the_build_is_the_pull_requests_own_run(self):
        wf = (WORKFLOWS / "pr-build.yml").read_text(encoding="utf-8")
        self.assertRegex(wf, r"(?m)^on:\n  pull_request:\n")
        self.assertNotIn("pull_request_target", wf)
        self.assertNotIn("secrets.", wf)
        self.assertNotIn("forgejo.token", wf)
        self.assertIn("scripts/pr_builds.py name", wf)
        self.assertIn("uses: ./.forgejo/workflows/build-linux.yml", wf)
        self.assertIn("uses: ./.forgejo/workflows/build-windows.yml", wf)

    def test_publishing_runs_mains_code_alone(self):
        wf = (WORKFLOWS / "pr-publish.yml").read_text(encoding="utf-8")
        self.assertNotRegex(wf, r"pull_request")
        self.assertNotRegex(wf, r"(?m)^\s+ref:")   # (the checkout is main's)
        self.assertIn("secrets.PR_SIGNING_KEY", wf)
        self.assertNotIn("RELEASE_SIGNING_KEY", wf)
        self.assertIn("scripts/pr_builds.py publish", wf)
        self.assertIn("scripts/pr_builds.py prune", wf)
        self.assertIn("scripts/pr_builds.py counts", wf)

    def test_a_pull_requests_build_is_no_release_build(self):
        # (build-linux.yml and build-windows.yml name the files by the tag; only a channel's version builds without the caches
        # other runs write, and writes the release-only download cache)
        for wf in ("build-linux.yml", "build-windows.yml"):
            text = (WORKFLOWS / wf).read_text(encoding="utf-8")
            self.assertIn("RELEASE_BUILD: ${{ inputs.tag != '' && !startsWith(inputs.tag, 'pr-') && '1' || '' }}", text, wf)
            self.assertNotRegex(text, r"if: .*env\.RELEASE_TAG", wf)

    def test_a_push_cancels_the_build_before_it(self):
        wf = (WORKFLOWS / "pr-build.yml").read_text(encoding="utf-8")
        self.assertIn("github.event.action == 'synchronized'", wf)

    def test_the_artifacts_are_the_builders(self):
        # (pr_builds.ARTIFACTS names what build-linux.yml and build-windows.yml upload)
        for name, wf in (("Longshot-linux-x86_64", "build-linux.yml"), ("LongshotBuilder-windows", "build-windows.yml")):
            self.assertIn(name, pr_builds.ARTIFACTS)
            self.assertRegex((WORKFLOWS / wf).read_text(encoding="utf-8"), r"name: %s\n" % re.escape(name))


if __name__ == "__main__":
    unittest.main()
