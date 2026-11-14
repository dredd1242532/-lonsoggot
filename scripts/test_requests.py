"""Ask the team to test a pull request on hardware you don't have, and pick up such requests, in the Matrix room
#test-requests:magicbean.dev (copied to Discord, where each request shows who is testing it and the results).

    python -I scripts/test_requests.py request --pr 2711 --summary "Keep 8 GB cards inside their memory budget" \
        [--needs "8 GB NVIDIA or AMD card, Windows"] [--notes "what to check, where in the game"] [--commit <sha>]
    python -I scripts/test_requests.py list [--all] [--mine] [--json]
    python -I scripts/test_requests.py show T-7KQ2 [--json]
    python -I scripts/test_requests.py claim T-7KQ2 [--again] [--commit <confirmed-pin>]
    python -I scripts/test_requests.py renew [T-7KQ2]
    python -I scripts/test_requests.py release T-7KQ2
    python -I scripts/test_requests.py done T-7KQ2 --result pass|fail|mixed --comment <URL of your comment on the PR>
        [--commit <tested-sha>]
    python -I scripts/test_requests.py update T-7KQ2 --commit <sha>
    python -I scripts/test_requests.py cancel T-7KQ2

It uses the sign-in of scripts/team_chat.py (run `team_chat.py login` once). Every message is one line that starts
with the request's ID and the action, so people can read it and type it by hand in Element:

    🧪 T-7KQ2 request: Keep 8 GB cards inside their memory budget
    Needs: 8 GB NVIDIA or AMD card, Windows          (then the PR link, then the notes)
    🧪 T-7KQ2 claim: until 2026-11-12 16:40Z
    🧪 T-7KQ2 renew: until 2026-11-12 18:10Z
    🧪 T-7KQ2 release
    🧪 T-7KQ2 done pass: https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/12#issuecomment-123
    🧪 T-7KQ2 cancel

The room's messages are the only record: list and every check below replay them in order. A request stays open until
the person who asked cancels it, and several people can test it at once: each holds their own claim and posts their
own result. A claim lasts 90 minutes (at most 8 hours) and simply clears when it runs out, so a tester renews while
testing (`renew` with no ID renews all of yours). done counts from anyone while the request is open, links to a comment
on the request's PR, and replaces that person's earlier result. release is the claimer's; cancel is the requester's.
A message that breaks these rules changes nothing. (The approval below is git.magicbean.dev's, so the script checks it
before claiming; a claim typed by hand on a request that isn't approved shows nowhere on Discord.)

Requests are for Longshot's pull requests on git.magicbean.dev. Only approved requests can be claimed: the PR's author
may push to Longshot, or a maintainer put the "testing wanted" label on it. Anything else waits for a maintainer to add
the label (the card on Discord says so, and list shows it last); this keeps strangers' code off testers' PCs until
someone has looked at it. The script asks git.magicbean.dev, with the team sign-in.

New cards pin a full commit SHA. A trailing Commit/Head footer records the requested pin and the observed PR head;
earlier note lines stay notes. A moved head requires explicit confirmation of the SAME pin, never silently following
the new head. Only the requester may update the pin. Claims remember their pin/head; done records the tested pin,
refusing another claimed commit. After an update, an earlier claim needs an explicit --commit to report its result,
or claim --again to test the current pin. After expiry/release give the full tested SHA explicitly. Earlier results
remain in JSON results, but only matching-pin current_results are displayed or counted. Footerless cards retain the old rules.

list puts first what most needs a tester: requests nobody has tested or is testing, then the ones being tested with no
result yet, then the ones with results; oldest first within each. Exit code 3: the action was refused (closed, not
yours, already tested by you).
Standard library only.
"""
import argparse
import calendar
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import magicbean_login  # noqa: E402  (same folder; python -I does not add it to the path)
import team_chat  # noqa: E402

