"""One sign-in for the team's services on magicbean.dev, used by team_chat.py, test_requests.py and forge_login.py.

Everything there signs in through Forgejo (git.magicbean.dev), which signs people in with Discord: the Matrix server
asks Forgejo who you are, and git asks Forgejo for a token. So one trip through the browser does both. The link
printed is Matrix's sign-in: Sign in with Discord on git.magicbean.dev and approve. The browser comes back to a
one-time listener on 127.0.0.1, which sends it straight on to Forgejo for the git token; Forgejo knows you by then, so
that step needs no click. The tab then says "Signed in".

Saved in the config folder (Linux: $XDG_CONFIG_HOME/longshot, default ~/.config/longshot; Windows: %APPDATA%\\Longshot;
LONGSHOT_TEAM_CHAT_DIR overrides it), readable by you only: team_chat.json (the Matrix session) and forge.json (the
git token, renewed by itself when it runs out).

    python -I scripts/magicbean_login.py login [--no-browser]     (the same as team_chat.py login)
    python -I scripts/magicbean_login.py whoami

The git part uses Forgejo's built-in OAuth application for git (git-credential-oauth's), with PKCE: nothing secret in
the repository. Standard library only; Windows and Linux.
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SERVER = "magicbean.dev"                       # the Matrix server name
FORGE_HOST = "git.magicbean.dev"
FORGE = "https://" + FORGE_HOST
GIT_CLIENT_ID = "a4792ccc-144e-407e-86c9-5e7d8d9c3269"   # Forgejo's default public application "git-credential-oauth"
USER_AGENT = "longshot-login/1"                # (Cloudflare answers 403 to urllib's default agent)
LOGIN_TIMEOUT = 300


class LoginError(Exception):
    pass


def config_dir():
    if os.environ.get("LONGSHOT_TEAM_CHAT_DIR"):
        return os.environ["LONGSHOT_TEAM_CHAT_DIR"]
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Longshot")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "longshot")


def write_private(path, value):
    """Written whole to a file only the user can read, then moved over the old one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(path, 0o600)


# --- the git token -------------------------------------------------------------------------------------------------

def forge_path():
    return os.path.join(config_dir(), "forge.json")


