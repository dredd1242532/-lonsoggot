"""The hardware test workflows keep a pull request's code off testers' PCs except through the runner owner's sandbox
(docs/hardware-runners.md).

Reads .forgejo/workflows/hardware-test.yml and hardware-test-label.yml as text, and runs the gate's and the report's
shell steps with a stand-in curl that answers as Forgejo's API. No network, no runner, no game.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# (the steps are bash)
RUN_ALL_SKIP = {"windows": "the workflow steps are bash; exercised on Linux"}

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".forgejo" / "workflows"
TEST = WF / "hardware-test.yml"
GATE = WF / "hardware-test-label.yml"
NAME = r"^[a-z0-9][a-z0-9-]{0,31}$"


def triggers(text):
    """The event names under the top-level on:."""
    m = re.search(r"^on:\n((?:[ \t]+.*\n|\n)+)", text, re.M)
    assert m, "no on: block"
    return set(re.findall(r"^  ([a-z_]+):", m.group(1), re.M))


def jobs(text):
    """Job id -> its text."""
    body = text[text.index("\njobs:\n") + 7:]
    parts = re.split(r"^  ([a-z_-]+):\n", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def runs(text):
    """Every run: | script, dedented."""
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(?:- )?run: \|$", line)
        if not m:
            continue
        indent, block = len(m.group(1)), []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            block.append(nxt)
        width = min(len(b) - len(b.lstrip()) for b in block if b.strip())
        out.append("\n".join(b[width:] for b in block) + "\n")
    return out


class Workflows(unittest.TestCase):
    def setUp(self):
        self.test = TEST.read_text(encoding="utf-8")
        self.gate = GATE.read_text(encoding="utf-8")

    def test_only_a_dispatch_starts_the_hardware_test(self):
        self.assertEqual(triggers(self.test), {"workflow_dispatch"})

    def test_the_gate_reads_labels_and_pushes_only(self):
        self.assertEqual(triggers(self.gate), {"pull_request_target"})
        self.assertRegex(self.gate, r"types: \[labeled, synchronize\]")

    def test_no_other_workflow_uses_a_self_hosted_runner(self):
        for wf in sorted(WF.glob("*.yml")):
            if wf != TEST:
                for line in re.findall(r"runs-on:.*", wf.read_text(encoding="utf-8")):
                    self.assertNotIn("self-hosted", line, wf.name)

    def test_the_self_hosted_job_has_no_token_and_no_checkout(self):
        j = jobs(self.test)
        hosted = [k for k, v in j.items() if "self-hosted" in v]
        self.assertEqual(hosted, ["run"])
        run = j["run"]
        self.assertIn("runs-on: [self-hosted, linux, longshot-hardware,", run)
        self.assertRegex(run, r"\n    defaults:\n      run:\n        shell: bash\n")
        self.assertRegex(run, r"\n    permissions: \{\}\n")
        self.assertNotIn("actions/checkout", run)
        self.assertNotIn("github.token", run)
        self.assertNotIn("secrets.", run)
        self.assertIn("needs: check", run)

    def test_the_runner_refuses_without_its_sandbox(self):
        script = next(s for s in runs(self.test) if "LONGSHOT_HW_SANDBOX" in s)
        self.assertLess(script.index("exit 1"), script.index('"$LONGSHOT_HW_SANDBOX" --repo'))

    def test_the_runner_checks_the_approval_again_before_the_sandbox(self):
        run = jobs(self.test)["run"]
        recheck = run.index("- name: The commit is still the labelled head, after the queue")
        self.assertLess(recheck, run.index("- name: Run the commit in the runner's sandbox"))
        self.assertLess(run.index("- name: Run the commit in the runner's sandbox"), run.index("- uses:"))
        self.assertNotIn("continue-on-error", run)

    def test_manual_runs_come_from_the_default_branch(self):
        check = jobs(self.test)["check"]
        self.assertIn("DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}", check)
        script = runs(check)[0]
        self.assertTrue(script.startswith('[ "$GITHUB_REF" = "refs/heads/$DEFAULT_BRANCH" ] ||'), script[:80])

    def test_the_pilot_is_fork_local_and_says_so(self):
        for text in (self.gate, self.test):
            self.assertIn("Fork-local pilot" if text is self.test else "fork-local pilot", text)
        self.assertIn("RUNNERS: ${{ vars.LONGSHOT_HW_RUNNERS }}", self.gate)
        doc = (ROOT / "docs" / "hardware-runners.md").read_text(encoding="utf-8")
        for promise in ("test pull request opened on the fork itself", "Registering runners on MagicBeanDev/Longshot is "
                        "not the fix", "again before taking the GPU", "only regular files",
                        "the runner keeps its own session and artifact credentials", "`LONGSHOT_HW_RUNNERS`",
                        "Runners are Linux"):
            self.assertIn(promise, re.sub(r"\s+", " ", doc))

    def test_the_cleanup_skips_old_pushes_and_newer_labels(self):
        unlabel = jobs(self.gate)["unlabel"]
        self.assertIn("PUSHED_AT: ${{ github.event.pull_request.updated_at }}", unlabel)
        self.assertIn('i.get("type") == "label" and i.get("body") == "1"', unlabel)

    def test_the_gate_never_checks_out_the_pull_request(self):
        self.assertNotIn("actions/checkout", self.gate)
        self.assertNotIn("head.ref", self.gate)
        self.assertNotIn("uses:", self.gate)

    def test_the_gate_dispatches_from_the_default_branch(self):
        self.assertIn("BRANCH: ${{ github.event.repository.default_branch }}", self.gate)
        self.assertIn('"$API/actions/workflows/hardware-test.yml/dispatches"', self.gate)
        self.assertIn('{"ref":"%s","inputs":{"pr":"%s","sha":"%s","hardware":"%s"}}\' "$BRANCH"', self.gate)

    def test_no_expression_inside_a_script(self):
        # (an expression in run: is pasted into the shell before it runs: a label or a branch name could be code)
        for name, text in (("gate", self.gate), ("test", self.test)):
            for script in runs(text):
                self.assertNotIn("${{", script, name)

    def test_actions_are_pinned_by_commit(self):
        for text in (self.gate, self.test):
            for use in re.findall(r"uses: (\S+)", text):
                self.assertRegex(use, r"@[0-9a-f]{40}$")

    def test_least_privilege(self):
        for text in (self.gate, self.test):
            self.assertRegex(text, r"\npermissions:\n  contents: read\n")
            self.assertNotIn("write-all", text)
            for job in jobs(text).values():
                self.assertIn("timeout-minutes:", job)
        self.assertNotIn("write", jobs(self.test)["check"].split("steps:")[0])

    def test_the_name_rule_is_the_same_everywhere(self):
        for text in (self.gate, self.test):
            self.assertIn(NAME.strip("^$"), text)


def pr_json(state="open", head="f" * 40, labels=("bug",), merged=False):
    """A pull request as Forgejo's API answers it (labels with their ids)."""
    return json.dumps({"state": state, "merged": merged, "head": {"sha": head},
                       "labels": [{"id": 10 + i, "name": n} for i, n in enumerate(labels)]})