ROOM = "#test-requests:magicbean.dev"
REPO = "https://git.magicbean.dev/MagicBeanDev/Longshot"
MARK = "\N{TEST TUBE}"
ID_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
DEFAULT_TTL = 90
MAX_TTL = 480
API = "https://git.magicbean.dev/api/v1/repos/MagicBeanDev/Longshot"
LABEL = "testing wanted"
WRITERS = ("owner", "admin", "write")  # may push to Longshot: their PRs need no label
HISTORY_LIMIT = 50000  # messages read back; renews add one per tester every half hour
EXIT_REFUSED = team_chat.EXIT_REFUSED

LINE = re.compile(r"^%s\s*(T-[%s]{4})\s+(request|claim|renew|release|done|cancel|update)\b(?:\s+(pass|fail|mixed))?\s*:?\s*(.*)$"
                  % (MARK, ID_CHARS))
UNTIL = re.compile(r"until\s+(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d)Z", re.I)
PR_URL = re.compile(r"https://git\.magicbean\.dev/MagicBeanDev/Longshot/pulls/(\d+)")
COMMENT_URL = re.compile(r"^https://git\.magicbean\.dev/MagicBeanDev/Longshot/pulls/(\d+)#issuecomment-\d+$")
SHA = re.compile(r"[0-9a-fA-F]{7,40}")
FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")
FOOTER = re.compile(r"(?:^|\n)Commit: ([0-9a-fA-F]{40})\nHead: ([0-9a-fA-F]{40})\Z")


def pin_fields(text):
    """Only the final reserved footer is metadata; earlier note lines stay notes."""
    match = FOOTER.search(text)
    return (text[:match.start()].rstrip(), match[1].lower(), match[2].lower()) if match else (text, None, None)


def pin_footer(commit, head):
    return "\nCommit: %s\nHead: %s" % (commit, head)


def same_commit(candidate, commit):
    return bool(isinstance(candidate, str) and SHA.fullmatch(candidate) and commit.startswith(candidate.lower()))


def clear_claim(t, user):
    for key in ("claims", "claim_commits", "claim_heads"):
        t[key].pop(user, None)


def current_results(t):
    """Historical results remain in JSON, but do not satisfy a different pin."""
    return {u: r for u, r in t["results"].items()
            if not t.get("commit") or r.get("commit") == t["commit"]}


def pr_commit(commit, head):
    """Resolve a pin and prove membership using both Forgejo comparison directions."""
    if not isinstance(head, str) or not FULL_SHA.fullmatch(head):
        raise team_chat.ChatError("Forgejo did not return a full PR head SHA")
    head = head.lower()
    if commit is not None and (not isinstance(commit, str) or not SHA.fullmatch(commit)):
        raise team_chat.ChatError("a commit must be 7 to 40 hexadecimal characters")
    if commit is None or commit.lower() == head:
        return head
    commit = commit.lower()
    try:
        pin = forge_get("/git/commits/" + commit + "?stat=false&files=false&verification=false").get("sha")
        if not isinstance(pin, str) or not FULL_SHA.fullmatch(pin) or not pin.lower().startswith(commit.lower()):
            raise team_chat.ChatError("Forgejo did not resolve the requested commit")
        pin = pin.lower()
        if pin == head:
            return pin
        ahead = forge_get("/compare/%s...%s?files=false&verification=false" % (pin, head)).get("total_commits")
        behind = forge_get("/compare/%s...%s?files=false&verification=false" % (head, pin)).get("total_commits")
    except (urllib.error.URLError, OSError, ValueError, AttributeError) as error:
        raise team_chat.ChatError("cannot verify the pinned commit on Forgejo: %s" % error) from None
    # Unequal unrelated histories can return zero BOTH ways. One empty comparison
    # is not ancestry proof; require positive evidence in the other direction.
    if type(ahead) is not int or type(behind) is not int or ahead <= 0 or behind != 0:
        raise team_chat.ChatError("the pin is not a verified ancestor of the PR head (diverged, newer, or unknown)")
    return pin


