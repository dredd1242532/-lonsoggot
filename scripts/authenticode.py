"""Optional Authenticode on the Linux runner, before packaging (docs/releases.md).

    python -I scripts/authenticode.py plan
        writes enabled=true/false to GITHUB_OUTPUT
    python -I scripts/authenticode.py runner
        before any secret is in a step: stops ("not on the signing runner") unless the PKCS#11 provider and module
        (AUTHENTICODE_PKCS11_PROVIDER, AUTHENTICODE_PKCS11_MODULE) are on this runner
    python -I scripts/authenticode.py sign --setup <exe> --publish <folder> --cache <folder>
        signs only the setup, the command-line builder's apphost and its LongshotBuilder.dll with the configured
        PKCS#11 key, then verifies them

The public certificate chain is AUTHENTICODE_CERTIFICATE_PEM (leaf first). The private
key stays in a token/service selected by AUTHENTICODE_KEY_URI; the optional token PIN
is sent on stdin, never in argv or logs. No PEM/PFX private-key route is exposed by CI.
The signer's chain is verified against AUTHENTICODE_ROOT_PEM (the public root, or root and
intermediates, of the code-signing CA) when it is set, else against the runner's system
bundle, which holds TLS roots only; the timestamps are verified against the system bundle.
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
ROOT_CERTIFICATE = "AUTHENTICODE_ROOT_PEM"
# RFC 3161 servers, tried in turn (several -ts): one server's outage does not stop a release, all of them down does.
TIMESTAMPS = ("https://timestamp.digicert.com", "http://timestamp.sectigo.com")
CA_FILE = "/etc/ssl/certs/ca-certificates.crt"
PKCS11_PATHS = ("AUTHENTICODE_PKCS11_PROVIDER", "AUTHENTICODE_PKCS11_MODULE")
# Same convention as builder_package.LINUX_LLVM_MINGW: version, prefix, SHA256, size, URL;
# tools.fetch checks the archive. This CI-only tool does not belong in players' tools.lock.
OSSLSIGNCODE = ("2.15", "osslsigncode-2.15-linux-amd64",
               "5d2890c13e630f21b3a104f0c7482bb2744cdf501237233796f6f9c67d526482", 2335349,
               "https://github.com/mtrojnar/osslsigncode/releases/download/2.15/osslsigncode-2.15-linux-amd64.tar.gz")
# (in the publish folder: the apphost, and the builder's own code that it loads; PublishSingleFile is false)
PUBLISH_FILES = ("LongshotBuilder.exe", "LongshotBuilder.dll")
ARCHIVE_FILES = ("LongshotBuilder.exe", "LongshotBuilder/LongshotBuilder-Setup.exe", "LongshotBuilder/LongshotBuilder.exe",
                 "LongshotBuilder/LongshotBuilder.dll")


class SigningError(Exception):
    pass


def fingerprint(pem, name=CERTIFICATE):
    """Pin the first (leaf) certificate's DER; osslsigncode validates the certificates themselves."""
    certs = re.findall(r"-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+?)\s*-----END CERTIFICATE-----", pem)
    remainder = re.sub(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", "", pem, flags=re.S)
    if not certs or remainder.strip() or "PRIVATE KEY" in pem:
        raise SigningError(name + " must contain public certificates only" + (", leaf first" if name == CERTIFICATE else ""))
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
def trust_anchor(env):
    """The -CAfile for the signer's chain: the maintainers' pinned code-signing root when set, else the system bundle.

    A set but malformed AUTHENTICODE_ROOT_PEM stops (fail closed); it never falls back to the system bundle."""
    pem = env.get(ROOT_CERTIFICATE, "")
    if not pem.strip():
        print("signer trust: the runner's system bundle %s (TLS roots only: set %s for a code-signing root)" % (CA_FILE, ROOT_CERTIFICATE))
        yield CA_FILE
        return
    root = fingerprint(pem, ROOT_CERTIFICATE)
    with tempfile.TemporaryDirectory(prefix="authenticode-root-") as tmp:
        path = Path(tmp) / "root.pem"
        path.write_text(pem, encoding="ascii")
        print("signer trust: %s, first certificate SHA-256 %s" % (ROOT_CERTIFICATE, root))
        yield str(path)


def signing_runner(env):
    """True when this runner has the PKCS#11 libraries. Run in a step that holds no secret, before the one that does."""
    for name in PKCS11_PATHS:
        path = Path(env.get(name, ""))
        if not env.get(name) or not path.is_absolute() or not path.is_file():
            print("not on the signing runner: %s does not name a library on this runner; the token PIN is not used" % name,
                  file=sys.stderr)
            return False
    print("signing runner: the PKCS#11 provider and module are present")
    return True


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
    for name in PKCS11_PATHS:
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


def verify_file(tool, file, leaf_hash, ca_file=CA_FILE, tsa_ca_file=None):
    """ca_file: the signer's trust; tsa_ca_file: the timestamp's (ca_file when not given)."""
    text = run_tool([tool, "verify", "-CAfile", ca_file, "-TSA-CAfile", tsa_ca_file or ca_file,
                     "-require-leaf-hash", "sha256:" + leaf_hash, "-in", file])
    # osslsigncode 2.15 also succeeds without a timestamp. Require its verified TSA result
    # (osslsigncode.c), not merely a printed timestamp/certificate.
    if not re.search(r"^Timestamp Server Signature verification: ok$", text, re.M):
        raise SigningError("signature verification failed: a verified timestamp is required")


def sign_files(files, tool, certificate, key_args, ca_file=CA_FILE, timestamp=TIMESTAMPS, password=None, tsa_ca_file=None):
    """Shared signing/verification path. Tests substitute only a temporary key and trust/TSA.

    ca_file is the signer's trust; tsa_ca_file (ca_file when not given) the timestamp servers' and their HTTPS."""
    files = [Path(f).resolve() for f in files]
    if not files or len(set(files)) != len(files) or any(not f.is_file() for f in files):
        raise SigningError("all named project files must exist and be distinct")
    leaf_hash = fingerprint(Path(certificate).read_text(encoding="ascii"))
    tsa_ca_file = tsa_ca_file or ca_file
    servers = [timestamp] if isinstance(timestamp, str) else list(timestamp)
    if not servers:
        raise SigningError("an RFC 3161 timestamp server is required")
    ts_args = [a for url in servers for a in ("-ts", url)]
    with contextlib.ExitStack() as stack:
        staged = []
        for file in files:
            tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".authenticode-", dir=file.parent)))
            signed = tmp / ("signed" + file.suffix)
            run_tool([tool, "sign", "-certs", certificate, *key_args, "-h", "sha256", *ts_args,
                      "-HTTPS-CAfile", tsa_ca_file, "-in", file, "-out", signed], password)
            verify_file(tool, signed, leaf_hash, ca_file, tsa_ca_file)
            staged.append((signed, file))
        # No original changes if any signing or verification fails.
        for signed, file in staged:
            os.replace(signed, file)
    print("signed and verified: %d named project files (SHA256, RFC 3161 timestamp)" % len(files))


