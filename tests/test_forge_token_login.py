"""forge.py login --token-stdin, game-free and offline, against a stand-in Forgejo on 127.0.0.1. A personal access
token read from standard input is checked (GET /api/v1/user) and saved as the sign-in, marked as a token, with no
refresh token and no expiry: forge_session returns it as it is, however late, without asking the token endpoint, and
forge.py's API calls send it; git's credential helper answers with it, also when a real `git credential fill` runs it;
login sets up git the same way as the Discord sign-in. A token Forgejo refuses at sign-in (401, a 403 for a missing
scope), an empty line and a line that is not one token fail with a clear message, print no token and leave the saved
sign-in and git as they were. One refused later (a 401 from the API, or git's erase) makes the next use say to make a
new one and sign in again, never "could not be renewed". whoami and logout work with both kinds. The Discord (OAuth)
sign-in is unchanged (that test passes on main too): an expired one is renewed once with its refresh token, git's erase
makes the next use renew it, a refused renewal says "could not be renewed", plain login keeps a saved sign-in and
--again signs in anew, with a token saved too. The team chat's sign-in from a token, when the team's service refuses
it, says how to sign in to the chat without Discord.

git runs in a sandbox: HOME, XDG_CONFIG_HOME and GIT_CONFIG_GLOBAL point into a temporary folder and the system config
is off, so the real global config is never read or written. The tokens are made up.

    python -I -B tests/test_forge_token_login.py
"""
import contextlib
import http.server
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "0123456789abcdef0123456789abcdef01234567"     # (made up, the shape of a Forgejo token: 40 hex digits)
OTHER = "89abcdef0123456789abcdef0123456789abcdef"     # (another one of the same account)
UNKNOWN = "fedcba9876543210fedcba9876543210fedcba98"   # (one Forgejo doesn't know: 401)
NARROW = "00112233445566778899aabbccddeeff00112233"    # (one without read:user: 403)
QUERY = "protocol=https\nhost=git.magicbean.dev\n"     # (git asks its credential helper for this host)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forge = load("forge", os.path.join(ROOT, "scripts", "forge.py"))
auth = forge.auth


class Forgejo:
    """GET /api/v1/user for the tokens it knows, POST /login/oauth/access_token renewing each refresh token once (as
    with InvalidateRefreshTokens on), and the team's POST /_magicbean/matrix-login, which refuses every token here."""

    def __init__(self):
        self.users = {TOKEN: "tokenuser", OTHER: "tokenuser", "oauth-access-1": "oauthuser"}   # (bearer -> login)
        self.narrow = {NARROW}
        self.refresh = {"rt-1": ("oauth-access-2", "rt-2")}   # (refresh token -> the next access and refresh tokens)
        self.calls, self.forms = [], []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            disable_nagle_algorithm = True   # (each reply's header and body go out at once, with no ACK wait)

            def reply(self, code, body):
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def bearer(self):
                got = self.headers.get("Authorization", "")
                return got[len("Bearer "):] if got.startswith("Bearer ") else None

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                outer.calls.append(("GET", path))
                token = self.bearer()
                if path != "/api/v1/user":
                    return self.reply(404, {"message": "not found"})
                if token in outer.narrow:
                    return self.reply(403, {"message": "token does not have at least one of required scope(s), "
                                                       "required=[read:user]"})
                if token in outer.users:
                    return self.reply(200, {"id": 7, "login": outer.users[token]})
                self.reply(401, {"message": "user does not exist"})

            def do_POST(self):
                path = urllib.parse.urlsplit(self.path).path
                outer.calls.append(("POST", path))
                data = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
                if path == "/login/oauth/access_token":
                    form = {k: v[0] for k, v in urllib.parse.parse_qs(data).items()}
                    outer.forms.append(form)
                    new = outer.refresh.pop(form.get("refresh_token", ""), None)
                    if form.get("grant_type") != "refresh_token" or form.get("client_id") != auth.GIT_CLIENT_ID \
                            or not new:
                        return self.reply(400, {"error": "unauthorized_client",
                                                "error_description": "token was already used"})
                    outer.users[new[0]] = "oauthuser"
                    return self.reply(200, {"access_token": new[0], "refresh_token": new[1], "token_type": "bearer",
                                            "expires_in": 3600})
                if path == "/_magicbean/matrix-login":
                    return self.reply(401, {"error": "not a token of the sign-in app"})
                self.reply(404, {"message": "not found"})

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def paths(self):
        return [p for _, p in self.calls]


def no_sign_in(*args, **kwargs):
    raise AssertionError("the Discord sign-in started")