def parse(body):
    """(id, action, result, rest of the first line, following lines) or None."""
    if not isinstance(body, str):
        return None
    first, _, more = body.strip().partition("\n")
    m = LINE.match(first.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4).strip(), more.strip()


def until_time(text):
    m = UNTIL.search(text)
    if not m:
        return None
    return calendar.timegm(time.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M"))


def expire(t, ts):
    """Drop the claims that ran out by ts."""
    for user, until in sorted(t["claims"].items(), key=lambda kv: kv[1]):
        if until <= ts:
            clear_claim(t, user)
            t["history"].append((until, user, "expired"))


def replay(events, now=None):
    """Requests by ID, from the room's messages oldest first: each event a dict with sender, origin_server_ts (ms)
    and body. The same rules as the Discord relay."""
    now = time.time() if now is None else now
    tests = {}
    for ev in events:
        p = parse(ev.get("body"))
        if not p:
            continue
        tid, action, result, rest, more = p
        more, commit, head = pin_fields(more)
        sender, ts = ev.get("sender"), ev.get("origin_server_ts", 0) / 1000
        t = tests.get(tid)
        if action == "request":
            if t:
                continue  # an ID is used once
            pr = PR_URL.search(rest + "\n" + more)
            needs, notes = "", []
            for extra in more.splitlines():
                extra = extra.strip()
                if extra.lower().startswith("needs:") and not needs:
                    needs = extra[6:].strip()
                elif extra and not (pr and extra == pr.group(0)):
                    notes.append(extra)
            tests[tid] = {"id": tid, "requester": sender, "requested": ts, "summary": rest, "needs": needs,
                          "pr": int(pr.group(1)) if pr else None, "notes": "\n".join(notes), "status": "open",
                          "claims": {}, "claim_commits": {}, "claim_heads": {}, "results": {},
                          "history": [(ts, sender, action)]}
            if commit:
                tests[tid].update(commit=commit, head=head, requested_head=head)
            continue
        if not t or t["status"] != "open":
            continue
        expire(t, ts)
        mine = sender in t["claims"]
        if action == "claim" or action == "renew" and mine:
            if action == "claim" and t.get("commit") and commit != t["commit"]:
                continue
            if action == "claim" and commit:
                t["claim_commits"][sender], t["claim_heads"][sender] = commit, head
            until = until_time(rest)
            if until is None or until <= ts:
                until = ts + 60 * DEFAULT_TTL
            t["claims"][sender] = min(until, ts + 60 * MAX_TTL)
            action = "renew" if mine else "claim"
        elif action == "release" and mine:
            clear_claim(t, sender)
        elif action == "done" and result:
            link = COMMENT_URL.match(rest)
            if not link or t["pr"] and int(link.group(1)) != t["pr"]:
                continue
            claimed = t["claim_commits"].get(sender)
            if t.get("commit") and (not commit or claimed and commit != claimed):
                continue
            clear_claim(t, sender)
            t["results"][sender] = {"result": result, "comment": rest, "time": ts}
            if commit:
                t["results"][sender].update(commit=commit, head=head)
        elif action == "update" and sender == t["requester"] and commit and rest == commit:
            t.update(commit=commit, head=head, requested_head=head)
        elif action == "cancel" and sender == t["requester"]:
            t["status"], t["claims"] = "closed", {}
            t["claim_commits"], t["claim_heads"] = {}, {}
        else:
            continue
        t["history"].append((ts, sender, action))
    for t in tests.values():
        if t["status"] == "open":
            expire(t, now)
    return tests


def need(t):
    """Sort key: what most needs a tester first; requests waiting for a maintainer last."""
    return (t.get("approved") is False, bool(current_results(t)), bool(t["claims"]), t["requested"])


def forge_get(path, token=None):
    req = urllib.request.Request(API + path, headers={"Accept": "application/json", "User-Agent": team_chat.USER_AGENT})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def pr_info(number):
    """The PR's state (open, draft, merged, closed), its labels and whether its author may push to Longshot, from
    git.magicbean.dev (the author check needs the team sign-in; without it only the label counts)."""
    try:
        pr = forge_get("/pulls/%d" % number)
    except urllib.error.HTTPError as error:
        raise team_chat.ChatError("git.magicbean.dev answered %d for PR #%d" % (error.code, number)) from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise team_chat.ChatError("cannot reach git.magicbean.dev to check PR #%d: %s" % (number, error)) from None
    state = "merged" if pr.get("merged") else "closed" if pr.get("state") == "closed" \
        else "draft" if pr.get("draft") else "open"
    author = (pr.get("user") or {}).get("login")
    writer = False
    signed_in = magicbean_login.forge_session()
    if author and signed_in:
        try:
            perm = forge_get("/collaborators/%s/permission" % urllib.parse.quote(author), signed_in["access_token"])
            writer = perm.get("permission") in WRITERS
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return {"state": state, "labels": [label.get("name") for label in pr.get("labels") or []], "author": author,
            "writer": writer, "head": (pr.get("head") or {}).get("sha")}


def approved(info):
    return info["writer"] or LABEL in info["labels"]


def annotate(tests):
    """Add each open request's PR state and approval (one call per PR); unknown when git.magicbean.dev can't be asked."""
    seen = {}
    for t in tests:
        if t["status"] != "open" or not t["pr"]:
            continue
        if t["pr"] not in seen:
            try:
                seen[t["pr"]] = pr_info(t["pr"])
            except team_chat.ChatError as error:
                print("warning: %s" % error, file=sys.stderr)
                seen[t["pr"]] = None
        info = seen[t["pr"]]
        t["pr_state"] = info and info["state"]
        t["approved"] = info and approved(info)
        if t.get("commit"):
            t["head"] = info and info.get("head")
            t["head_moved"] = t["head"] != t["commit"] if t["head"] else None


def history(config, rid):
    """Every message in the room, oldest first (up to HISTORY_LIMIT)."""
    events, start = [], None
    while len(events) < HISTORY_LIMIT:
        path = "/rooms/%s/messages?dir=b&limit=1000" % team_chat.quote(rid)
        if start:
            path += "&from=" + team_chat.quote(start)
        page = team_chat.request("GET", team_chat.client(config["base_url"], path), config["access_token"])
        for ev in page.get("chunk", []):
            content = ev.get("content", {})
            if ev.get("type") == "m.room.message" and "m.new_content" not in content:
                events.append({"sender": ev.get("sender"), "origin_server_ts": ev.get("origin_server_ts", 0),
                               "body": content.get("body"), "event_id": ev.get("event_id")})
        start = page.get("end")
        if not start or not page.get("chunk"):
            break
    events.reverse()
    return events


def state(config):
    rid = team_chat.room_id(config, config.get("test_room", ROOM))
    return rid, replay(history(config, rid))


def send(config, rid, text):
    path = "/rooms/%s/send/m.room.message/%s" % (team_chat.quote(rid), uuid.uuid4().hex)
    team_chat.request("PUT", team_chat.client(config["base_url"], path), config["access_token"],
                      body={"msgtype": "m.text", "body": text})


def new_id(taken):
    while True:
        tid = "T-" + "".join(random.SystemRandom().choice(ID_CHARS) for _ in range(4))
        if tid not in taken:
            return tid


def pr_number(text):
    m = PR_URL.search(text) or re.match(r"^#?(\d+)$", text.strip())
    if not m:
        raise team_chat.ChatError("--pr takes a number or %s/pulls/<number>" % REPO)
    return int(m.group(1))


def stamp(ts):
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(ts))


