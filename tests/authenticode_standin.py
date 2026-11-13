"""Optional Linux Authenticode mechanics stand-in; not a Windows/provider/publisher-trust test.

    python -I -B tests/authenticode_standin.py --osslsigncode /path/to/osslsigncode

Needs openssl, clang and lld-link on PATH, plus osslsigncode. Downloads nothing. Makes a
small authored PE and a throwaway self-signed certificate in a temporary directory; no
key is installed in any trust store. The verifier trusts that certificate explicitly for
this test alone. No timestamp server is contacted or tested. Never use this certificate
or the resulting files for a release. The temporary key and executables are removed.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args, *, ok=True):
    p = subprocess.run([str(a) for a in args], capture_output=True, timeout=120)
    if ok and p.returncode:
        raise RuntimeError("%s failed (%d): %s" % (args[0], p.returncode, p.stderr.decode("utf-8", "replace")))
    if not ok and not p.returncode:
        raise AssertionError("the altered executable was accepted")
    return p.stdout.decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--osslsigncode", required=True)
    a = parser.parse_args()
    tool = str(Path(a.osslsigncode).resolve())
    bp, release = load("builder_package"), load("release")
    with tempfile.TemporaryDirectory(prefix="authenticode-standin-") as tmp:
        folder = Path(tmp)
        source, obj, unsigned, signed = (folder / n for n in ("test.c", "test.obj", "unsigned.exe", "signed.exe"))
        key, cert = folder / "test-only.key", folder / "test-only.pem"
        source.write_text("volatile int standin_payload = 1;\nint mainCRTStartup(void) { return standin_payload; }\n")
        run(["clang", "--target=x86_64-pc-windows-msvc", "-c", source, "-o", obj])
        run(["lld-link", "/entry:mainCRTStartup", "/subsystem:console", "/nodefaultlib", "/out:" + str(unsigned), obj])
        run(["openssl", "req", "-new", "-x509", "-newkey", "rsa:3072", "-sha256", "-noenc", "-days", "1",
             "-subj", "/CN=Longshot mechanics stand-in only", "-addext", "extendedKeyUsage=codeSigning",
             "-addext", "keyUsage=critical,digitalSignature", "-addext", "basicConstraints=critical,CA:FALSE",
             "-keyout", key, "-out", cert])
        run([tool, "sign", "-certs", cert, "-key", key, "-h", "sha256", "-in", unsigned, "-out", signed])
        output = run([tool, "verify", "-CAfile", cert, "-in", signed])
        assert "Signature verification: ok" in output, output
        print("stand-in: authored PE embedded signature verifies with explicit throwaway-certificate trust")
        print("stand-in: unsigned %d bytes; signed %d bytes" % (unsigned.stat().st_size, signed.stat().st_size))

        # Exercise the real package copy/zip and release-checksum implementation, not another archive writer.
        package = folder / "package"
        bp.copy(str(signed), str(package / "LongshotBuilder.exe"))
        bp.copy(str(signed), str(package / "LongshotBuilder-Setup.exe"))
        archive = folder / "builder.zip"
        with contextlib.redirect_stdout(io.StringIO()):
            bp.make_zip(str(package), "v3.0.0", str(signed), str(archive))
            release.main(["sums", str(archive), "--out", str(folder / "SHA256SUMS")])
        sums = release.read_sums((folder / "SHA256SUMS").read_text())
        assert sums == {"builder.zip": hashlib.sha256(archive.read_bytes()).hexdigest()}
        names = ("LongshotBuilder.exe", "LongshotBuilder/LongshotBuilder-Setup.exe", "LongshotBuilder/LongshotBuilder.exe")
        with zipfile.ZipFile(archive) as z:
            for i, name in enumerate(names):
                data = z.read(name)
                assert data == signed.read_bytes()
                extracted = folder / ("copy-%d.exe" % i)
                extracted.write_bytes(data)
                run([tool, "verify", "-CAfile", cert, "-in", extracted])
        print("stand-in: all 3 packaged EXE paths preserve the signature; release checksum covers the finished zip")

        # Flip a byte in the first section (.text), not the PE checksum or certificate-table fields excluded by Authenticode.
        altered = bytearray(signed.read_bytes())
        pe = struct.unpack_from("<I", altered, 0x3c)[0]
        assert altered[pe:pe + 4] == b"PE\0\0"
        section = pe + 24 + struct.unpack_from("<H", altered, pe + 20)[0]
        assert altered[section:section + 8].rstrip(b"\0") == b".text"
        offset = struct.unpack_from("<I", altered, section + 20)[0]
        altered[offset] ^= 1
        tampered = folder / "tampered.exe"
        tampered.write_bytes(altered)
        run([tool, "verify", "-CAfile", cert, "-in", tampered], ok=False)
        print("stand-in: modifying .text is rejected by embedded-signature verification")
    print("PASS: mechanics stand-in only; no Windows execution, public trust, provider or timestamp-service proof")


if __name__ == "__main__":
    main()