def discord(no_browser=False, out=print):
    """magicbean_login.sign_in, the browser trip done: a new Discord (OAuth) sign-in saved, as browser_trip saves it"""
    auth.write_private(auth.forge_path(), {"access_token": "oauth-access-3", "refresh_token": "rt-3",
                                           "expires_at": time.time() + 3600, "login": "oauthuser"})
    return {"forge": "oauthuser", "matrix": "@oauthuser:magicbean.dev"}


class TokenLogin(unittest.TestCase):
    def setUp(self):
        self.forgejo = Forgejo()
        self.addCleanup(self.forgejo.server.server_close)
        self.addCleanup(self.forgejo.server.shutdown)
        self.tmp = tempfile.mkdtemp(prefix="forge_token_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        self.conf = os.path.join(self.tmp, "conf")
        self.gitconfig = os.path.join(self.tmp, "gitconfig")
        sandbox = mock.patch.dict(os.environ, {
            "LONGSHOT_TEAM_CHAT_DIR": self.conf, "HOME": home, "USERPROFILE": home,
            "XDG_CONFIG_HOME": os.path.join(home, ".config"), "GIT_CONFIG_GLOBAL": self.gitconfig,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "", "SSH_ASKPASS": "", "no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})
        sandbox.start()
        self.addCleanup(sandbox.stop)
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
                     "GIT_CONFIG_COUNT"):
            os.environ.pop(name, None)   # (put back by sandbox.stop)
        self.addCleanup(os.chdir, os.getcwd())
        self.fresh_repo("repo")
        # (Forgejo's API and token endpoint are the stand-in's; the host git asks about stays git.magicbean.dev)
        for target, name in ((auth, "FORGE"), (forge, "BASE")):
            patch = mock.patch.object(target, name, self.forgejo.url)
            patch.start()
            self.addCleanup(patch.stop)

    def fresh_repo(self, name):
        path = os.path.join(self.tmp, name)
        subprocess.run(["git", "init", "-q", path], check=True, capture_output=True)
        os.chdir(path)

    def forge(self, *argv, stdin="", secrets=(TOKEN, OTHER, UNKNOWN, NARROW)):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                code = forge.main(list(argv))
            except SystemExit as e:   # (argparse refused the command line)
                code = e.code
        out, err = out.getvalue(), err.getvalue()
        for secret in secrets:
            self.assertNotIn(secret, out + err, "a token printed by forge.py %s" % " ".join(argv))
        return code, out, err

    def saved(self):
        return auth.forge_load()

    def save_oauth(self, access, refresh, expires_at):
        auth.write_private(auth.forge_path(), {"access_token": access, "refresh_token": refresh,
                                               "expires_at": expires_at, "login": "oauthuser"})

    def helpers(self):
        """The credential helpers the sandbox's global git config has for git.magicbean.dev, read from the file"""
        try:
            with open(self.gitconfig, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            return []
        found, inside = [], False
        for line in lines:
            line = line.strip()
            if line.startswith("["):
                inside = line == '[credential "https://%s"]' % auth.FORGE_HOST
            elif inside and line.partition("=")[0].strip() == "helper":
                found.append(line.partition("=")[2].strip())
        return found

    def remotes(self):
        out = subprocess.run(["git", "remote", "-v"], check=True, capture_output=True, text=True).stdout
        return {line.split()[0]: line.split()[1] for line in out.splitlines()}

    # --- a personal access token ---

    def test_token_is_checked_saved_and_git_set_up(self):
        code, out, err = self.forge("login", "--token-stdin", stdin=TOKEN + "\n")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("Signed in to git.magicbean.dev as tokenuser with a personal access token"),
                        out)
        for scope in ("write:repository", "write:issue", "read:user"):
            self.assertIn(scope, lines[1])
        self.assertEqual(self.saved(), {"kind": "token", "login": "tokenuser", "access_token": TOKEN})
        if os.name != "nt":
            self.assertEqual(os.stat(auth.forge_path()).st_mode & 0o777, 0o600)
        self.assertEqual(self.forgejo.calls, [("GET", "/api/v1/user")])
        self.assertEqual(self.helpers(), ["", forge.helper_command()])
        self.assertEqual(self.remotes(), {"forge": "%s/MagicBeanDev/Longshot.git" % forge.BASE})
        token_setup = lines[2:]

        # the same git setup as the Discord sign-in's, in a repository of its own
        self.assertEqual(self.forge("logout")[0], 0)
        self.assertEqual(self.helpers(), [])
        self.fresh_repo("repo-oauth")
        self.save_oauth("oauth-access-1", "rt-1", time.time() + 3600)
        with mock.patch.object(auth, "sign_in", no_sign_in):
            code, out, err = self.forge("login")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[0], "Signed in to git.magicbean.dev as oauthuser (--again to sign in anew).")
        self.assertEqual(out.splitlines()[1:], token_setup)
        self.assertEqual(self.helpers(), ["", forge.helper_command()])
        self.assertEqual(self.remotes(), {"forge": "%s/MagicBeanDev/Longshot.git" % forge.BASE})

    def test_session_never_renews_a_token(self):
        self.assertEqual(self.forge("login", "--token-stdin", stdin=TOKEN + "\n")[0], 0)
        years_later = time.time() + 10 * 365 * 86400
        with mock.patch.object(auth.time, "time", lambda: years_later):
            state = auth.forge_session()
        self.assertEqual(state["access_token"], TOKEN)
        self.assertEqual(forge.api("GET", "/user")["login"], "tokenuser")   # (sent as the bearer)
        self.assertNotIn("/login/oauth/access_token", self.forgejo.paths())
        self.assertEqual(self.saved(), {"kind": "token", "login": "tokenuser", "access_token": TOKEN})

    def test_credential_helper_answers_with_the_token(self):
        self.assertEqual(self.forge("login", "--token-stdin", stdin=TOKEN + "\n")[0], 0)
        code, out, _ = self.forge("credential", "get", stdin=QUERY, secrets=())
        self.assertEqual((code, out), (0, "username=tokenuser\npassword=%s\n" % TOKEN))
        self.assertEqual(self.forge("credential", "get", stdin="protocol=https\nhost=example.org\n")[:2], (0, ""))
        # git itself, through the helper login put in the sandbox's global config: a process of its own, which knows the
        # real host only, so the answer needs no request
        r = subprocess.run(["git", "credential", "fill"], input=QUERY + "\n", capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("username=tokenuser\n", r.stdout)
        self.assertIn("password=%s\n" % TOKEN, r.stdout)
        self.assertNotIn("/login/oauth/access_token", self.forgejo.paths())

    def test_refused_tokens_fail_cleanly(self):
        self.save_oauth("oauth-access-1", "rt-1", time.time() + 3600)   # (a sign-in that must survive each refusal)
        before = self.saved()
        for given, says in ((UNKNOWN, "token refused"), (NARROW, "read:user"), ("", "no token on standard input"),
                            ("%s %s" % (TOKEN, OTHER), "not one token")):
            code, out, err = self.forge("login", "--token-stdin", stdin=given + "\n")
            self.assertEqual(code, 1, (given, out, err))
            self.assertIn(says, err)
            self.assertTrue(err.startswith("forge.py: "), err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(self.saved(), before)
            self.assertEqual(self.helpers(), [])
        self.assertEqual(self.forgejo.paths(), ["/api/v1/user", "/api/v1/user"])   # (the empty and the two: not sent)

    def test_token_refused_later_says_sign_in_again(self):
        self.assertEqual(self.forge("login", "--token-stdin", stdin=TOKEN + "\n")[0], 0)
        del self.forgejo.users[TOKEN]   # (deleted on the site)
        with self.assertRaises(forge.ForgeError) as refused:
            forge.api("GET", "/user")
        self.assertIn("refused the saved personal access token", str(refused.exception))
        self.assertIn("login --token-stdin", str(refused.exception))
        self.assertTrue(self.saved()["refused"])
        for argv, stdin in ((("whoami",), ""), (("credential", "get"), QUERY)):
            _, out, err = self.forge(*argv, stdin=stdin)
            self.assertIn("refused the saved personal access token", err, argv)
            self.assertIn("login --token-stdin", err, argv)
            self.assertNotIn("could not be renewed", err, argv)
            self.assertNotIn("password=", out, argv)
        self.assertEqual(self.forge("whoami")[0], 1)

        # git found a new token wrong (its erase): the same, with no request
        self.assertEqual(self.forge("login", "--token-stdin", stdin=OTHER + "\n")[0], 0)
        self.assertNotIn("refused", self.saved())
        calls = len(self.forgejo.calls)
        self.forge("credential", "erase", stdin=QUERY + "username=tokenuser\npassword=%s\n" % OTHER, secrets=())
        self.assertTrue(self.saved()["refused"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(auth.forge_session())
        self.assertIn("refused the saved personal access token", err.getvalue())
        self.assertNotIn("could not be renewed", err.getvalue())
        self.assertEqual(len(self.forgejo.calls), calls)
        self.assertNotIn("/login/oauth/access_token", self.forgejo.paths())

    def test_whoami_and_logout_with_both_kinds(self):
        self.assertEqual(self.forge("whoami")[0], 1)
        self.assertEqual(self.forge("login", "--token-stdin", stdin=TOKEN + "\n")[0], 0)
        self.assertEqual(self.forge("whoami")[:2], (0, "tokenuser (personal access token)\n"))
        code, out, _ = self.forge("logout")
        self.assertEqual(code, 0)
        self.assertIn("Signed out of git.magicbean.dev", out)
        self.assertIn("/user/settings/applications", out)   # (where to delete the token itself)
        self.assertIsNone(self.saved())
        self.assertEqual(self.helpers(), [])
        self.assertEqual(self.forge("whoami")[0], 1)

        self.save_oauth("oauth-access-1", "rt-1", time.time() + 3600)
        self.assertEqual(self.forge("whoami")[:2], (0, "oauthuser\n"))
        code, out, _ = self.forge("logout")
        self.assertEqual((code, out), (0, "Signed out of git.magicbean.dev (the team chat stays signed in: "
                                          "team_chat.py logout).\n"))
        self.assertIsNone(self.saved())

    # --- the Discord sign-in, as before ---

    def test_discord_sign_in_unchanged(self):
        self.save_oauth("oauth-access-1", "rt-1", 0)   # (run out: renewed with its refresh token)
        state = auth.forge_session()
        self.assertEqual((state["access_token"], state["refresh_token"]), ("oauth-access-2", "rt-2"))
        self.assertGreater(state["expires_at"], time.time() + 3000)
        self.assertEqual(self.saved(), state)
        self.assertEqual(self.forgejo.forms, [{"grant_type": "refresh_token", "refresh_token": "rt-1",
                                               "client_id": auth.GIT_CLIENT_ID}])
        self.assertNotIn("kind", state)

        # git found it wrong: the next use renews it, which Forgejo refuses here as after another machine's renewal
        self.forge("credential", "erase", stdin=QUERY + "username=oauthuser\npassword=oauth-access-2\n")
        self.assertEqual(self.saved()["expires_at"], 0)
        self.assertNotIn("refused", self.saved())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(auth.forge_session())
        self.assertIn("could not be renewed", err.getvalue())
        self.assertIn("token was already used", err.getvalue())

        # plain login keeps a saved sign-in; --again signs in anew
        self.save_oauth("oauth-access-1", "rt-1", time.time() + 3600)
        with mock.patch.object(auth, "sign_in", no_sign_in):
            code, out, _ = self.forge("login")
        self.assertEqual(code, 0)
        self.assertIn("Signed in to git.magicbean.dev as oauthuser (--again to sign in anew).", out)
        with mock.patch.object(auth, "sign_in", discord):
            code, out, _ = self.forge("login", "--again")
        self.assertEqual(code, 0)
        self.assertIn("Signed in as oauthuser (the team chat too: @oauthuser:magicbean.dev).", out)
        self.assertEqual(self.saved()["access_token"], "oauth-access-3")

    def test_plain_login_keeps_a_token_and_again_signs_in_with_discord(self):
        self.assertEqual(self.forge("login", "--token-stdin", stdin=TOKEN + "\n")[0], 0)
        with mock.patch.object(auth, "sign_in", no_sign_in):
            code, out, _ = self.forge("login")
        self.assertEqual(code, 0)
        self.assertIn("as tokenuser with a personal access token (--token-stdin to give a new one, --again", out)
        self.assertEqual(self.saved()["access_token"], TOKEN)
        with mock.patch.object(auth, "sign_in", discord):
            code, out, _ = self.forge("login", "--again")
        self.assertEqual(code, 0)
        self.assertIn("Signed in as oauthuser (the team chat too: @oauthuser:magicbean.dev).", out)
        self.assertEqual(self.saved()["access_token"], "oauth-access-3")
        self.assertNotIn("kind", self.saved())

    def test_chat_sign_in_refused_for_a_token_names_the_other_way(self):
        self.assertEqual(self.forge("login", "--token-stdin", stdin=TOKEN + "\n")[0], 0)
        chat = types.SimpleNamespace(homeserver=lambda server: server, finish_jwt=no_sign_in)
        with self.assertRaises(auth.LoginError) as refused:
            auth.matrix_from_forge(self.forgejo.url, chat)
        self.assertIn("/_magicbean/matrix-login: HTTP 401", str(refused.exception))
        self.assertIn("team_chat.py login --access-token-stdin", str(refused.exception))
        self.assertNotIn(TOKEN, str(refused.exception))


if __name__ == "__main__":
    unittest.main()
