"""Optional Windows builder Authenticode signing, before packaging (docs/releases.md).

    python -I scripts/authenticode.py plan
        writes enabled=true/false to GITHUB_OUTPUT; selects the release-only runner
    python -I scripts/authenticode.py sign --setup <setup exe> --publish <dotnet publish folder>
        signs only the setup and LongshotBuilder.exe, then verifies both

AUTHENTICODE_CERTIFICATE_SHA1 is a repository secret selecting a certificate in the signing
account's CurrentUser/My store. The private key stays with its hardware/provider, never in
this script, the checkout or a command line. Windows PowerShell is part of Windows: no new
signing executable is downloaded, and tools.lock remains the player's build tools only.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPOSITORY = "MagicBeanDev/Longshot"
CERTIFICATE = "AUTHENTICODE_CERTIFICATE_SHA1"
TIMESTAMP = "http://timestamp.digicert.com"
# Windows' Authenticode API used by this cmdlet requires HTTP, not HTTPS. The timestamp
# is itself signed, and verification below requires it. See docs/releases.md.
POWERSHELL = r'''
$ErrorActionPreference = 'Stop'
$WarningPreference = 'Stop'
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $cert = Get-Item -LiteralPath ('Cert:\CurrentUser\My\' + $request.certificate)
    if (-not $cert.HasPrivateKey) { throw 'The certificate has no signing key.' }
    $null = Set-AuthenticodeSignature -LiteralPath $request.file -Certificate $cert `
        -HashAlgorithm SHA256 -IncludeChain NotRoot -TimestampServer $request.timestamp
    $signature = Get-AuthenticodeSignature -LiteralPath $request.file
    [ordered]@{
        status = [string]$signature.Status
        signature_type = [string]$signature.SignatureType
        signer = [string]$signature.SignerCertificate.Thumbprint
        timestamped = ($null -ne $signature.TimeStamperCertificate)
    } | ConvertTo-Json -Compress
} catch {
    # Provider errors can include credentials. Never echo them, or the request.
    [Console]::Error.WriteLine('Authenticode signing or verification failed.')
    exit 1
}
'''


class SigningError(Exception):
    pass


def certificate_for(env):
    """None outside this repository's release pushes, even if a caller supplied a secret."""
    ref = env.get("GITHUB_REF", "")
    if (env.get("GITHUB_EVENT_NAME") != "push" or env.get("GITHUB_REPOSITORY") != REPOSITORY
            or not (ref == "refs/heads/main" or re.fullmatch(r"refs/tags/v\d+\.\d+\.\d+(?:-rc\.\d+)?", ref))):
        print("not signed: not a release push (pull requests are never signed)")
        return None
    value = env.get(CERTIFICATE, "")
    if not value:
        print("not signed: no certificate configured")
        return None
    if not re.fullmatch(r"[0-9A-Fa-f]{40}", value):
        raise SigningError("AUTHENTICODE_CERTIFICATE_SHA1 must be the certificate's 40 hex thumbprint")
    return value.upper()


def command(env):
    """Use the OS-supplied Windows PowerShell, not an executable found in the checkout/PATH."""
    root = env.get("SystemRoot")
    if not root:
        raise SigningError("Windows PowerShell is required for configured signing")
    return [str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL]


def verify_result(text, certificate):
    """No success from a zero exit alone: Windows must trust the embedded, timestamped signature."""
    try:
        result = json.loads(text)
    except (TypeError, ValueError):
        raise SigningError("the signature verifier returned no usable result") from None
    if (not isinstance(result, dict) or result.get("status") != "Valid"
            or result.get("signature_type") != "Authenticode"
            or result.get("signer") != certificate or result.get("timestamped") is not True):
        raise SigningError("signature verification failed: need a trusted, timestamped signature by the configured certificate")


def sign_builder(setup, publish, certificate, env):
    # No globs: .NET's and tools.lock's third-party executables keep their own signatures.
    files = [Path(setup).resolve(), (Path(publish) / "LongshotBuilder.exe").resolve()]
    if files[0] == files[1] or any(not f.is_file() for f in files):
        raise SigningError("both the setup and the published LongshotBuilder.exe are required")
    args = command(env)
    for file in files:
        request = json.dumps({"file": str(file), "certificate": certificate, "timestamp": TIMESTAMP})
        try:
            p = subprocess.run(args, input=request, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            raise SigningError("could not complete Windows Authenticode signing; no package may be published") from None
        if p.returncode:
            # Do not print the child's output: a provider might expose secret material there.
            raise SigningError("Windows Authenticode signing or verification failed; no package may be published")
        verify_result(p.stdout, certificate)
    print("signed and verified: LongshotBuilder-Setup.exe and published LongshotBuilder.exe (SHA256, timestamped)")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    s = sub.add_parser("sign")
    s.add_argument("--setup", required=True)
    s.add_argument("--publish", required=True)
    a = p.parse_args(argv)
    try:
        certificate = certificate_for(os.environ)
        if a.cmd == "plan":
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8", newline="\n") as f:
                f.write("enabled=%s\n" % ("true" if certificate else "false"))
        elif certificate:
            sign_builder(a.setup, a.publish, certificate, dict(os.environ))
    except SigningError as e:
        print("authenticode.py: " + str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