def until_line():
    return "until %s" % stamp(time.time() + 60 * DEFAULT_TTL)


def describe(t, me=None):
    def who(user):
        return "you" if user == me else user
    head = "%s  %s  %s/pulls/%s  %s" % (t["id"], t["status"].upper(), REPO, t["pr"], t["summary"])
    lines = [head, "    asked by %s on %s" % (who(t["requester"]), stamp(t["requested"]))]
    if t.get("commit"):
        lines.append("    commit: %s; current PR head: %s%s" % (
            t["commit"], t.get("head") or "unknown", " (moved)" if t.get("head_moved") else ""))
    if t["needs"]:
        lines.append("    needs: " + t["needs"])
    if t["claims"]:
        lines.append("    being tested by " + ", ".join(
            who(u) + (" (earlier commit %s)" % t["claim_commits"][u]
                      if t.get("commit") and t["claim_commits"].get(u)
                      and t["claim_commits"][u] != t["commit"] else "")
            for u in sorted(t["claims"])))
    for user, r in sorted(current_results(t).items(), key=lambda kv: kv[1]["time"]):
        lines.append("    %s by %s: %s" % (r["result"], who(user), r["comment"]))
    if t.get("approved") is False:
        lines.append('    waiting for a maintainer to add the "%s" label on git.magicbean.dev: not claimable yet' % LABEL)
    elif not t["claims"] and not current_results(t) and t["status"] == "open":
        lines.append("    nobody has tested it yet")
    if t.get("pr_state") in ("merged", "closed"):
        lines.append("    the PR is %s" % t["pr_state"])
    if t["notes"]:
        lines += ["    " + line for line in t["notes"].splitlines()]
    return "\n".join(lines)