def forge_load():
    try:
        with open(forge_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def forge_request(method, url, form=None, token=None, body=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=30) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise LoginError("%s %s: HTTP %d %s" % (method, url.split("?")[0], e.code,
                                                e.read()[:200].decode("utf-8", "replace")))
    except (urllib.error.URLError, OSError) as e:
        raise LoginError("cannot reach %s: %s" % (FORGE_HOST, e))


def forge_tokens(form):
    r = forge_request("POST", FORGE + "/login/oauth/access_token", dict(form, client_id=GIT_CLIENT_ID))
    if not r or "access_token" not in r:
        raise LoginError("no token from %s: %s" % (FORGE_HOST, r))
    return {"access_token": r["access_token"], "refresh_token": r.get("refresh_token"),
            "expires_at": time.time() + int(r.get("expires_in") or 3600)}


def forge_session():
    """The saved git sign-in with an access token good for at least a minute (renewed with the refresh token, which
    Forgejo replaces each time), or None."""
    state = forge_load()
    if not state:
        return None
    if state["expires_at"] - 60 < time.time():
        if not state.get("refresh_token"):
            return None
        try:
            state.update(forge_tokens({"grant_type": "refresh_token", "refresh_token": state["refresh_token"]}))
        except LoginError as e:
            print("the git.magicbean.dev sign-in could not be renewed (%s): sign in again" % e, file=sys.stderr)
            return None
        write_private(forge_path(), state)
    return state


def forge_expire():
    """The saved token was refused: the next use renews it first."""
    state = forge_load()
    if state:
        state["expires_at"] = 0
        write_private(forge_path(), state)


# --- the browser trip ----------------------------------------------------------------------------------------------

def listen(server, wanted, forge_url, expect_state, timeout):
    """Serve the redirects until each wanted part came back ("matrix": ?loginToken= on /matrix; "forge": ?code= or
    ?error= on /), sending the browser from the first to forge_url. Returns what came back."""
    found = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
            onward = None
            if query.get("loginToken"):
                found["matrix"] = query["loginToken"]   # (kept even if the tab closes before the reply below is sent)
                if "forge" in wanted:
                    onward = forge_url
            elif "code" in query or "error" in query:
                if query.get("state") == expect_state:
                    found["forge"] = query
            try:
                if onward:
                    self.send_response(302)
                    self.send_header("Location", onward)
                    self.end_headers()
                    return
                ok = bool(found) and "error" not in found.get("forge", {})
                text = "Signed in. You can close this tab." if ok else "Sign-in failed: %s" % (
                    found.get("forge", {}).get("error") or "nothing to sign in with in this request")
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(("<!doctype html><title>Longshot</title><p>%s</p>" % text).encode("utf-8"))
            except OSError:
                pass   # (the browser went away first, e.g. WinError 10053; what came in above is what matters)

        def log_message(self, *args):
            pass

    server.RequestHandlerClass = Handler
    server.timeout = 1
    deadline = time.monotonic() + timeout
    try:
        while not all(w in found for w in wanted) and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    return found


def sign_in(server=SERVER, matrix=True, forge=True, no_browser=False, out=print, chat=None):
    """One trip through the browser for the Matrix session (saved by team_chat) and the git token (saved here).
    Returns {"matrix": user ID, "forge": login} for what it signed in to."""
    if chat is None:
        import team_chat as chat   # (same folder; the module that keeps the Matrix session)
    team_chat = chat
    listener = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    port = listener.server_address[1]
    wanted = (["matrix"] if matrix else []) + (["forge"] if forge else [])
    verifier = secrets.token_urlsafe(48)
    expect = secrets.token_urlsafe(16)
    forge_redirect = "http://127.0.0.1:%d" % port   # (no path: Forgejo matches loopback redirects without the port)
    forge_url = FORGE + "/login/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": GIT_CLIENT_ID, "redirect_uri": forge_redirect, "response_type": "code", "state": expect,
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
        "code_challenge_method": "S256"})
    base = None
    if matrix:
        base = team_chat.homeserver(server)
        flows = [f.get("type") for f in team_chat.request("GET", team_chat.client(base, "/login")).get("flows", [])]
        if "m.login.sso" not in flows:
            listener.server_close()
            raise LoginError("%s offers no single sign-on (flows: %s)" % (base, ", ".join(flows)))
        url = team_chat.client(base, "/login/sso/redirect?redirectUrl=" +
                               team_chat.quote("http://127.0.0.1:%d/matrix" % port))
    else:
        url = forge_url
    out("Sign in with Discord here (the link works for %d minutes):\n\n  %s\n" % (LOGIN_TIMEOUT // 60, url))
    sys.stdout.flush()
    if not no_browser:
        webbrowser.open(url)
    found = listen(listener, wanted, forge_url, expect, LOGIN_TIMEOUT)
    if not found:
        raise LoginError("no sign-in came back within %d minutes" % (LOGIN_TIMEOUT // 60))
    done = {}
    if "matrix" in found:
        done["matrix"] = team_chat.finish_sso(server, base, found["matrix"])
    if forge:
        reply = found.get("forge")
        if not reply or "code" not in reply:
            out("git.magicbean.dev: not signed in (%s)" % ((reply or {}).get("error") or "no answer"))
        else:
            state = forge_tokens({"grant_type": "authorization_code", "code": reply["code"], "code_verifier": verifier,
                                  "redirect_uri": forge_redirect})
            state["login"] = forge_request("GET", FORGE + "/api/v1/user", token=state["access_token"])["login"]
            write_private(forge_path(), state)
            done["forge"] = state["login"]
    return done


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login", help="sign in with Discord to the team chat and git.magicbean.dev")
    s.add_argument("--no-browser", action="store_true", help="print the link only")
    sub.add_parser("whoami")
    args = p.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import team_chat
    try:
        if args.cmd == "login":
            done = sign_in(no_browser=args.no_browser)
            for what, who in sorted(done.items()):
                print("%s: signed in as %s" % ({"matrix": "team chat", "forge": FORGE_HOST}[what], who))
        else:
            chat = team_chat.load()
            print("team chat: %s" % (chat.get("user_id") or "not signed in"))
            forge = forge_session()
            print("%s: %s" % (FORGE_HOST, forge["login"] if forge else "not signed in"))
    except (LoginError, team_chat.ChatError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
