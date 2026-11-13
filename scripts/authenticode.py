"""Optional Authenticode on the Linux runner, before packaging (docs/releases.md).

    python -I scripts/authenticode.py plan
        writes enabled=true/false to GITHUB_OUTPUT
    python -I scripts/authenticode.py sign --setup <exe> --publish <folder> --cache <folder>
        signs only the setup and WPF apphost with the configured PKCS#11 key, then verifies

The public certificate chain is AUTHENTICODE_CERTIFICATE_PEM (leaf first). The private
key stays in a token/service selected by AUTHENTICODE_KEY_URI; the optional token PIN
is sent on stdin, never in argv or logs. No PEM/PFX private-key route is exposed by CI.
"""
import argparse
import base64
import contextlib
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "MagicBeanDev/Longshot"
CERTIFICATE = "AUTHENTICODE_CERTIFICATE_PEM"
TIMESTAMP = "https://timestamp.digicert.com"
CA_FILE = "/etc/ssl/certs/ca-certificates.crt"
# Same convention as builder_package.LINUX_LLVM_MINGW: version, prefix, SHA256, size, URL;
# tools.fetch checks the archive. This CI-only tool does not belong in players' tools.lock.
OSSLSIGNCODE = ("2.15", "osslsigncode-2.15-linux-amd64",
               "5d2890c13e630f21b3a104f0c7482bb2744cdf501237233796f6f9c67d526482", 2335349,
               "https://github.com/mtrojnar/osslsigncode/releases/download/2.15/osslsigncode-2.15-linux-amd64.tar.gz")
ARCHIVE_FILES = ("LongshotBuilder.exe", "LongshotBuilder/LongshotBuilder-Setup.exe", "LongshotBuilder/LongshotBuilder.exe")


class SigningError(Exception):
    pass


def fingerprint(pem):
    """Pin the first (leaf) certificate's DER; osslsigncode validates the certificates themselves."""
    certs = re.findall(r"-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+?)\s*-----END CERTIFICATE-----", pem)
    remainder = re.sub(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", "", pem, flags=re.S)
    if not certs or remainder.strip() or "PRIVATE KEY" in pem:
        raise SigningError("AUTHENTICODE_CERTIFICATE_PEM must contain public certificates only, leaf first")
    try:
        der = [base64.b64decode(re.sub(r"\s", "", c), validate=True) for c in certs]
    except ValueError:
        raise SigningError("invalid public certificate encoding") from None
    if any(not c or c[0] != 0x30 for c in der):
        raise SigningError("invalid public certificate encoding")
    return hashlib.sha256(der[0]).hexdigest()


def certificate_for(env):
    ref, sha = env.get("GITHUB_REF", ""), env.get("GITHUB_SHA", "")
    if (env.get("GITHUB_EVENT_NAME") != "push" or env.get("GITHUB_REPOSITORY") != REPOSITORY
            or not re.fullmatch(r"[0-9a-f]{40}", sha) or env.get("AUTHENTICODE_BUILD_REF") != sha
            or not (ref == "refs/heads/main" or re.fullmatch(r"refs/tags/v\d+\.\d+\.\d+(?:-rc\.\d+)?", ref))):
        print("not signed: not a matching release push (pull requests are never signed)")
        return None
    pem = env.get(CERTIFICATE, "")
    if not pem:
        print("not signed: no certificate configured")
        return None
    fingerprint(pem)
    return pem


@contextlib.contextmanager
def pinned_tool(cache):
    spec = importlib.util.spec_from_file_location("tools", ROOT / "scripts/tools.py")
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    version, prefix, sha, size, url = OSSLSIGNCODE
    tool = tools.Tool("osslsigncode", version, "linux-x64", sha, size, "-", prefix, [url])
    archive = tools.fetch(tool, str(cache))
    # Extract anew from the checked archive, never execute a previously unpacked cached binary.
    with tempfile.TemporaryDirectory(prefix="osslsigncode-", dir=cache) as tmp:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(tmp, filter="data")
        exe = Path(tmp) / prefix / "osslsigncode"
        if not exe.is_file():
            raise SigningError("the pinned archive has no signing executable")
        yield str(exe)


def key_arguments(env):
    uri = env.get("AUTHENTICODE_KEY_URI", "")
    # PKCS#11 selectors only: no query/pin-source/pin-value, file key, or shell fragment.
    fields = uri.removeprefix("pkcs11:").split(";")
    allowed = {"token", "manufacturer", "serial", "model", "object", "type", "id", "slot-id"}
    if (not uri.startswith("pkcs11:") or "?" in uri or not all("=" in f and f.split("=", 1)[0] in allowed for f in fields)
            or any(c in uri for c in "\r\n") or "pin-" in urllib.parse.unquote(uri).lower()):
        raise SigningError("AUTHENTICODE_KEY_URI must be a PKCS#11 selector without credentials")
    paths = []
    for name in ("AUTHENTICODE_PKCS11_PROVIDER", "AUTHENTICODE_PKCS11_MODULE"):
        path = Path(env.get(name, ""))
        if not path.is_absolute() or not path.is_file():
            raise SigningError(name + " must name a provisioned absolute library path")
        paths.append(str(path))
    return ["-provider", paths[0], "-pkcs11module", paths[1], "-key", uri, "-readpass", "-"]


def run_tool(args, password=None):
    env = dict(os.environ)
    env.pop("AUTHENTICODE_TOKEN_PIN", None)
    try:
        p = subprocess.run([str(a) for a in args], input=password, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise SigningError("could not complete Authenticode signing/verification; no package may be published") from None
    if p.returncode:
        # Provider diagnostics can contain secrets. Never print stdout/stderr or the command.
        raise SigningError("Authenticode signing/verification failed; no package may be published")
    return p.stdout


def verify_file(tool, file, leaf_hash, ca_file=CA_FILE):
    text = run_tool([tool, "verify", "-CAfile", ca_file, "-TSA-CAfile", ca_file,
                     "-require-leaf-hash", "sha256:" + leaf_hash, "-in", file])
    # osslsigncode 2.15 also succeeds without a timestamp. Require its verified TSA result
    # (osslsigncode.c), not merely a printed timestamp/certificate.
    if not re.search(r"^Timestamp Server Signature verification: ok$", text, re.M):
        raise SigningError("signature verification failed: a verified timestamp is required")


def sign_files(files, tool, certificate, key_args, ca_file=CA_FILE, timestamp=TIMESTAMP, password=None):
    """Shared signing/verification path. Tests substitute only a temporary key and trust/TSA."""
    files = [Path(f).resolve() for f in files]
    if not files or len(set(files)) != len(files) or any(not f.is_file() for f in files):
        raise SigningError("all named project executables must exist and be distinct")
    leaf_hash = fingerprint(Path(certificate).read_text(encoding="ascii"))
    with contextlib.ExitStack() as stack:
        staged = []
        for file in files:
            tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".authenticode-", dir=file.parent)))
            signed = tmp / "signed.exe"
            run_tool([tool, "sign", "-certs", certificate, *key_args, "-h", "sha256", "-ts", timestamp,
                      "-HTTPS-CAfile", ca_file, "-in", file, "-out", signed], password)
            verify_file(tool, signed, leaf_hash, ca_file)
            staged.append((signed, file))
        # No original changes if either signing or verification fails.
        for signed, file in staged:
            os.replace(signed, file)
    print("signed and verified: named project executables (SHA256, RFC 3161 timestamp)")


