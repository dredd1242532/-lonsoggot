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
import re
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
        (publish / "LongshotBuilder.dll").write_bytes(b"MZ builder library stand-in")
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
        with mock.patch.object(auth.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=VALID)) as run:
            auth.verify_file("signer", "file.dll", self.leaf, "root.pem", auth.CA_FILE)
        self.assertEqual(run.call_args.args[0][2:6], ["-CAfile", "root.pem", "-TSA-CAfile", auth.CA_FILE])
        for code, text in ((1, VALID), (2, VALID), (0, "Timestamp is not available\nSignature verification: ok"), (0, "")):
            with self.subTest(code=code, text=text), mock.patch.object(auth.subprocess, "run",
                    return_value=SimpleNamespace(returncode=code, stdout=text)), self.assertRaises(auth.SigningError):
                auth.verify_file("signer", "file.exe", self.leaf)

    def test_exact_three_files_and_stdin_request(self):
        setup, publish = self.inputs()
        files = [setup, publish / "LongshotBuilder.exe", publish / "LongshotBuilder.dll"]
        with mock.patch.dict(os.environ, {"AUTHENTICODE_TOKEN_PIN": "pin-secret"}), \
                mock.patch.object(auth.subprocess, "run", side_effect=self.signer) as run:
            auth.sign_files(files, "signer", self.cert, ["-key", "pkcs11:object=key", "-readpass", "-"],
                            ca_file="root.pem", password="pin-secret\n", tsa_ca_file=auth.CA_FILE)
        self.assertEqual(run.call_count, 6)  # sign, verify for each
        signs = [c for c in run.call_args_list if c.args[0][1] == "sign"]
        self.assertEqual([Path(c.args[0][c.args[0].index("-in") + 1]) for c in signs], [f.resolve() for f in files])
        self.assertEqual(auth.TIMESTAMPS, ("https://timestamp.digicert.com", "http://timestamp.sectigo.com"))
        for call in signs:
            args = call.args[0]
            self.assertEqual(args[args.index("-h") + 1], "sha256")
            # (every server, in order: osslsigncode tries the next when one fails, and fails when all do)
            self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "-ts"], list(auth.TIMESTAMPS))
            self.assertEqual(args[args.index("-HTTPS-CAfile") + 1], auth.CA_FILE)
            self.assertEqual(Path(args[args.index("-out") + 1]).suffix, Path(args[args.index("-in") + 1]).suffix)
            self.assertEqual(call.kwargs["input"], "pin-secret\n")
        for call in run.call_args_list:
            if call.args[0][1] == "verify":
                self.assertEqual(call.args[0][2:6], ["-CAfile", "root.pem", "-TSA-CAfile", auth.CA_FILE])
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
            auth.sign_files([setup, publish / "LongshotBuilder.exe", publish / "LongshotBuilder.dll"], "signer", self.cert, [])
        package, out = self.root / "package", self.root / "release.zip"
        with mock.patch.object(bp, "tracked", return_value=[]), mock.patch.object(bp, "state_list", return_value=0):
            self.assertEqual(bp.package(SimpleNamespace(out=str(package), publish=str(publish), setup=str(setup))), 0)
        bp.make_zip(str(package), "v3.0.0", str(setup), str(out))
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.read("LongshotBuilder.exe"), setup.read_bytes())
            self.assertEqual(z.read("LongshotBuilder/LongshotBuilder-Setup.exe"), setup.read_bytes())
            self.assertEqual(z.read("LongshotBuilder/LongshotBuilder.exe"), (publish / "LongshotBuilder.exe").read_bytes())
            self.assertEqual(z.read("LongshotBuilder/LongshotBuilder.dll"), (publish / "LongshotBuilder.dll").read_bytes())
            self.assertTrue(z.read("LongshotBuilder/LongshotBuilder.dll").endswith(b" signed stand-in"))
            self.assertEqual(z.read("LongshotBuilder/third-party.exe"), b"third-party signature unchanged")
        with mock.patch.object(auth, "verify_file") as verify:
            auth.verify_archive(out, "signer", self.leaf)
            self.assertEqual(verify.call_count, 4)
            self.assertEqual([Path(c.args[1]).suffix for c in verify.call_args_list], [".exe", ".exe", ".exe", ".dll"])
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
        self.assertIn("AUTHENTICODE_ROOT_PEM: ${{ vars.AUTHENTICODE_ROOT_PEM }}", signed)
        # (the canary for runs-on: the runner check holds no secret and comes before the step that does)
        runner = windows.split("- name: Signing runner\n", 1)[1].split("- name:", 1)[0]
        self.assertEqual(runner.split("if:", 1)[1].splitlines()[0], signed.split("if:", 1)[1].splitlines()[0])
        self.assertNotIn("secrets.", runner)
        self.assertIn("scripts/authenticode.py runner", runner)
        self.assertLess(windows.index("dotnet publish builder/"), windows.index("- name: Signing runner"))
        self.assertLess(windows.index("- name: Signing runner"), windows.index("- name: Authenticode (optional)"))
        self.assertIn("--disable-build-servers", windows.split("dotnet publish builder/", 1)[1].split("- name:", 1)[0])
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
        self.assertIn("AUTHENTICODE_ROOT_PEM: ${{ vars.AUTHENTICODE_ROOT_PEM }}", packaged)
        self.assertIn("authenticode: ${{ needs.version.outputs.authenticode == 'true' }}", release)
        self.assertIn("needs: [version, linux, windows]", release)
        self.assertNotIn("secrets: inherit", release)
        plan = release.split("- name: Windows Authenticode configuration\n", 1)[1].split("\n  linux:", 1)[0]
        self.assertIn("if: steps.name.outputs.tag != ''", plan)  # a superseded nightly does not inspect signing configuration
        self.assertEqual(release.count("checked: ${{ needs.version.outputs.checked }}"), 2)
        self.assertEqual(release.count("if: needs.version.outputs.tag != ''"), 3)
        package = windows.split("\n  package:", 1)[1].split("\n  vulkan:", 1)[0]
        self.assertNotIn("RELEASE_CHECKED", package)  # release files are rebuilt and signed even if the old build check passed
        self.assertIn("tools.lock's dav1d", package)
        self.assertLess(package.index("Check packaged Authenticode"), package.index("- id: upload_1"))
        self.assertIn("if: steps.upload_1.outcome == 'failure'", package)
        self.assertIn("linux-signing", (ROOT / ".forgejo/actionlint.yaml").read_text(encoding="utf-8"))

    def test_forgejo_call_keeps_supported_keys_and_only_named_secrets(self):
        windows = (ROOT / ".forgejo/workflows/build-windows.yml").read_text(encoding="utf-8")
        release = (ROOT / ".forgejo/workflows/release.yml").read_text(encoding="utf-8")
        call = windows.split("\n  workflow_call:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertLessEqual(set(re.findall(r"^    (\w+):", call, re.M)), {"inputs", "outputs"},
                             "Forgejo 17 rejects declarations under on.workflow_call.secrets")
        caller = release.split("\n  windows:\n", 1)[1].split("\n  publish:\n", 1)[0]
        forwarded = caller.split("\n    secrets:\n", 1)[1]
        names = ("AUTHENTICODE_CERTIFICATE_PEM", "AUTHENTICODE_KEY_URI", "AUTHENTICODE_TOKEN_PIN")
        self.assertEqual(re.findall(r"^      (\w+): (.+)$", forwarded, re.M),
                         [(name, "${{ secrets." + name + " }}") for name in names])
        self.assertNotIn("secrets: inherit", release)

    def test_pinned_root_or_system_bundle_and_never_a_silent_fallback(self):
        with auth.trust_anchor({}) as ca_file:
            self.assertEqual(ca_file, auth.CA_FILE)
        self.assertIn("system bundle", self.stdout.getvalue())
        with auth.trust_anchor({auth.ROOT_CERTIFICATE: CERT}) as ca_file:
            self.assertNotEqual(ca_file, auth.CA_FILE)
            self.assertEqual(Path(ca_file).read_text(encoding="ascii"), CERT)
        self.assertFalse(Path(ca_file).exists())
        self.assertIn(auth.ROOT_CERTIFICATE + ", first certificate SHA-256 " + self.leaf, self.stdout.getvalue())
        for value in ("not a certificate", "-----BEGIN PRIVATE KEY-----\nsecret", CERT + "secret"):
            with self.subTest(value=value), self.assertRaises(auth.SigningError):
                with auth.trust_anchor({auth.ROOT_CERTIFICATE: value}):
                    self.fail("a malformed root must not be used, nor the system bundle in its place")
        self.assertNotIn("secret", self.stdout.getvalue())

    def test_sign_builder_signs_the_setup_apphost_and_library_against_the_pinned_root(self):
        setup, publish = self.inputs()
        provider, module = self.root / "provider.so", self.root / "module.so"
        provider.touch()
        module.touch()
        env = dict(ENV, AUTHENTICODE_KEY_URI="pkcs11:object=key", AUTHENTICODE_PKCS11_PROVIDER=str(provider),
                   AUTHENTICODE_PKCS11_MODULE=str(module), AUTHENTICODE_TOKEN_PIN="pin-secret",
                   **{auth.ROOT_CERTIFICATE: CERT})
        with mock.patch.object(auth, "pinned_tool", return_value=contextlib.nullcontext("signer")), \
                mock.patch.object(auth, "sign_files") as sign:
            auth.sign_builder(setup, publish, CERT, env, self.root / "cache")
        files = sign.call_args.args[0]
        self.assertEqual(files, [setup, publish / "LongshotBuilder.exe", publish / "LongshotBuilder.dll"])
        self.assertNotEqual(sign.call_args.kwargs["ca_file"], auth.CA_FILE)
        self.assertEqual(sign.call_args.kwargs["tsa_ca_file"], auth.CA_FILE)
        self.assertEqual(sign.call_args.kwargs["password"], "pin-secret\n")
        self.assertEqual(auth.ARCHIVE_FILES[3], "LongshotBuilder/LongshotBuilder.dll")
        # (off the signing runner the PKCS#11 check stops it before the PIN is read or a tool is fetched)
        with mock.patch.object(auth, "pinned_tool") as tool, mock.patch.object(auth, "sign_files") as sign, \
                self.assertRaises(auth.SigningError):
            auth.sign_builder(setup, publish, CERT, dict(env, AUTHENTICODE_PKCS11_MODULE="/absent/module.so"), self.root)
        tool.assert_not_called()
        sign.assert_not_called()

    def test_runner_check_stops_off_the_signing_runner_without_secrets(self):
        provider, module = self.root / "provider.so", self.root / "module.so"
        provider.touch()
        module.touch()
        for env in ({}, {"AUTHENTICODE_PKCS11_PROVIDER": str(provider)},
                    {"AUTHENTICODE_PKCS11_PROVIDER": str(provider), "AUTHENTICODE_PKCS11_MODULE": "module.so"},
                    {"AUTHENTICODE_PKCS11_PROVIDER": str(provider), "AUTHENTICODE_PKCS11_MODULE": str(self.root / "absent.so")}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(auth.main(["runner"]), 1)
        self.assertIn("not on the signing runner", self.stderr.getvalue())
        with mock.patch.dict(os.environ, {"AUTHENTICODE_PKCS11_PROVIDER": str(provider),
                                          "AUTHENTICODE_PKCS11_MODULE": str(module)}, clear=True), \
                mock.patch.object(auth, "certificate_for") as certificate:
            self.assertEqual(auth.main(["runner"]), 0)
            certificate.assert_not_called()

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