def public(t):
    return dict({k: v for k, v in t.items() if k != "history"}, current_results=current_results(t))


def find(tests, tid):
    tid = tid.upper()
    if tid not in tests:
        raise team_chat.ChatError("no request %s in %s" % (tid, ROOM))
    return tests[tid]


def refuse(why):
    print("refused: " + why, file=sys.stderr)
    return EXIT_REFUSED


def cmd_request(args, config):
    pr = pr_number(args.pr)
    summary, needs = " ".join(args.summary.split()), " ".join((args.needs or "").split())
    if not summary:
        raise team_chat.ChatError("--summary is the change in one line")
    rid, tests = state(config)
    for t in tests.values():
        if t["pr"] == pr and t["requester"] == config["user_id"] and t["status"] == "open" \
                and t["needs"].lower() == needs.lower():
            return refuse("%s already asks for this" % t["id"])
    info = pr_info(pr)
    commit = pr_commit(args.commit, info.get("head"))
    tid = new_id(tests)
    text = "%s %s request: %s\n" % (MARK, tid, summary)
    if needs:
        text += "Needs: %s\n" % needs
    text += "%s/pulls/%d" % (REPO, pr)
    if args.notes and args.notes.strip():
        text += "\n" + args.notes.strip()
    send(config, rid, text + pin_footer(commit, info["head"]))
    print("asked for testing as %s" % tid)
    if not approved(info):
        print('note: #%d is not from a collaborator and has no "%s" label, so nobody can claim it until a maintainer '
              "adds the label on git.magicbean.dev" % (pr, LABEL))
    return 0


def cmd_list(args, config):
    _, tests = state(config)
    me = config["user_id"]
    shown = [t for t in tests.values()
             if (args.all or t["status"] == "open")
             and (not args.mine or me == t["requester"] or me in t["claims"] or me in t["results"])]
    annotate(shown)
    shown.sort(key=need)
    if args.json:
        print(json.dumps([public(t) for t in shown], indent=2))
    elif not shown:
        print("no requests" if args.all else "no open requests")
    else:
        print("\n\n".join(describe(t, me) for t in shown))
    return 0


def cmd_show(args, config):
    _, tests = state(config)
    t = find(tests, args.id)
    annotate([t])
    print(json.dumps(public(t), indent=2) if args.json else describe(t, config["user_id"]))
    return 0