def sign_builder(setup, publish, pem, env, cache):
    args = key_arguments(env)
    pin = env.get("AUTHENTICODE_TOKEN_PIN", "")
    if "\n" in pin or "\r" in pin:
        raise SigningError("AUTHENTICODE_TOKEN_PIN must be one line")
    with tempfile.TemporaryDirectory(prefix="authenticode-cert-") as tmp:
        cert = Path(tmp) / "chain.pem"
        cert.write_text(pem, encoding="ascii")
        cert.chmod(0o600)
        with pinned_tool(cache) as tool:
            sign_files([setup, Path(publish) / "LongshotBuilder.exe"], tool, cert, args, password=pin + "\n")


def verify_archive(archive, tool, leaf_hash, ca_file=CA_FILE):
    with tempfile.TemporaryDirectory(prefix="authenticode-zip-") as tmp, zipfile.ZipFile(archive) as z:
        for i, name in enumerate(ARCHIVE_FILES):
            if z.namelist().count(name) != 1:
                raise SigningError("the package must contain each named project executable exactly once")
            file = Path(tmp) / (str(i) + ".exe")
            file.write_bytes(z.read(name))
            verify_file(tool, file, leaf_hash, ca_file)
    print("verified packaged Authenticode: all three project EXE paths")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    s = sub.add_parser("sign")
    s.add_argument("--setup", required=True)
    s.add_argument("--publish", required=True)
    s.add_argument("--cache", required=True)
    a = p.parse_args(argv)
    try:
        pem = certificate_for(os.environ)
        if a.cmd == "plan":
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8", newline="\n") as f:
                f.write("enabled=%s\n" % ("true" if pem else "false"))
        elif pem:
            sign_builder(a.setup, a.publish, pem, os.environ, a.cache)
    except (SigningError, OSError, ValueError, UnicodeError):
        # Even file/encoding/provider errors must not reproduce secret material.
        print("authenticode.py: signing configuration, operation or verification failed; no package may be published", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
