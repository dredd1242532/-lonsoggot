"""Optional Authenticode decisions, commands, failure handling and package ordering; no key or network.

    python -I -B tests/test_authenticode.py

Windows' actual certificate store, provider and trust checks need a Windows release-runner test.
These tests use a stand-in subprocess result, not a Windows-trusted publisher certificate.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


auth = load("authenticode")
bp = load("builder_package")
CERT = "1234567890ABCDEF1234567890ABCDEF12345678"
ENV = {"GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "MagicBeanDev/Longshot", "GITHUB_REF": "refs/heads/main",
       "AUTHENTICODE_CERTIFICATE_SHA1": CERT, "SystemRoot": "C:/Windows"}
VALID = {"status": "Valid", "signature_type": "Authenticode", "signer": CERT, "timestamped": True}


class Authenticode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="authenticode-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(self.stdout))
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))
        self.stack.enter_context(mock.patch.dict(os.environ, ENV, clear=True))

    def inputs(self):
        setup = self.root / "LongshotBuilder-Setup.exe"
        setup.write_bytes(b"MZ setup stand-in")
        publish = self.root / "publish with spaces"
        publish.mkdir()
        (publish / "LongshotBuilder.exe").write_bytes(b"MZ builder stand-in")
        (publish / "third-party.exe").write_bytes(b"third-party signature unchanged")
        return setup, publish

    def test_no_certificate_is_success_without_files_or_signing_tool(self):
        for value in (None, ""):
            with self.subTest(value=value), mock.patch.dict(os.environ, clear=True):
                os.environ.update({k: v for k, v in ENV.items() if k not in (auth.CERTIFICATE, "SystemRoot")})
                if value is not None:
                    os.environ[auth.CERTIFICATE] = value
                with mock.patch.object(auth.subprocess, "run") as run:
                    self.assertEqual(auth.main(["sign", "--setup", "absent.exe", "--publish", "absent"]), 0)
                run.assert_not_called()
        self.assertEqual(self.stdout.getvalue().count("not signed: no certificate configured"), 2)
        self.assertNotIn("signed and verified", self.stdout.getvalue())

    def test_only_this_repositorys_main_and_version_pushes_can_sign(self):
        for ref in ("refs/heads/main", "refs/tags/v3.0.0", "refs/tags/v3.0.0-rc.2"):
            with self.subTest(ref=ref):
                self.assertEqual(auth.certificate_for(dict(ENV, GITHUB_REF=ref)), CERT)
        for change in ({"GITHUB_EVENT_NAME": "pull_request"}, {"GITHUB_EVENT_NAME": "pull_request_target"},
                       {"GITHUB_EVENT_NAME": "workflow_dispatch"}, {"GITHUB_EVENT_NAME": ""},
                       {"GITHUB_REPOSITORY": "someone/Longshot"}, {"GITHUB_REPOSITORY": ""},
                       {"GITHUB_REF": "refs/heads/topic"}, {"GITHUB_REF": "refs/pull/1/head"},
                       {"GITHUB_REF": "refs/tags/tools"}, {"GITHUB_REF": "refs/tags/v3.bad"}):
            with self.subTest(change=change), mock.patch.dict(os.environ, dict(ENV, **change), clear=True), \
                    mock.patch.object(auth.subprocess, "run") as run:
                self.assertEqual(auth.main(["sign", "--setup", "absent.exe", "--publish", "absent"]), 0)
                run.assert_not_called()
        self.assertNotIn(CERT, self.stdout.getvalue())

    def test_bad_configuration_fails_without_echoing_it(self):
        for value in (" ", "x" * 40, CERT + "\n", "secret; Write-Host unsafe", "private key bytes"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {auth.CERTIFICATE: value}):
                with mock.patch.object(auth.subprocess, "run") as run:
                    self.assertEqual(auth.main(["sign", "--setup", "absent.exe", "--publish", "absent"]), 1)
                run.assert_not_called()
                if value.strip():
                    self.assertNotIn(value, self.stdout.getvalue() + self.stderr.getvalue())
        self.assertEqual(auth.certificate_for(dict(ENV, **{auth.CERTIFICATE: CERT.lower()})), CERT)

    def test_plan_exposes_only_a_boolean(self):
        output = self.root / "output"
        os.environ["GITHUB_OUTPUT"] = str(output)
        self.assertEqual(auth.main(["plan"]), 0)
        del os.environ[auth.CERTIFICATE]
        self.assertEqual(auth.main(["plan"]), 0)
        os.environ.update(ENV, GITHUB_EVENT_NAME="pull_request")
        self.assertEqual(auth.main(["plan"]), 0)
        self.assertEqual(output.read_text(), "enabled=true\nenabled=false\nenabled=false\n")
        self.assertNotIn(CERT, self.stdout.getvalue())

    def test_command_uses_os_powershell_and_no_shell_or_secret_interpolation(self):
        args = auth.command(ENV)
        self.assertEqual(Path(args[0]), Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
        self.assertEqual(args[1:5], ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertEqual(args[5], auth.POWERSHELL)
        self.assertNotIn(CERT, " ".join(args))
        self.assertIn("-LiteralPath $request.file", auth.POWERSHELL)
        self.assertIn("-HashAlgorithm SHA256 -IncludeChain NotRoot -TimestampServer $request.timestamp", auth.POWERSHELL)
        self.assertLess(auth.POWERSHELL.index("Set-AuthenticodeSignature"), auth.POWERSHELL.index("Get-AuthenticodeSignature"))
        self.assertNotIn("Import-Certificate", auth.POWERSHELL)
        self.assertNotIn("$_.", auth.POWERSHELL)
        with self.assertRaises(auth.SigningError):
            auth.command({})

    def test_verification_requires_trust_embedded_signature_pinned_signer_and_timestamp(self):
        auth.verify_result(json.dumps(VALID), CERT)
        for result in (dict(VALID, status="NotSigned"), dict(VALID, status="NotTrusted"),
                       dict(VALID, status="HashMismatch"), dict(VALID, signature_type="Catalog"),
                       dict(VALID, signer="0" * 40), dict(VALID, timestamped=False),
                       dict(VALID, timestamped="True"), {}, [], None):
            with self.subTest(result=result), self.assertRaises(auth.SigningError):
                auth.verify_result(json.dumps(result), CERT)
        for result in ("", "provider error: secret data", "{}\n{}"):
            with self.subTest(result=result), self.assertRaises(auth.SigningError) as caught:
                auth.verify_result(result, CERT)
            self.assertNotIn("secret data", str(caught.exception))

    def test_exact_two_files_and_stdin_request(self):
        setup, publish = self.inputs()
        response = SimpleNamespace(returncode=0, stdout=json.dumps(VALID))
        with mock.patch.object(auth.subprocess, "run", return_value=response) as run:
            self.assertEqual(auth.main(["sign", "--setup", str(setup), "--publish", str(publish)]), 0)
        self.assertEqual(run.call_count, 2)
        requests = [json.loads(c.kwargs["input"]) for c in run.call_args_list]
        self.assertEqual([r["file"] for r in requests], [str(setup.resolve()), str((publish / "LongshotBuilder.exe").resolve())])
        for request in requests:
            self.assertEqual(request["certificate"], CERT)
            self.assertEqual(request["timestamp"], "http://timestamp.digicert.com")
        for call in run.call_args_list:
            self.assertNotIn(CERT, " ".join(call.args[0]))
            self.assertFalse(call.kwargs.get("shell", False))
            self.assertTrue(call.kwargs["capture_output"])
            self.assertEqual(call.kwargs["timeout"], 120)
        self.assertIn("signed and verified", self.stdout.getvalue())
        self.assertNotIn(CERT, self.stdout.getvalue())

    def test_missing_named_file_fails_before_any_signature(self):
        setup, publish = self.inputs()
        (publish / "LongshotBuilder.exe").unlink()
        with mock.patch.object(auth.subprocess, "run") as run:
            self.assertEqual(auth.main(["sign", "--setup", str(setup), "--publish", str(publish)]), 1)
        run.assert_not_called()

    def test_child_failure_and_timeout_never_expose_provider_output(self):
        setup, publish = self.inputs()
        args = ["sign", "--setup", str(setup), "--publish", str(publish)]
        for response in (SimpleNamespace(returncode=1, stdout="private material", stderr="private material"),
                         SimpleNamespace(returncode=2, stdout="private material", stderr="private material")):
            with self.subTest(code=response.returncode), mock.patch.object(auth.subprocess, "run", return_value=response) as run:
                self.assertEqual(auth.main(args), 1)
                self.assertEqual(run.call_count, 1)
        for error in (OSError("private material"), subprocess.TimeoutExpired(["private material"], 120, output="private material")):
            with self.subTest(error=type(error)), mock.patch.object(auth.subprocess, "run", side_effect=error):
                self.assertEqual(auth.main(args), 1)
        self.assertNotIn("private material", self.stdout.getvalue() + self.stderr.getvalue())
        self.assertNotIn("signed and verified", self.stdout.getvalue())

    def test_failed_post_sign_verification_stops_the_job(self):
        setup, publish = self.inputs()
        responses = [SimpleNamespace(returncode=0, stdout=json.dumps(VALID)),
                     SimpleNamespace(returncode=0, stdout=json.dumps(dict(VALID, timestamped=False)))]
        with mock.patch.object(auth.subprocess, "run", side_effect=responses):
            self.assertEqual(auth.main(["sign", "--setup", str(setup), "--publish", str(publish)]), 1)
        self.assertNotIn("signed and verified", self.stdout.getvalue())

    def test_package_preserves_signed_bytes_in_both_setup_copies(self):
        setup, publish = self.inputs()

        def signer(args, **kwargs):
            file = Path(json.loads(kwargs["input"])["file"])
            file.write_bytes(file.read_bytes() + b" signed stand-in")
            return SimpleNamespace(returncode=0, stdout=json.dumps(VALID))

        with mock.patch.object(auth.subprocess, "run", side_effect=signer):
            self.assertEqual(auth.main(["sign", "--setup", str(setup), "--publish", str(publish)]), 0)
        package, out = self.root / "package", self.root / "release.zip"
        with mock.patch.object(bp, "tracked", return_value=[]), mock.patch.object(bp, "state_list", return_value=0):
            self.assertEqual(bp.package(SimpleNamespace(out=str(package), publish=str(publish), setup=str(setup))), 0)
        bp.make_zip(str(package), "v3.0.0", str(setup), str(out))
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.read("LongshotBuilder.exe"), setup.read_bytes())
            self.assertEqual(z.read("LongshotBuilder/LongshotBuilder-Setup.exe"), setup.read_bytes())
            self.assertEqual(z.read("LongshotBuilder/LongshotBuilder.exe"), (publish / "LongshotBuilder.exe").read_bytes())
            self.assertEqual(z.read("LongshotBuilder/third-party.exe"), b"third-party signature unchanged")

    def test_release_workflow_guards_and_signing_before_packaging(self):
        windows = (ROOT / ".forgejo/workflows/build-windows.yml").read_text(encoding="utf-8")
        release = (ROOT / ".forgejo/workflows/release.yml").read_text(encoding="utf-8")
        signed = windows.split("- name: Authenticode (optional)\n", 1)[1].split("- name:", 1)[0]
        for gate in ("inputs.authenticode", "github.event_name == 'push'", "github.repository == 'MagicBeanDev/Longshot'",
                     "inputs.ref == github.sha", "github.ref == 'refs/heads/main'"):
            self.assertIn(gate, signed)
            self.assertIn(gate, windows.split("runs-on:", 1)[1].splitlines()[0])
        self.assertIn("AUTHENTICODE_CERTIFICATE_SHA1: ${{ secrets.AUTHENTICODE_CERTIFICATE_SHA1 }}", signed)
        unsigned = windows.split("- name: Authenticode not configured\n", 1)[1].split("- name:", 1)[0]
        self.assertNotIn("secrets.", unsigned)
        self.assertIn("!inputs.authenticode", unsigned)
        self.assertLess(windows.index("dotnet publish builder/"), windows.index("- name: Authenticode (optional)"))
        self.assertLess(windows.index("- name: Authenticode (optional)"), windows.index("- name: Package folder"))
        self.assertLess(windows.index("- name: Package folder"), windows.index("- name: The zip"))
        self.assertIn("authenticode: ${{ needs.version.outputs.authenticode == 'true' }}", release)
        self.assertIn("needs: [version, linux, windows]", release)
        self.assertIn("python3 -I scripts/release.py sums files/*", release)
        self.assertNotIn("secrets: inherit", release)


if __name__ == "__main__":
    unittest.main()