def cmd_claim(args, config):
    me = config["user_id"]
    rid, tests = state(config)
    t = find(tests, args.id)
    if t["status"] != "open":
        return refuse("%s is closed" % t["id"])
    if args.commit and not t.get("commit"):
        return refuse("this legacy card has no pin to confirm; ask its requester to update it")
    if me in current_results(t) and not args.again:
        return refuse("you already tested %s (%s); --again to test it again" % (t["id"], current_results(t)[me]["result"]))
    if t["pr"]:
        info = pr_info(t["pr"])  # no answer from git.magicbean.dev: no claim
        if not approved(info):
            return refuse('%s waits for a maintainer: #%d is not from a collaborator and has no "%s" label'
                          % (t["id"], t["pr"], LABEL))
        if info["state"] in ("merged", "closed"):
            return refuse("#%d is %s" % (t["pr"], info["state"]))
    footer = ""
    if t.get("commit"):
        if not t["pr"]:
            return refuse("a pinned card must name its PR")
        if args.commit and not same_commit(args.commit, t["commit"]):
            return refuse("--commit must confirm the card's exact pin, not choose another")
        head = info.get("head")
        if head != t["commit"] and not args.commit:
            return refuse("PR head moved; ask the requester to update, or explicitly confirm --commit " + t["commit"])
        pin = pr_commit(t["commit"], head)
        footer = pin_footer(pin, head)
        print("build exactly " + pin)
    send(config, rid, "%s %s claim: %s%s" % (MARK, t["id"], until_line(), footer))
    others = sorted(u for u in t["claims"] if u != me)
    print("claimed %s%s; renew while you test (`renew` renews all of yours)" % (
        t["id"], ", also being tested by " + ", ".join(others) if others else ""))
    return 0


def cmd_renew(args, config):
    me = config["user_id"]
    rid, tests = state(config)
    held = [t for t in tests.values() if me in t["claims"]]
    if args.id:
        t = find(tests, args.id)
        if t not in held:
            return refuse("you hold no claim on %s (it may have run out: claim it again)" % t["id"])
        held = [t]
    if not held:
        print("no claims to renew")
        return 0
    for t in held:
        send(config, rid, "%s %s renew: %s" % (MARK, t["id"], until_line()))
    print("renewed " + ", ".join(t["id"] for t in held))
    return 0


def cmd_release(args, config):
    me = config["user_id"]
    rid, tests = state(config)
    t = find(tests, args.id)
    if me not in t["claims"]:
        return refuse("you hold no claim on %s" % t["id"])
    send(config, rid, "%s %s release" % (MARK, t["id"]))
    print("released %s" % t["id"])
    return 0


def cmd_done(args, config):
    url = args.comment.strip()
    rid, tests = state(config)
    t = find(tests, args.id)
    if t["status"] != "open":
        return refuse("%s is closed" % t["id"])
    if args.commit and not t.get("commit"):
        return refuse("this legacy card has no pin; report without --commit or ask its requester to update it")
    link = COMMENT_URL.match(url)
    if not link:
        return refuse("--comment must be the link to your comment on the PR (%s/pulls/<n>#issuecomment-...)" % REPO)
    if t["pr"] and int(link.group(1)) != t["pr"]:
        return refuse("that comment is on #%s, the request is for #%s" % (link.group(1), t["pr"]))
    footer = ""
    if t.get("commit"):
        me = config["user_id"]
        claimed = t["claim_commits"].get(me)
        if claimed and claimed != t["commit"] and not args.commit:
            return refuse("your claim is for earlier commit %s, but %s now requests %s; "
                          "run claim %s --again to test the current commit, or done %s --commit %s "
                          "to report what you tested at the earlier commit"
                          % (claimed, t["id"], t["commit"], t["id"], t["id"], claimed))
        commit = args.commit or claimed
        if claimed and not same_commit(commit, claimed):
            return refuse("--commit differs from the commit you claimed")
        if claimed:
            commit = claimed
        if not commit or not FULL_SHA.fullmatch(commit):
            return refuse("no active pinned claim; give the full --commit actually tested")
        head = t["claim_heads"].get(me) or t["requested_head"]
        footer = pin_footer(commit.lower(), head)
    send(config, rid, "%s %s done %s: %s%s" % (MARK, t["id"], args.result, url, footer))
    print("marked %s done (%s)" % (t["id"], args.result))
    if t.get("commit") and claimed and claimed != t["commit"]:
        print("reported earlier commit %s; current commit %s still has no result from you" % (claimed, t["commit"]))
    return 0