def timeline(*events):
    """Timeline entries: (time, label name, put on?)."""
    return json.dumps([{"type": "label", "created_at": t, "body": "1" if on else "", "label": {"name": n}}
                       for t, n, on in events] + [{"type": "comment", "created_at": "2026-11-12T09:00:00Z", "body": "hi"}])


@unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), "needs bash and python3")
class Steps(unittest.TestCase):
    """The scripts themselves, with a curl that records its arguments and answers as Forgejo's API."""

    @staticmethod
    def stub(d, name, answers):
        """A stand-in command that logs its arguments and prints the answer of the first glob its arguments match
        (None: it fails)."""
        if isinstance(answers, str):
            answers = [("*", answers)] if answers else []
        cases = []
        for i, (glob, out) in enumerate(answers):
            if out is None:
                cases.append(f"  {glob}) exit 22 ;;\n")
            else:
                (Path(d) / f"{name}.{i}").write_text(out + "\n", encoding="utf-8")
                cases.append(f"  {glob}) cat '{d}/{name}.{i}' ;;\n")
        path = Path(d) / name
        path.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CURL_LOG\"\ncase \"$*\" in\n" + "".join(cases)
                        + "esac\n", encoding="utf-8")
        path.chmod(0o755)

    def run_step(self, script, env, curl=""):
        with tempfile.TemporaryDirectory() as d:
            self.stub(d, "curl", curl)
            log = Path(d) / "log"
            log.touch()
            full = {"PATH": f"{d}:{os.environ['PATH']}", "CURL_LOG": str(log), "HOME": d, "FORGEJO_TOKEN": "t",
                    "API": "https://forge/api/v1/repos/o/r", **env}
            r = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=d, env=full,
                               capture_output=True, text=True)
            comment = (Path(d) / "comment.md").read_text(encoding="utf-8") if (Path(d) / "comment.md").exists() else ""
            return r, log.read_text(encoding="utf-8"), comment, d

    def gate_dispatch(self):
        return next(s for s in runs(GATE.read_text(encoding="utf-8")) if "/dispatches" in s)

    def test_a_good_label_dispatches_that_commit(self):
        sha = "a" * 40
        r, log, _, _ = self.run_step(self.gate_dispatch(), {"LABEL": "hardware: steam-deck", "PR": "2368",
                                                            "SHA": sha, "BRANCH": "main",
                                                            "RUNNERS": "nvidia-8gb steam-deck"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(log.strip().splitlines()), 1, log)
        self.assertIn("-X POST", log)
        self.assertIn('{"ref":"main","inputs":{"pr":"2368","sha":"%s","hardware":"steam-deck"}}' % sha, log)
        self.assertIn("https://forge/api/v1/repos/o/r/actions/workflows/hardware-test.yml/dispatches", log)

    def test_a_bad_label_dispatches_nothing(self):
        for label in ("hardware: Steam Deck", "hardware: x$(id)", "hardware: a;b", "hardware: ", "hardware: -x",
                      "hardware: " + "a" * 33, "hardware: ../x", 'hardware: a","ref":"x'):
            r, log, _, _ = self.run_step(self.gate_dispatch(), {"LABEL": label, "PR": "1", "SHA": "b" * 40,
                                                                "BRANCH": "main", "RUNNERS": "steam-deck"})
            self.assertNotEqual(r.returncode, 0, label)
            self.assertEqual(log, "", label)

    def test_a_bad_commit_dispatches_nothing(self):
        for sha in ("", "b" * 39, "B" * 40, "b" * 40 + '"'):
            r, log, _, _ = self.run_step(self.gate_dispatch(), {"LABEL": "hardware: steam-deck", "PR": "1",
                                                                "SHA": sha, "BRANCH": "main", "RUNNERS": "steam-deck"})
            self.assertNotEqual(r.returncode, 0, sha)
            self.assertEqual(log, "", sha)

    def test_a_repository_without_that_runner_only_leaves_a_notice(self):
        # (MagicBeanDev/Longshot has no runners: the pilot is on a tester's fork)
        for runners in ("", "steam-deck-oled", "nvidia-8gb steam"):
            r, log, _, _ = self.run_step(self.gate_dispatch(), {"LABEL": "hardware: steam-deck", "PR": "1",
                                                                "SHA": "b" * 40, "BRANCH": "main",
                                                                "RUNNERS": runners,
                                                                "GITHUB_REPOSITORY": "MagicBeanDev/Longshot"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(log, "", runners)
            self.assertIn("::notice::MagicBeanDev/Longshot has no \"steam-deck\" runner", r.stdout)
            self.assertIn("fork", r.stdout)

    def gate_unlabel(self):
        return next(s for s in runs(GATE.read_text(encoding="utf-8")) if "/labels/" in s)

    def unlabel(self, pr, events):
        return self.run_step(self.gate_unlabel(), {"PR": "5", "SHA": "f" * 40, "PUSHED_AT": "2026-11-12T12:00:00Z"},
                             [("*-X\\ DELETE*", ""), ("*-X\\ POST*", ""), ("*/pulls/5", pr),
                              ("*/issues/5/timeline*", events), ("*", None)])

    def test_new_commits_take_the_labels_put_on_before_them_off(self):
        pr = pr_json(labels=("bug", "hardware: steam-deck", "hardware: amd-rdna4"))
        events = timeline(("2026-11-12T10:00:00Z", "hardware: amd-rdna4", True),
                          ("2026-11-12T11:00:00Z", "hardware: steam-deck", True),
                          ("2026-11-12T13:00:00Z", "hardware: amd-rdna4", True),
                          ("2026-11-12T13:10:00Z", "hardware: steam-deck", False),
                          ("2026-11-12T13:30:00Z", "hardware: steam-deck-oled", True))
        r, log, _, _ = self.unlabel(pr, events)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("/issues/5/timeline?limit=50&page=1", log)
        self.assertIn("-X DELETE -H Authorization: token t https://forge/api/v1/repos/o/r/issues/5/labels/11", log)
        # (put on again after the push: a maintainer looked at the new head while this job waited)
        self.assertNotIn("/labels/12", log)
        self.assertIn("/issues/5/comments", log)

    def test_a_late_cleanup_for_an_older_push_does_nothing(self):
        r, log, _, _ = self.unlabel(pr_json(head="0" * 40, labels=("hardware: steam-deck",)),
                                    timeline(("2026-11-12T11:00:00Z", "hardware: steam-deck", True)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(log.strip().splitlines()), 1, log)
        self.assertIn("/pulls/5", log)
        self.assertIn("the newer push's own run", r.stdout)

    def test_labels_put_on_after_the_push_stay_without_a_comment(self):
        r, log, _, _ = self.unlabel(pr_json(labels=("hardware: steam-deck",)),
                                    timeline(("2026-11-12T12:00:01Z", "hardware: steam-deck", True)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("DELETE", log)
        self.assertNotIn("/comments", log)

    def test_unreadable_label_events_take_every_label_off(self):
        for events in (None, "<html>bad gateway</html>"):
            r, log, _, _ = self.unlabel(pr_json(labels=("hardware: steam-deck", "hardware: amd-rdna4")), events)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("/issues/5/labels/10", log)
            self.assertIn("/issues/5/labels/11", log)
            self.assertIn("/issues/5/comments", log)

    def test_no_hardware_labels_no_comment(self):
        r, log, _, _ = self.unlabel(pr_json(), "[]")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(log.strip().splitlines()), 1)

    def runner_recheck(self):
        return next(s for s in runs(TEST.read_text(encoding="utf-8")) if "check it again" in s)

    def test_the_runner_checks_the_approval_again_after_the_queue(self):
        sha = "1" * 40
        env = {"PR": "8", "SHA": sha, "HARDWARE": "steam-deck", "GITHUB_SERVER_URL": "https://git.magicbean.dev",
               "GITHUB_REPOSITORY": "tester/Longshot"}

        def pr(state="open", head=sha, labels=("bug", "hardware: steam-deck"), merged=False):
            return pr_json(state, head, labels, merged)
        for curl_out, ok in ((pr(), True), (pr(state="closed"), False), (pr(state="closed", merged=True), False),
                             (pr(head="2" * 40), False), (pr(labels=("bug",)), False),
                             (pr(labels=("hardware: steam-deck-oled",)), False),
                             ("", False), ("<html>rate limited</html>", False), ("{}", False)):
            r, log, _, _ = self.run_step(self.runner_recheck(), env, [("*", curl_out or None)])
            self.assertEqual(r.returncode == 0, ok, curl_out + r.stdout + r.stderr)
        self.assertIn("https://git.magicbean.dev/api/v1/repos/tester/Longshot/pulls/8", log)
        self.assertNotIn("Authorization", log)   # (no token on the runner)

    def check_script(self):
        return runs(jobs(TEST.read_text(encoding="utf-8"))["check"])[0]

    def check_env(self, **over):
        return {"PR": "7", "SHA": "c" * 40, "HARDWARE": "nvidia-8gb", "GITHUB_REF": "refs/heads/main",
                "DEFAULT_BRANCH": "main", **over}

    def test_a_manual_run_from_another_branch_stops(self):
        for ref in ("refs/heads/feature", "refs/tags/main", "refs/heads/main2"):
            r, log, _, _ = self.run_step(self.check_script(), {**self.check_env(GITHUB_REF=ref),
                                                               "GITHUB_OUTPUT": os.devnull},
                                         pr_json(head="c" * 40, labels=("hardware: nvidia-8gb",)))
            self.assertNotEqual(r.returncode, 0, ref)
            self.assertEqual(log, "", ref)

    def test_the_check_wants_the_labelled_head(self):
        sha = "c" * 40
        env = self.check_env()
        for answer, ok in ((pr_json(head=sha, labels=("hardware: nvidia-8gb",)), True),
                           (pr_json(head="d" * 40, labels=("hardware: nvidia-8gb",)), False),
                           (pr_json(head=sha, labels=("hardware: nvidia-8gb-x", "bug")), False),
                           (pr_json(state="closed", merged=True, head=sha, labels=("hardware: nvidia-8gb",)), False)):
            r, _, _, d = self.run_step(self.check_script() + 'cat "$GITHUB_OUTPUT"\n',
                                       {**env, "GITHUB_OUTPUT": os.devnull if not ok else "out"}, answer)
            self.assertEqual(r.returncode == 0, ok, answer + r.stdout + r.stderr)
        for bad in ({"HARDWARE": "a b"}, {"SHA": "short"}, {"PR": "7/../1"}):
            r, log, _, _ = self.run_step(self.check_script(), {**env, **bad, "GITHUB_OUTPUT": os.devnull},
                                         pr_json(head=sha, labels=("hardware: nvidia-8gb",)))
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertEqual(log, "", bad)

    def test_only_hardware_labels_count(self):
        # (the label "bug" is not the hardware "bug")
        r, _, _, _ = self.run_step(self.check_script(), {**self.check_env(HARDWARE="bug"), "GITHUB_OUTPUT": os.devnull},
                                   pr_json(head="c" * 40, labels=("bug",)))
        self.assertNotEqual(r.returncode, 0)

    def test_the_report_quotes_the_runner_text(self):
        script = next(s for s in runs(TEST.read_text(encoding="utf-8")) if "comment.md" in s)
        evil = "fine\n````\n# injected\n@hckrman101 look\n<img src=x>\n"
        r, log, comment, _ = self.run_step(
            "mkdir -p results && printf '%s' \"$EVIL\" > results/results.txt\n" + script,
            {"EVIL": evil, "PR": "9", "SHA": "e" * 40, "HARDWARE": "amd-rdna4", "OUTCOME": "success",
             "GITHUB_SERVER_URL": "https://git.magicbean.dev", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("-X POST", log)
        self.assertIn("--data-binary @comment.json https://forge/api/v1/repos/o/r/issues/9/comments", log)
        inner = comment.split("````text\n", 1)[1].rsplit("\n````", 1)[0]
        self.assertNotIn("````", inner)
        self.assertNotIn("@hckrman101", comment)
        self.assertIn("@\u200bhckrman101", inner)
        self.assertEqual(comment.count("````"), 2)
        self.assertTrue(inner.endswith("<img src=x>"), inner[-40:])   # (no blank line added before the fence)
        self.assertNotIn("cut at", comment)

    def report(self, text):
        script = next(s for s in runs(TEST.read_text(encoding="utf-8")) if "comment.md" in s)
        r, _, comment, d = self.run_step(
            "mkdir -p results && printf '%s' \"$TEXT\" > results/results.txt\n" + script + "cp comment.json sent.json\n",
            {"TEXT": text, "PR": "9", "SHA": "e" * 40, "HARDWARE": "amd-rdna4", "OUTCOME": "success",
             "GITHUB_SERVER_URL": "https://git.magicbean.dev", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return comment   # (read as strict UTF-8: a character cut in two would fail here)
    def test_the_comment_is_capped_after_escaping(self):
        # (each @ becomes 4 bytes: capping the input alone let 60,000 of them make a 240,000-byte comment)
        for text in ("@" * 60000, "x" + "@" * 60000, "````\n" * 20000):
            comment = self.report(text)
            self.assertLess(len(comment), 65536 - 4000)
            self.assertLess(len(comment.encode("utf-8")), 65536)
            inner = comment.split("````text\n", 1)[1].rsplit("\n````", 1)[0]
            self.assertLessEqual(len(inner.encode("utf-8")), 60000)
            self.assertNotRegex(inner, r"@\w")
            self.assertEqual(comment.count("````"), 2)
            self.assertIn("(cut at 60,000 bytes", comment)
            self.assertTrue(comment.rstrip().endswith("</details>"))

    def test_a_file_without_a_final_newline_still_closes_the_fence(self):
        comment = self.report("line one\nlast line, no newline")
        self.assertIn("last line, no newline\n````\n", comment)
        self.assertEqual(comment.count("````"), 2)

    def test_an_empty_file_still_closes_the_fence(self):
        comment = self.report("")
        self.assertIn("````text\n````\n", comment)


if __name__ == "__main__":
    unittest.main(verbosity=1)