def sign_builder(setup, publish, pem, env, cache):
    # (the PKCS#11 libraries are checked before the PIN is read: off the signing runner it stops here)
    args = key_arguments(env)
    pin = env.get("AUTHENTICODE_TOKEN_PIN", "")
    if "\n" in pin or "\r" in pin:
        raise SigningError("AUTHENTICODE_TOKEN_PIN must be one line")
    with tempfile.TemporaryDirectory(prefix="authenticode-cert-") as tmp, trust_anchor(env) as ca_file:
        cert = Path(tmp) / "chain.pem"
        cert.write_text(pem, encoding="ascii")
        cert.chmod(0o600)
        with pinned_tool(cache) as tool:
            sign_files([setup, *(Path(publish) / n for n in PUBLISH_FILES)], tool, cert, args,
                       ca_file=ca_file, password=pin + "\n", tsa_ca_file=CA_FILE)


def verify_archive(archive, tool, leaf_hash, ca_file=CA_FILE, tsa_ca_file=None):
    with tempfile.TemporaryDirectory(prefix="authenticode-zip-") as tmp, zipfile.ZipFile(archive) as z:
        for i, name in enumerate(ARCHIVE_FILES):
            if z.namelist().count(name) != 1:
                raise SigningError("the package must contain each named project file exactly once")
            file = Path(tmp) / (str(i) + Path(name).suffix)
            file.write_bytes(z.read(name))
            verify_file(tool, file, leaf_hash, ca_file, tsa_ca_file)
    print("verified packaged Authenticode: all %d project file paths" % len(ARCHIVE_FILES))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("runner")
    s = sub.add_parser("sign")
    s.add_argument("--setup", required=True)
    s.add_argument("--publish", required=True)
    s.add_argument("--cache", required=True)
    a = p.parse_args(argv)
    if a.cmd == "runner":
        # (reads two public variables only: no certificate, key selector or PIN)
        return 0 if signing_runner(os.environ) else 1
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