def cmd_cancel(args, config):
    me = config["user_id"]
    rid, tests = state(config)
    t = find(tests, args.id)
    if t["requester"] != me:
        return refuse("only %s, who asked, can cancel %s" % (t["requester"], t["id"]))
    if t["status"] != "open":
        return refuse("%s is already closed" % t["id"])
    send(config, rid, "%s %s cancel" % (MARK, t["id"]))
    print("closed %s" % t["id"])
    return 0


def cmd_update(args, config):
    rid, tests = state(config)
    t = find(tests, args.id)
    if t["status"] != "open" or t["requester"] != config["user_id"]:
        return refuse("only the requester can update an open card")
    info = pr_info(t["pr"])
    if info["state"] in ("merged", "closed"):
        return refuse("the PR is " + info["state"])
    commit = pr_commit(args.commit, info.get("head"))
    send(config, rid, "%s %s update: %s%s" % (MARK, t["id"], commit, pin_footer(commit, info["head"])))
    print("updated %s to %s; earlier results remain historical" % (t["id"], commit))
    return 0


def main(argv=None):
    team_chat.unicode_output()  # summaries and notes are anyone's text: no crash on Windows' code page in a pipe
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("request", help="ask for a PR to be tested")
    p.add_argument("--pr", required=True, help="PR number or link")
    p.add_argument("--summary", required=True, help="the change in one line, like a title")
    p.add_argument("--needs", help='what testing needs, one line, e.g. "AMD RX 6000 or newer, Linux"')
    p.add_argument("--notes", help="what to check and where (a few lines, for the tester)")
    p.add_argument("--commit", help="commit to test (default: the current PR head)")
    p = sub.add_parser("update", help="move your open card to another verified commit")
    p.add_argument("id")
    p.add_argument("--commit", required=True)
    p = sub.add_parser("list", help="open requests, what most needs a tester first (--all: closed ones too)")
    p.add_argument("--all", action="store_true")
    p.add_argument("--mine", action="store_true", help="only the ones you asked for, are testing or tested")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="one request")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("claim", help="start testing a request")
    p.add_argument("id")
    p.add_argument("--again", action="store_true", help="test a request you already reported on")
    p.add_argument("--commit", help="explicitly confirm the existing pin when the PR head moved")
    p = sub.add_parser("renew", help="keep your claims (all of them, or one)")
    p.add_argument("id", nargs="?")
    p = sub.add_parser("release", help="stop testing without a result")
    p.add_argument("id")
    p = sub.add_parser("done", help="finished: link your comment on the PR")
    p.add_argument("id")
    p.add_argument("--result", required=True, choices=("pass", "fail", "mixed"))
    p.add_argument("--comment", required=True)
    p.add_argument("--commit", help="commit actually tested (default: your active claim, only if still current)")
    p = sub.add_parser("cancel", help="close your request")
    p.add_argument("id")
    args = parser.parse_args(argv)
    commands = {"request": cmd_request, "list": cmd_list, "show": cmd_show, "claim": cmd_claim, "renew": cmd_renew,
                "release": cmd_release, "done": cmd_done, "cancel": cmd_cancel, "update": cmd_update}
    try:
        return commands[args.command](args, team_chat.session()) or 0
    except team_chat.ChatError as error:
        print("test_requests: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
