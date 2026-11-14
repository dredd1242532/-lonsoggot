"""Offline commit-pin contract for scripts/test_requests.py.

Recorded unauthenticated Forgejo bodies live in fixtures/forgejo_pin; mutations.json
labels every artificial response family. Room events, permission responses and
transport failures below are synthetic. No sign-in, live room/PR posting or Discord
card rendering is exercised. Run with python -I -B tests/test_test_requests_pin.py.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
spec = importlib.util.spec_from_file_location("test_requests_pin", os.path.join(ROOT, "scripts", "test_requests.py"))
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "forgejo_pin")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


RECORDED = [fixture(name) for name in (
    "commit-abbreviated.json", "commit-full.json", "commit-parent.json", "commit-divergent.json",
    "compare-forward.json", "compare-reverse.json", "compare-identical.json",
    "compare-divergent-forward.json", "compare-divergent-reverse.json", "pull-11.json")]
ROUTES = {item["url"].removeprefix(tr.API).partition("?")[0]: item["body"] for item in RECORDED}
MUTATIONS = fixture("mutations.json")
HEAD = fixture("commit-full.json")["body"]["sha"]
OLD = fixture("commit-parent.json")["body"]["sha"]
DIVERGENT = fixture("commit-divergent.json")["body"]["sha"]
DIVERGENT_BASE = fixture("compare-divergent-forward.json")["body"]["commits"][0]["sha"]
PR = tr.REPO + "/pulls/11"
COMMENT = PR + "#issuecomment-123"
SECOND_COMMENT = PR + "#issuecomment-124"
TID = "T-7KQ2"
ANN, BOB, CAT = ("@%s:magicbean.dev" % n for n in ("ann", "bob", "cat"))
NOW = 1800000000


def at(minute):
    return NOW + minute * 60


def event(user, minute, body):
    return {"sender": "@%s:magicbean.dev" % user, "origin_server_ts": at(minute) * 1000, "body": body}


def footer(commit, head):
    return "\nCommit: %s\nHead: %s" % (commit, head)


def message(action, rest="", commit=None, head=None, tid=TID):
    text = "%s %s %s%s" % (tr.MARK, tid, action, ": " + rest if rest else "")
    return text + (footer(commit, head) if commit is not None else "")


def request(commit=HEAD, head=HEAD, notes="Check the change.", tid=TID, minute=0):
    body = message("request", "Pin test", tid=tid) + "\nNeeds: Linux\n" + PR
    if notes:
        body += "\n" + notes
    if commit is not None:
        body += footer(commit, head)
    return event("ann", minute, body)


def claim(user="bob", minute=1, commit=HEAD, head=HEAD, until=80):
    return event(user, minute, message("claim", "until " + tr.stamp(at(until)), commit, head))


def done(user="bob", minute=2, commit=HEAD, head=HEAD, comment=COMMENT, result="pass"):
    return event(user, minute, message("done " + result, comment, commit, head))


def update(commit=HEAD, head=HEAD, user="ann", minute=3):
    return event(user, minute, message("update", commit, commit, head))


def changed(body, mutation):
    if "body" in mutation:
        return copy.deepcopy(mutation["body"])
    result = copy.deepcopy(body)
    if "remove" in mutation:
        result.pop(mutation["remove"])
    else:
        result[mutation["field"]] = mutation["value"]
    return result


class PinTests(unittest.TestCase):
    def setUp(self):
        # A missed mock fails closed rather than reading credentials or reaching a server.
        for obj, name in ((tr.urllib.request, "urlopen"), (tr.team_chat, "session"),
                          (tr.magicbean_login, "forge_session")):
            patch = mock.patch.object(obj, name, side_effect=AssertionError("unexpected live " + name))
            patch.start()
            self.addCleanup(patch.stop)

    def api(self, overrides=None):
        routes = dict(ROUTES)
        routes.update(overrides or {})
        calls = []

        def get(path, token=None):
            self.assertIsNone(token, "pin membership GETs must be public")
            path, _, query = path.partition("?")
            if path.startswith("/git/commits/"):
                self.assertEqual(query, "stat=false&files=false&verification=false")
            elif path.startswith("/compare/"):
                self.assertEqual(query, "files=false&verification=false")
            else:
                self.assertEqual(query, "")
            calls.append(path)
            self.assertIn(path, routes, "unexpected endpoint; no live fallback")
            value = routes[path]
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)

        return get, calls

    def replay(self, events, minute=10):
        return tr.replay(copy.deepcopy(events), now=at(minute))[TID]

    def current(self, state):
        return tr.public(state)["current_results"]

    def run_cli(self, events, user, *argv, head=HEAD, info=None, overrides=None, minute=10):
        sent, out, err = [], io.StringIO(), io.StringIO()
        config = {"user_id": "@%s:magicbean.dev" % user, "base_url": "http://offline.invalid", "access_token": "fake"}
        info = info if info is not None else {"state": "open", "labels": [], "author": "ann", "writer": True,
                                             "head": head}
        get, calls = self.api(overrides)

        def send(c, rid, text):
            sent.append(text)
            events.append(event(user, minute, text))

        with mock.patch.object(tr.team_chat, "session", return_value=config), \
                mock.patch.object(tr.team_chat, "room_id", return_value="!offline"), \
                mock.patch.object(tr, "history", side_effect=lambda c, r: copy.deepcopy(events)), \
                mock.patch.object(tr, "pr_info", side_effect=info if isinstance(info, Exception) else None,
                                  return_value=info), \
                mock.patch.object(tr, "forge_get", side_effect=get), \
                mock.patch.object(tr, "send", side_effect=send), \
                mock.patch.object(tr.time, "time", return_value=at(minute)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tr.main(list(argv))
        return code, sent, out.getvalue(), err.getvalue(), calls

    def refused(self, result, code=3):
        self.assertEqual(result[0], code, result[2:4])
        self.assertEqual(result[1], [], "refusal must not post")

    def test_recorded_provenance_and_real_comparison_directions(self):
        for item in RECORDED:
            self.assertEqual(item["kind"], "recorded")
            self.assertEqual(item["method"], "GET")
            self.assertEqual(item["authentication"], "none")
            self.assertTrue(item["url"].startswith(tr.API + "/"))
        self.assertEqual(MUTATIONS["kind"], "mutation")
        self.assertEqual(ROUTES["/compare/%s...%s" % (OLD, HEAD)]["total_commits"], 1)
        self.assertEqual(ROUTES["/compare/%s...%s" % (HEAD, OLD)]["total_commits"], 0)
        self.assertEqual(ROUTES["/compare/%s...%s" % (HEAD, HEAD)]["total_commits"], 0)
        for a, b in ((DIVERGENT, DIVERGENT_BASE), (DIVERGENT_BASE, DIVERGENT)):
            self.assertGreater(ROUTES["/compare/%s...%s" % (a, b)]["total_commits"], 0)

    def test_resolve_omitted_full_and_abbreviated_equal_head(self):
        for commit in (None, HEAD, HEAD.upper(), HEAD[:9]):
            with self.subTest(commit=commit):
                get, calls = self.api()
                with mock.patch.object(tr, "forge_get", side_effect=get):
                    self.assertEqual(tr.pr_commit(commit, HEAD), HEAD)
                if commit == HEAD[:9]:
                    self.assertIn("/git/commits/" + commit, calls)

    def test_accepted_prefix_length_boundaries_use_labelled_routing_mutation(self):
        for length in (7, 39):
            with self.subTest(length=length):
                get, _ = self.api({"/git/commits/" + HEAD[:length]: ROUTES["/git/commits/" + HEAD]})
                with mock.patch.object(tr, "forge_get", side_effect=get):
                    self.assertEqual(tr.pr_commit(HEAD[:length], HEAD), HEAD)

    def test_ancestor_requires_both_comparisons(self):
        get, calls = self.api()
        with mock.patch.object(tr, "forge_get", side_effect=get):
            self.assertEqual(tr.pr_commit(OLD, HEAD), OLD)
        self.assertIn("/compare/%s...%s" % (OLD, HEAD), calls)
        self.assertIn("/compare/%s...%s" % (HEAD, OLD), calls)

    def test_descendant_and_real_divergent_commit_refused(self):
        for commit, head in ((HEAD, OLD), (DIVERGENT, DIVERGENT_BASE)):
            with self.subTest(commit=commit, head=head):
                get, calls = self.api()
                with mock.patch.object(tr, "forge_get", side_effect=get), self.assertRaises(tr.team_chat.ChatError):
                    tr.pr_commit(commit, head)
                self.assertIn("/compare/%s...%s" % (commit, head), calls)
                self.assertIn("/compare/%s...%s" % (head, commit), calls)

    def test_unequal_both_zero_is_not_identical_or_ancestor(self):
        zero = fixture(MUTATIONS["unsafe_both_zero"]["source"])["body"]
        get, _ = self.api({"/compare/%s...%s" % (OLD, HEAD): zero})
        with mock.patch.object(tr, "forge_get", side_effect=get), self.assertRaises(tr.team_chat.ChatError):
            tr.pr_commit(OLD, HEAD)

    def test_malformed_commit_and_head_inputs(self):
        for commit in ("", "abc123", "a" * 41, "g" * 40, "main", "HEAD~1", "../main", "a b c d", 123, []):
            with self.subTest(commit=commit), mock.patch.object(tr, "forge_get") as get, \
                    self.assertRaises(tr.team_chat.ChatError):
                tr.pr_commit(commit, HEAD)
            get.assert_not_called()
        for head in (None, "", HEAD[:9], "a" * 41, "g" * 40, 123, []):
            with self.subTest(head=head), mock.patch.object(tr, "forge_get") as get, \
                    self.assertRaises(tr.team_chat.ChatError):
                tr.pr_commit(None, head)
            get.assert_not_called()

    def test_malformed_resolution_bodies(self):
        source = fixture(MUTATIONS["commit_source"])["body"]
        for mutation in MUTATIONS["commit_cases"]:
            with self.subTest(case=mutation["name"]):
                get, _ = self.api({"/git/commits/" + HEAD[:9]: changed(source, mutation)})
                with mock.patch.object(tr, "forge_get", side_effect=get), self.assertRaises(tr.team_chat.ChatError):
                    tr.pr_commit(HEAD[:9], HEAD)

    def test_malformed_comparison_bodies_in_either_direction(self):
        source = fixture(MUTATIONS["compare_source"])["body"]
        for mutation in MUTATIONS["compare_cases"]:
            for a, b in ((OLD, HEAD), (HEAD, OLD)):
                with self.subTest(case=mutation["name"], direction=(a, b)):
                    get, _ = self.api({"/compare/%s...%s" % (a, b): changed(source, mutation)})
                    with mock.patch.object(tr, "forge_get", side_effect=get), self.assertRaises(tr.team_chat.ChatError):
                        tr.pr_commit(OLD, HEAD)

    def test_missing_ambiguous_and_unavailable_responses(self):
        paths = ["/git/commits/" + HEAD[:9], "/compare/%s...%s" % (OLD, HEAD),
                 "/compare/%s...%s" % (HEAD, OLD)]
        for case in MUTATIONS["transport_cases"]:
            for path in paths:
                with self.subTest(case=case["name"], path=path):
                    error = urllib.error.HTTPError(tr.API + path, case["status"], case["name"], {}, None)
                    get, _ = self.api({path: error})
                    commit = HEAD[:9] if path.startswith("/git/") else OLD
                    with mock.patch.object(tr, "forge_get", side_effect=get), self.assertRaises(tr.team_chat.ChatError):
                        tr.pr_commit(commit, HEAD)
        for error in (urllib.error.URLError("offline"), OSError("offline"), ValueError("malformed JSON")):
            with self.subTest(error=type(error).__name__):
                get, _ = self.api({"/git/commits/" + HEAD[:9]: error})
                with mock.patch.object(tr, "forge_get", side_effect=get), self.assertRaises(tr.team_chat.ChatError):
                    tr.pr_commit(HEAD[:9], HEAD)

    def test_pr_info_carries_real_head_without_changing_permission_contract(self):
        pr = ROUTES["/pulls/11"]
        calls = []

        def get(path, token=None):
            calls.append((path, token))
            if path == "/pulls/11":
                self.assertIsNone(token)
                return copy.deepcopy(pr)
            self.assertEqual(path, "/collaborators/%s/permission" % pr["user"]["login"])
            self.assertEqual(token, "fake-permission-token")
            return {"permission": "write"}  # Synthetic existing authenticated permission response.

        with mock.patch.object(tr.magicbean_login, "forge_session", return_value=None), \
                mock.patch.object(tr, "forge_get", side_effect=get):
            info = tr.pr_info(11)
        self.assertEqual(info["head"], pr["head"]["sha"])
        self.assertFalse(info["writer"])
        self.assertEqual(calls, [("/pulls/11", None)])
        with mock.patch.object(tr.magicbean_login, "forge_session", return_value={"access_token": "fake-permission-token"}), \
                mock.patch.object(tr, "forge_get", side_effect=get):
            self.assertTrue(tr.pr_info(11)["writer"])

    def test_request_footer_and_note_lines_round_trip(self):
        notes = "Commit: " + OLD + "\nHead: " + OLD + "\nThese are notes, not metadata."
        events = []
        result = self.run_cli(events, "ann", "request", "--pr", "11", "--summary", "Pin test", "--notes", notes)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertEqual(len(result[1]), 1)
        self.assertTrue(result[1][0].endswith(footer(HEAD, HEAD)))
        tid = tr.parse(result[1][0])[0]
        state = tr.replay(events, now=at(10))[tid]
        self.assertEqual((state["commit"], state["head"], state["notes"]), (HEAD, HEAD, notes))
        terminal_notes = "The notes themselves end in a pair:" + footer(OLD, OLD)
        state = self.replay([request(notes=terminal_notes)])
        self.assertEqual(state["notes"], terminal_notes)
        self.assertEqual(state["commit"], HEAD)

    def test_request_explicit_prefix_and_bad_or_unavailable_pin(self):
        events = []
        result = self.run_cli(events, "ann", "request", "--pr", "11", "--summary", "Pin test", "--commit", HEAD[:9])
        self.assertEqual(result[0], 0, result[2:4])
        self.assertTrue(result[1][0].endswith(footer(HEAD, HEAD)))
        for args, info in ((["--commit", "main"], None), ([], tr.team_chat.ChatError("cannot reach Forgejo"))):
            with self.subTest(args=args):
                self.refused(self.run_cli([], "ann", "request", "--pr", "11", "--summary", "x", *args, info=info), 1)

    def test_claim_default_refuses_head_drift_explicit_confirms_same_pin(self):
        events = [request(OLD, OLD)]
        self.refused(self.run_cli(events, "bob", "claim", TID))
        self.refused(self.run_cli(events, "bob", "claim", TID, "--commit", HEAD))
        result = self.run_cli(events, "bob", "claim", TID, "--commit", OLD)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertTrue(result[1][0].endswith(footer(OLD, HEAD)))
        state = self.replay(events)
        self.assertEqual(state["claim_commits"][BOB], OLD)
        self.assertEqual(state["claim_heads"][BOB], HEAD)
        zero = fixture("compare-reverse.json")["body"]
        self.refused(self.run_cli([request(OLD, OLD)], "bob", "claim", TID, "--commit", OLD,
                                 overrides={"/compare/%s...%s" % (OLD, HEAD): zero}), 1)

    def test_claim_default_at_head_and_permission_refusals(self):
        events = [request()]
        result = self.run_cli(events, "bob", "claim", TID)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertTrue(result[1][0].startswith(message("claim", "until ")))
        self.assertTrue(result[1][0].endswith(footer(HEAD, HEAD)))
        base = {"state": "open", "labels": [], "author": "x", "writer": False, "head": HEAD}
        for info in (base, dict(base, writer=True, state="closed"), dict(base, writer=True, state="merged")):
            self.refused(self.run_cli([request()], "bob", "claim", TID, info=info))
        self.refused(self.run_cli([request()], "bob", "claim", TID,
                                 info=tr.team_chat.ChatError("cannot reach Forgejo")), 1)
        self.assertEqual(self.run_cli([request()], "bob", "claim", TID,
                                     info=dict(base, labels=[tr.LABEL]))[0], 0)

    def test_replay_claim_requires_matching_full_metadata(self):
        for bad in (message("claim", "until " + tr.stamp(at(80))),
                    message("claim", "until " + tr.stamp(at(80)), OLD, HEAD),
                    message("claim", "until " + tr.stamp(at(80)), HEAD[:9], HEAD)):
            with self.subTest(body=bad):
                state = self.replay([request(), event("bob", 1, bad)])
                self.assertNotIn(BOB, state["claims"])
                self.assertNotIn(BOB, state["claim_commits"])
        state = self.replay([request(), claim(), claim("cat", 2)])
        self.assertEqual(set(state["claims"]), {BOB, CAT})
        self.assertEqual(state["claim_commits"], {BOB: HEAD, CAT: HEAD})
        self.assertEqual(state["claim_heads"], {BOB: HEAD, CAT: HEAD})

    def test_update_only_requester_and_retains_results_and_claims(self):
        events = [request(OLD, OLD), done("cat", 1, OLD, OLD), claim(commit=OLD, head=OLD)]
        self.refused(self.run_cli(events, "bob", "update", TID, "--commit", HEAD))
        result = self.run_cli(events, "ann", "update", TID, "--commit", HEAD[:9])
        self.assertEqual(result[0], 0, result[2:4])
        self.assertEqual(result[1], [message("update", HEAD, HEAD, HEAD)])
        state = self.replay(events)
        self.assertEqual((state["commit"], state["head"]), (HEAD, HEAD))
        self.assertEqual(state["results"][CAT]["commit"], OLD)
        self.assertEqual(self.current(state), {})
        self.assertEqual(state["claim_commits"][BOB], OLD)
        self.assertEqual(state["claim_heads"][BOB], OLD)
        before = [request(OLD, OLD), update(user="bob")]
        self.assertEqual(self.replay(before)["commit"], OLD)
        closed = [request(), event("ann", 2, message("cancel"))]
        self.refused(self.run_cli(closed, "ann", "update", TID, "--commit", HEAD))
        self.assertEqual(self.replay(closed + [update(OLD, HEAD)])["commit"], HEAD)
        self.refused(self.run_cli([request()], "ann", "update", TID, "--commit", DIVERGENT,
                                 head=DIVERGENT_BASE), 1)

    def test_renew_preserves_old_claim_snapshots_after_update(self):
        events = [request(OLD, OLD), claim(commit=OLD, head=OLD), update()]
        result = self.run_cli(events, "bob", "renew")
        self.assertEqual(result[0], 0, result[2:4])
        state = self.replay(events, minute=90)
        self.assertIn(BOB, state["claims"])
        self.assertEqual(state["claim_commits"][BOB], OLD)
        self.assertEqual(state["claim_heads"][BOB], OLD)
        spoof = event("bob", 4, message("renew", "until " + tr.stamp(at(150)), HEAD, HEAD))
        state = self.replay(events[:3] + [spoof])
        self.assertEqual((state["claim_commits"][BOB], state["claim_heads"][BOB]), (OLD, OLD))

    def test_done_defaults_claimed_pin_and_clears_snapshots(self):
        events = [request(), claim()]
        result = self.run_cli(events, "bob", "done", TID, "--result", "mixed", "--comment", COMMENT)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertEqual(result[1], [message("done mixed", COMMENT, HEAD, HEAD)])
        state = self.replay(events)
        self.assertEqual(state["results"][BOB]["commit"], HEAD)
        self.assertEqual(self.current(state)[BOB]["result"], "mixed")
        for field in ("claims", "claim_commits", "claim_heads"):
            self.assertNotIn(BOB, state[field])

    def test_done_confirm_same_prefix_refuse_different_claimed_commit(self):
        events = [request(), claim()]
        self.refused(self.run_cli(events, "bob", "done", TID, "--result", "pass", "--comment", COMMENT,
                                 "--commit", OLD))
        self.assertEqual(self.replay(events)["claim_commits"][BOB], HEAD)
        result = self.run_cli(events, "bob", "done", TID, "--result", "pass", "--comment", COMMENT,
                              "--commit", HEAD[:9])
        self.assertEqual(result[0], 0, result[2:4])
        self.assertTrue(result[1][0].endswith(footer(HEAD, HEAD)))
        state = self.replay([request(), claim(), done(commit=OLD)])
        self.assertEqual(state["results"], {})
        self.assertIn(BOB, state["claims"])

    def test_done_without_claim_requires_explicit_full_sha(self):
        for suffix in ([], ["--commit", HEAD[:9]]):
            with self.subTest(suffix=suffix):
                self.refused(self.run_cli([request()], "bob", "done", TID, "--result", "pass",
                                         "--comment", COMMENT, *suffix))
        events = [request()]
        result = self.run_cli(events, "bob", "done", TID, "--result", "pass", "--comment", COMMENT,
                              "--commit", HEAD)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertEqual(self.current(self.replay(events))[BOB]["commit"], HEAD)
        state = self.replay([request(), done(commit=None)])
        self.assertEqual(state["results"], {})

    def test_done_refuses_stale_claim_until_claim_again(self):
        events = [request(OLD, OLD)]
        result = self.run_cli(events, "bob", "claim", TID, head=OLD, minute=1)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertIn("build exactly " + OLD, result[2])
        result = self.run_cli(events, "ann", "update", TID, "--commit", HEAD, minute=3)
        self.assertEqual(result[0], 0, result[2:4])
        before = copy.deepcopy(events)
        result = self.run_cli(events, "bob", "done", TID, "--result", "pass", "--comment", COMMENT,
                              info=tr.team_chat.ChatError("cannot reach Forgejo"))
        self.refused(result)
        self.assertEqual(result[2], "")
        self.assertEqual(result[3],
                         "refused: your claim is for earlier commit %s, but %s now requests %s; "
                         "run claim %s --again to test the current commit, or done %s --commit %s "
                         "to report what you tested at the earlier commit\n"
                         % (OLD, TID, HEAD, TID, TID, OLD))
        self.assertEqual(result[4], [])
        self.assertEqual(events, before)
        state = self.replay(events)
        self.assertEqual((state["claim_commits"][BOB], state["claim_heads"][BOB]), (OLD, OLD))
        self.assertEqual(state["results"], {})
        self.assertEqual(self.current(state), {})
        self.refused(self.run_cli(events, "bob", "done", TID, "--result", "pass", "--comment", COMMENT,
                                 "--commit", HEAD))
        result = self.run_cli(events, "bob", "claim", TID, "--again", minute=11)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertIn("build exactly " + HEAD, result[2])
        state = self.replay(events, minute=11)
        self.assertEqual((state["claim_commits"][BOB], state["claim_heads"][BOB]), (HEAD, HEAD))
        result = self.run_cli(events, "bob", "done", TID, "--result", "pass", "--comment", SECOND_COMMENT, minute=12)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertEqual(result[1], [message("done pass", SECOND_COMMENT, HEAD, HEAD)])
        self.assertEqual(result[2], "marked %s done (pass)\n" % TID)
        state = self.replay(events, minute=12)
        self.assertEqual(self.current(state)[BOB]["commit"], HEAD)
        self.assertEqual(self.current(state)[BOB]["comment"], SECOND_COMMENT)
        for field in ("claims", "claim_commits", "claim_heads"):
            self.assertNotIn(BOB, state[field])

    def test_show_and_list_mark_stale_claims_after_update(self):
        events = [request(OLD, OLD), claim(commit=OLD, head=OLD)]
        result = self.run_cli(events, "ann", "update", TID, "--commit", HEAD, minute=3)
        self.assertEqual(result[0], 0, result[2:4])
        result = self.run_cli(events, "cat", "claim", TID, minute=4)
        self.assertEqual(result[0], 0, result[2:4])
        for user, name in (("bob", "you"), ("ann", BOB)):
            for argv in (("show", TID), ("list",)):
                with self.subTest(user=user, argv=argv):
                    result = self.run_cli(events, user, *argv)
                    self.assertEqual(result[0], 0, result[2:4])
                    self.assertIn("    being tested by %s (earlier commit %s), %s\n" % (name, OLD, CAT), result[2])
                    self.assertNotIn("%s (earlier commit" % CAT, result[2])
        state = self.replay(events)
        self.assertEqual(state["claim_commits"], {BOB: OLD, CAT: HEAD})
        self.assertEqual(self.current(state), {})

    def test_stale_claim_done_remains_historical_after_retarget(self):
        events = [request(OLD, OLD), claim(commit=OLD, head=OLD), update()]
        result = self.run_cli(events, "bob", "done", TID, "--result", "fail", "--comment", COMMENT,
                              "--commit", OLD, info=tr.team_chat.ChatError("cannot reach Forgejo"))
        self.assertEqual(result[0], 0, result[2:4])
        self.assertTrue(result[1][0].endswith(footer(OLD, OLD)))
        self.assertEqual(result[2], "marked %s done (fail)\n"
                         "reported earlier commit %s; current commit %s still has no result from you\n"
                         % (TID, OLD, HEAD))
        self.assertEqual(result[4], [])
        state = self.replay(events)
        self.assertEqual(state["results"][BOB]["commit"], OLD)
        self.assertEqual(state["results"][BOB]["head"], OLD)
        self.assertEqual(self.current(state), {})
        self.assertEqual(state["commit"], HEAD)
        self.assertNotIn(BOB, state["claim_heads"])

    def test_expiry_release_and_cancel_remove_claim_snapshots(self):
        for suffix, minute in (([], 81), ([event("bob", 2, message("release"))], 10),
                               ([event("ann", 2, message("cancel"))], 10)):
            with self.subTest(suffix=suffix, minute=minute):
                state = self.replay([request(), claim()] + suffix, minute)
                for field in ("claims", "claim_commits", "claim_heads"):
                    self.assertNotIn(BOB, state[field])
        events = [request(), claim()]
        self.assertEqual(self.run_cli(events, "bob", "release", TID)[0], 0)
        self.refused(self.run_cli(events, "bob", "done", TID, "--result", "pass", "--comment", COMMENT))
        self.refused(self.run_cli([request(), claim()], "bob", "done", TID, "--result", "pass",
                                 "--comment", COMMENT, minute=81))
        state = self.replay([request(), claim(), claim("cat", 2), event("bob", 3, message("release"))])
        self.assertEqual(state["claim_commits"], {CAT: HEAD})
        self.assertEqual(state["claim_heads"], {CAT: HEAD})
        events = [request(), claim(), claim("cat", 2)]
        self.refused(self.run_cli(events, "bob", "cancel", TID))
        self.assertEqual(self.run_cli(events, "ann", "cancel", TID)[0], 0)
        state = self.replay(events)
        for field in ("claims", "claim_commits", "claim_heads"):
            self.assertEqual(state[field], {})

    def test_current_results_drive_display_sorting_json_and_claim_again(self):
        events = [request(OLD, OLD), done(commit=OLD, head=OLD), update()]
        old = self.replay(events)
        self.assertIn(BOB, old["results"])
        self.assertEqual(self.current(old), {})
        current = self.replay([request(), done()])
        self.assertLess(tr.need(old), tr.need(current))
        self.assertNotIn(COMMENT, tr.describe(old))
        self.assertIn(COMMENT, tr.describe(current))
        for argv in (("show", TID, "--json"), ("list", "--mine", "--all", "--json")):
            code, _, out, err, _ = self.run_cli(events, "bob", *argv)
            self.assertEqual(code, 0, err)
            public = json.loads(out)
            public = public[0] if isinstance(public, list) else public
            self.assertIn(BOB, public["results"])
            self.assertEqual(public["current_results"], {})
        self.assertEqual(self.run_cli(events, "bob", "claim", TID)[0], 0)
        current_events = [request(), done()]
        self.refused(self.run_cli(current_events, "bob", "claim", TID))
        self.assertEqual(self.run_cli(current_events, "bob", "claim", TID, "--again")[0], 0)
        self.assertIn(BOB, self.current(self.replay([request(OLD, OLD), done(commit=OLD, head=OLD),
                                                  update(), update(OLD, HEAD, minute=4)])))

    def test_result_link_and_closed_request_rules_still_apply(self):
        for comment in (PR, tr.REPO + "/pulls/12#issuecomment-1", "not a link"):
            with self.subTest(comment=comment):
                self.refused(self.run_cli([request(), claim()], "bob", "done", TID, "--result", "pass",
                                         "--comment", comment))
                self.assertEqual(self.replay([request(), claim(), done(comment=comment)])["results"], {})
        closed = [request(), claim(), event("ann", 2, message("cancel"))]
        self.refused(self.run_cli(closed, "bob", "done", TID, "--result", "pass", "--comment", COMMENT,
                                 "--commit", HEAD))
        self.assertEqual(self.replay(closed + [done(minute=3)])["results"], {})

    def test_legacy_footerless_cards_keep_walk_in_results_and_commands(self):
        legacy = request(commit=None)
        events = [legacy, done(commit=None)]
        state = self.replay(events)
        self.assertFalse(state.get("commit"))
        self.assertEqual(state["notes"], "Check the change.")
        self.assertEqual(self.current(state), state["results"])
        self.assertEqual(state["results"][BOB]["comment"], COMMENT)
        self.refused(self.run_cli(events, "bob", "claim", TID, head=OLD))
        self.assertEqual(self.run_cli(events, "bob", "claim", TID, "--again", head=OLD)[0], 0)
        self.assertNotIn("\nCommit:", events[-1]["body"])
        walk_in = [legacy]
        result = self.run_cli(walk_in, "cat", "done", TID, "--result", "pass", "--comment", COMMENT)
        self.assertEqual(result[0], 0, result[2:4])
        self.assertEqual(result[1], [message("done pass", COMMENT)])
        replaced = self.replay([legacy, done(commit=None), done(minute=3, commit=None, comment=SECOND_COMMENT)])
        self.assertEqual(replaced["results"][BOB]["comment"], SECOND_COMMENT)
        notes = "Commit: " + HEAD + "\nHead: " + HEAD + "\nStill ordinary notes."
        self.assertEqual(self.replay([request(commit=None, notes=notes)])["notes"], notes)

    def test_legacy_commit_flag_refuses_until_requester_introduces_pin(self):
        events = [request(commit=None), done(commit=None)]
        self.refused(self.run_cli(events, "cat", "claim", TID, "--commit", HEAD))
        self.refused(self.run_cli(events, "cat", "done", TID, "--result", "pass",
                                 "--comment", COMMENT, "--commit", HEAD))
        result = self.run_cli(events, "ann", "update", TID, "--commit", HEAD)
        self.assertEqual(result[0], 0, result[2:4])
        state = self.replay(events)
        self.assertEqual(state["commit"], HEAD)
        self.assertIn(BOB, state["results"])
        self.assertEqual(self.current(state), {}, "an unpinned old result cannot satisfy the new pin")
        self.assertEqual(self.run_cli(events, "bob", "claim", TID)[0], 0)

    def test_show_observes_new_head_without_rewriting_pin_or_footer(self):
        events = [request(OLD, OLD)]
        original = copy.deepcopy(events)
        result = self.run_cli(events, "bob", "show", TID, "--json")
        self.assertEqual(result[0], 0, result[2:4])
        shown = json.loads(result[2])
        self.assertEqual((shown["commit"], shown["requested_head"], shown["head"]), (OLD, OLD, HEAD))
        self.assertTrue(shown["head_moved"])
        self.assertEqual(events, original)
        unavailable = self.run_cli(events, "bob", "show", TID, "--json",
                                   info=tr.team_chat.ChatError("cannot reach Forgejo"))
        self.assertEqual(unavailable[0], 0, unavailable[2:4])
        shown = json.loads(unavailable[2])
        self.assertEqual((shown["commit"], shown["requested_head"]), (OLD, OLD))
        self.assertIsNone(shown["head_moved"])

    def test_cli_list_sorts_stale_results_before_current_results(self):
        other = "T-AAAA"
        events = [request(OLD, OLD), done(commit=OLD, head=OLD), update(),
                  request(tid=other, minute=4),
                  event("cat", 5, message("done pass", COMMENT, HEAD, HEAD, tid=other))]
        result = self.run_cli(events, "bob", "list", "--json")
        self.assertEqual(result[0], 0, result[2:4])
        self.assertEqual([t["id"] for t in json.loads(result[2])], [TID, other])

    def test_update_replay_requires_terminal_matching_metadata(self):
        for body in (message("update", HEAD), message("update", OLD, HEAD, HEAD),
                     message("update", HEAD[:9], HEAD[:9], HEAD),
                     message("update", HEAD, HEAD, HEAD) + "\nTrailing note"):
            with self.subTest(body=body):
                state = self.replay([request(OLD, OLD), event("ann", 3, body)])
                self.assertEqual((state["commit"], state["requested_head"]), (OLD, OLD))


if __name__ == "__main__":
    unittest.main()
