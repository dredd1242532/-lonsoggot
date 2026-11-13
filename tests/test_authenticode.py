"""Optional Linux Authenticode decisions, pinned tools, commands, failures and packaging; no network/key.

    python -I -B tests/test_authenticode.py

The explicit authenticode_standin.py exercises the real signer with throwaway keys/TSA.
These unit tests do not claim token/service access or Windows trust.
"""
import base64
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
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


auth, bp, checker = (load(n) for n in ("authenticode", "builder_package", "check_windows_files"))
DER = b"\x30authored certificate encoding stand-in"
CERT = "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(DER).decode() + "\n-----END CERTIFICATE-----\n"
SHA = "1" * 40
ENV = {"GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "MagicBeanDev/Longshot", "GITHUB_REF": "refs/heads/main",
       "GITHUB_SHA": SHA, "AUTHENTICODE_BUILD_REF": SHA, "AUTHENTICODE_CERTIFICATE_PEM": CERT}
VALID = "Timestamp Server Signature verification: ok\nSignature verification: ok\n"


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
        self.cert = self.root / "chain.pem"
        self.cert.write_text(CERT)
        self.leaf = hashlib.sha256(DER).hexdigest()
        self.cli = ["sign", "--setup", "absent.exe", "--publish", "absent", "--cache", str(self.root / "cache")]

    def inputs(self):
        setup = self.root / "LongshotBuilder-Setup.exe"
        setup.write_bytes(b"MZ setup stand-in")
        publish = self.root / "publish with spaces"
        publish.mkdir()
        (publish / "LongshotBuilder.exe").write_bytes(b"MZ builder stand-in")
        (publish / "third-party.exe").write_bytes(b"third-party signature unchanged")
        return setup, publish

    def signer(self, args, **kwargs):
        if args[1] == "sign":
            original, signed = Path(args[args.index("-in") + 1]), Path(args[args.index("-out") + 1])
            signed.write_bytes(original.read_bytes() + b" signed stand-in")
        return SimpleNamespace(returncode=0, stdout=VALID, stderr="")

    def test_no_certificate_is_success_without_files_or_signing_tool(self):
        for value in (None, ""):
            with self.subTest(value=value), mock.patch.dict(os.environ, clear=True):
                os.environ.update({k: v for k, v in ENV.items() if k != auth.CERTIFICATE})
                if value is not None:
                    os.environ[auth.CERTIFICATE] = value
                with mock.patch.object(auth, "pinned_tool") as tool, mock.patch.object(auth.subprocess, "run") as run:
                    self.assertEqual(auth.main(self.cli), 0)
                tool.assert_not_called()
                run.assert_not_called()
        self.assertEqual(self.stdout.getvalue().count("not signed: no certificate configured"), 2)
        self.assertNotIn("signed and verified", self.stdout.getvalue())

    def test_only_this_repositorys_matching_main_and_version_pushes_can_sign(self):
        for ref in ("refs/heads/main", "refs/tags/v3.0.0", "refs/tags/v3.0.0-rc.2"):
            self.assertEqual(auth.certificate_for(dict(ENV, GITHUB_REF=ref)), CERT)
        for change in ({"GITHUB_EVENT_NAME": "pull_request"}, {"GITHUB_EVENT_NAME": "pull_request_target"},
                       {"GITHUB_EVENT_NAME": "workflow_dispatch"}, {"GITHUB_EVENT_NAME": ""},
                       {"GITHUB_REPOSITORY": "someone/Longshot"}, {"GITHUB_REPOSITORY": ""},
                       {"GITHUB_REF": "refs/heads/topic"}, {"GITHUB_REF": "refs/pull/1/head"},
                       {"GITHUB_REF": "refs/tags/tools"}, {"GITHUB_REF": "refs/tags/v3.bad"},
                       {"AUTHENTICODE_BUILD_REF": "2" * 40}, {"GITHUB_SHA": ""}):
            with self.subTest(change=change), mock.patch.dict(os.environ, dict(ENV, **change), clear=True), \
                    mock.patch.object(auth, "pinned_tool") as tool:
                self.assertEqual(auth.main(self.cli), 0)
                tool.assert_not_called()
        self.assertNotIn(CERT, self.stdout.getvalue())

    def test_bad_configuration_fails_without_echoing_it(self):
        for value in (" ", "secret private key bytes", "-----BEGIN PRIVATE KEY-----\nsecret", CERT + "secret",
                      CERT.replace(base64.b64encode(DER).decode(), "=")):
            with self.subTest(value=value), mock.patch.dict(os.environ, {auth.CERTIFICATE: value}), \
                    mock.patch.object(auth, "pinned_tool") as tool:
                self.assertEqual(auth.main(self.cli), 1)
                tool.assert_not_called()
        self.assertNotIn("secret", self.stdout.getvalue() + self.stderr.getvalue())
        self.assertEqual(auth.fingerprint(CERT + CERT), self.leaf)

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

    def test_command_uses_pkcs11_and_stdin_not_exportable_keys_or_password_arguments(self):
        provider, module = self.root / "provider.so", self.root / "module.so"
        provider.touch()
        module.touch()
        env = dict(ENV, AUTHENTICODE_KEY_URI="pkcs11:token=release;object=key;type=private",
                   AUTHENTICODE_PKCS11_PROVIDER=str(provider), AUTHENTICODE_PKCS11_MODULE=str(module))
        args = auth.key_arguments(env)
        self.assertEqual(args, ["-provider", str(provider), "-pkcs11module", str(module), "-key",
                                env["AUTHENTICODE_KEY_URI"], "-readpass", "-"])
        for uri in ("key.pem", "pkcs11:object=key?pin-value=secret", "pkcs11:object=key;pin-source=file",
                    "pkcs11:object=key;%70in-value=secret", "pkcs11:object=key\nsecret"):
            with self.subTest(uri=uri), self.assertRaises(auth.SigningError):
                auth.key_arguments(dict(env, AUTHENTICODE_KEY_URI=uri))
        with self.assertRaises(auth.SigningError):
            auth.key_arguments(dict(env, AUTHENTICODE_PKCS11_PROVIDER="absent.so"))

    def test_verification_requires_trust_embedded_signature_pinned_signer_and_timestamp(self):
        with mock.patch.object(auth.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=VALID)) as run:
            auth.verify_file("signer", "file.exe", self.leaf)
        args = run.call_args.args[0]
        self.assertEqual(args, ["signer", "verify", "-CAfile", auth.CA_FILE, "-TSA-CAfile", auth.CA_FILE,
                                "-require-leaf-hash", "sha256:" + self.leaf, "-in", "file.exe"])
        self.assertNotIn("-catalog", args)
        for code, text in ((1, VALID), (2, VALID), (0, "Timestamp is not available\nSignature verification: ok"), (0, "")):
            with self.subTest(code=code, text=text), mock.patch.object(auth.subprocess, "run",
                    return_value=SimpleNamespace(returncode=code, stdout=text)), self.assertRaises(auth.SigningError):
                auth.verify_file("signer", "file.exe", self.leaf)

    def test_exact_two_files_and_stdin_request(self):
        setup, publish = self.inputs()
        with mock.patch.dict(os.environ, {"AUTHENTICODE_TOKEN_PIN": "pin-secret"}), \
                mock.patch.object(auth.subprocess, "run", side_effect=self.signer) as run:
            auth.sign_files([setup, publish / "LongshotBuilder.exe"], "signer", self.cert,
                            ["-key", "pkcs11:object=key", "-readpass", "-"], password="pin-secret\n")
        self.assertEqual(run.call_count, 4)  # sign, verify, sign, verify
        signs = [c for c in run.call_args_list if c.args[0][1] == "sign"]
        self.assertEqual([Path(c.args[0][c.args[0].index("-in") + 1]) for c in signs], [setup.resolve(), (publish / "LongshotBuilder.exe").resolve()])
        for call in signs:
            args = call.args[0]
            self.assertEqual(args[args.index("-h") + 1], "sha256")
            self.assertEqual(args[args.index("-ts") + 1], auth.TIMESTAMP)
            self.assertEqual(call.kwargs["input"], "pin-secret\n")
        for call in run.call_args_list:
            self.assertNotIn("pin-secret", " ".join(call.args[0]))
            self.assertNotIn("AUTHENTICODE_TOKEN_PIN", call.kwargs["env"])
            self.assertFalse(call.kwargs.get("shell", False))
            self.assertTrue(call.kwargs["capture_output"])
            self.assertEqual(call.kwargs["timeout"], 120)
        self.assertEqual((publish / "third-party.exe").read_bytes(), b"third-party signature unchanged")

    def test_missing_named_file_fails_before_any_signature(self):
        setup, publish = self.inputs()
        (publish / "LongshotBuilder.exe").unlink()
        with mock.patch.object(auth.subprocess, "run") as run, self.assertRaises(auth.SigningError):
            auth.sign_files([setup, publish / "LongshotBuilder.exe"], "signer", self.cert, [])
        run.assert_not_called()
        for files in ([], [setup, setup]):
            with self.subTest(files=files), mock.patch.object(auth.subprocess, "run") as run, self.assertRaises(auth.SigningError):
                auth.sign_files(files, "signer", self.cert, [])
            run.assert_not_called()

    def test_child_failure_and_timeout_never_expose_provider_output(self):
        for response in (SimpleNamespace(returncode=1, stdout="private material", stderr="private material"),
                         SimpleNamespace(returncode=2, stdout="private material", stderr="private material")):
            with mock.patch.object(auth.subprocess, "run", return_value=response), self.assertRaises(auth.SigningError) as caught:
                auth.run_tool(["signer", "verify"])
            self.assertNotIn("private material", str(caught.exception))
        for error in (OSError("private material"), subprocess.TimeoutExpired(["private material"], 120, output="private material")):
            with mock.patch.object(auth.subprocess, "run", side_effect=error), self.assertRaises(auth.SigningError) as caught:
                auth.run_tool(["signer", "verify"])
            self.assertNotIn("private material", str(caught.exception))

    def test_failed_post_sign_verification_keeps_originals_and_stops(self):
        setup, publish = self.inputs()
        count = 0

        def failing(args, **kwargs):
            nonlocal count
            count += 1
            result = self.signer(args, **kwargs)
            if count == 4:
                result.stdout = "Timestamp is not available"
            return result

        with mock.patch.object(auth.subprocess, "run", side_effect=failing), self.assertRaises(auth.SigningError):
            auth.sign_files([setup, publish / "LongshotBuilder.exe"], "signer", self.cert, [])
        self.assertEqual(setup.read_bytes(), b"MZ setup stand-in")
        self.assertEqual((publish / "LongshotBuilder.exe").read_bytes(), b"MZ builder stand-in")
        self.assertNotIn("signed and verified", self.stdout.getvalue())

    def test_package_preserves_signed_bytes_in_both_setup_copies(self):
        setup, publish = self.inputs()
        with mock.patch.object(auth.subprocess, "run", side_effect=self.signer):
            auth.sign_files([setup, publish / "LongshotBuilder.exe"], "signer", self.cert, [])
        package, out = self.root / "package", self.root / "release.zip"
        with mock.patch.object(bp, "tracked", return_value=[]), mock.patch.object(bp, "state_list", return_value=0):
            self.assertEqual(bp.package(SimpleNamespace(out=str(package), publish=str(publish), setup=str(setup))), 0)
        bp.make_zip(str(package), "v3.0.0", str(setup), str(out))
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.read("LongshotBuilder.exe"), setup.read_bytes())
            self.assertEqual(z.read("LongshotBuilder/LongshotBuilder-Setup.exe"), setup.read_bytes())
            self.assertEqual(z.read("LongshotBuilder/LongshotBuilder.exe"), (publish / "LongshotBuilder.exe").read_bytes())
            self.assertEqual(z.read("LongshotBuilder/third-party.exe"), b"third-party signature unchanged")
        with mock.patch.object(auth, "verify_file") as verify:
            auth.verify_archive(out, "signer", self.leaf)
            self.assertEqual(verify.call_count, 3)
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("LongshotBuilder.exe", b"MZ")
        with mock.patch.object(auth, "verify_file"), self.assertRaises(auth.SigningError):
            auth.verify_archive(out, "signer", self.leaf)

    def test_release_workflow_guards_and_signing_before_packaging(self):
        windows = (ROOT / ".forgejo/workflows/build-windows.yml").read_text(encoding="utf-8")
        release = (ROOT / ".forgejo/workflows/release.yml").read_text(encoding="utf-8")
        signed = windows.split("- name: Authenticode (optional)\n", 1)[1].split("- name:", 1)[0]
        for gate in ("inputs.authenticode", "github.event_name == 'push'", "github.repository == 'MagicBeanDev/Longshot'",
                     "inputs.ref == github.sha", "github.ref == 'refs/heads/main'"):
            self.assertIn(gate, signed)
            self.assertIn(gate, windows.split("runs-on:", 1)[1].splitlines()[0])
        self.assertIn("'linux-signing' || 'ubuntu-24.04'", windows)
        self.assertIn("AUTHENTICODE_CERTIFICATE_PEM: ${{ secrets.AUTHENTICODE_CERTIFICATE_PEM }}", signed)
        unsigned = windows.split("- name: Authenticode not configured\n", 1)[1].split("- name:", 1)[0]
        self.assertNotIn("secrets.", unsigned)
        self.assertIn("!inputs.authenticode", unsigned)
        self.assertLess(windows.index("dotnet publish builder/"), windows.index("- name: Authenticode (optional)"))
        self.assertLess(windows.index("- name: Authenticode (optional)"), windows.index("- name: Package folder"))
        self.assertLess(windows.index("- name: Package folder"), windows.index("- name: The zip"))
        self.assertLess(windows.index("- name: The zip"), windows.index("- name: Check packaged Authenticode"))
        packaged = windows.split("- name: Check packaged Authenticode\n", 1)[1].split("- name:", 1)[0]
        self.assertNotIn("AUTHENTICODE_TOKEN_PIN", packaged)
        self.assertNotIn("AUTHENTICODE_KEY_URI", packaged)
        self.assertIn("check_windows_files.py --authenticode", packaged)
        self.assertIn("authenticode: ${{ needs.version.outputs.authenticode == 'true' }}", release)
        self.assertIn("needs: [version, linux, windows]", release)
        self.assertNotIn("secrets: inherit", release)

    def test_checked_tool_archive_is_freshly_extracted(self):
        self.assertEqual(auth.OSSLSIGNCODE[:4], ("2.15", "osslsigncode-2.15-linux-amd64",
            "5d2890c13e630f21b3a104f0c7482bb2744cdf501237233796f6f9c67d526482", 2335349))
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as tf:
            entry = tarfile.TarInfo("fixture/osslsigncode")
            entry.size, entry.mode = 4, 0o755
            tf.addfile(entry, io.BytesIO(b"tool"))
        archive = data.getvalue()
        sha = hashlib.sha256(archive).hexdigest()
        cache = self.root / "cache"
        cache.mkdir()
        (cache / (sha + ".zip")).write_bytes(archive)
        pin = ("test", "fixture", sha, len(archive), "https://example.invalid/tool.tar.gz")
        with mock.patch.object(auth, "OSSLSIGNCODE", pin):
            with auth.pinned_tool(cache) as exe:
                self.assertEqual(Path(exe).read_bytes(), b"tool")
                Path(exe).write_bytes(b"modified")
                first = Path(exe)
            self.assertFalse(first.exists())
            with auth.pinned_tool(cache) as exe:
                self.assertEqual(Path(exe).read_bytes(), b"tool")

    def test_windows_file_checker_routes_optional_signature_check(self):
        checker.problems.clear()
        with mock.patch.object(checker, "authenticode") as verify:
            self.assertEqual(checker.main(["builder.zip", "--authenticode", "--signer-cache", "cache"]), 0)
            verify.assert_called_once()
        checker.problems.clear()
        with mock.patch.object(checker, "authenticode", side_effect=lambda a: checker.bad("signature rejected")):
            self.assertEqual(checker.main(["builder.zip", "--authenticode", "--signer-cache", "cache"]), 1)
        checker.problems.clear()


if __name__ == "__main__":
    unittest.main()
