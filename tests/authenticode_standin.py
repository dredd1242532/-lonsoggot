"""Explicit Linux mechanics stand-in through the production Authenticode command path.

    python -I -B tests/authenticode_standin.py --osslsigncode /path/to/osslsigncode [--out <empty folder>]

Requires Python 3.12+, OpenSSL 3, clang, lld-link and osslsigncode 2.15 for this PC's OS.
Downloads nothing. Uses throwaway software keys/certificates and a loopback RFC 3161 TSA,
not a real token/service or public timestamp authority. No trust store is changed and no
Windows program is run. --out retains only tiny authored stand-ins, public certificates
and hashes, never keys. They are not Builder binaries or publisher-trust evidence.
"""
import argparse
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import io
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args):
    p = subprocess.run([str(a) for a in args], capture_output=True, timeout=120)
    if p.returncode:
        raise RuntimeError("%s failed (%d): %s" % (args[0], p.returncode, p.stderr.decode("utf-8", "replace")))
    return p.stdout


def certificate(folder, name, usage):
    key, cert = folder / (name + ".key"), folder / (name + ".pem")
    run(["openssl", "req", "-new", "-x509", "-newkey", "rsa:3072", "-sha256", "-noenc", "-days", "1",
         "-subj", "/CN=Longshot mechanics stand-in " + name, "-addext", "extendedKeyUsage=critical," + usage,
         "-addext", "keyUsage=critical,digitalSignature", "-addext", "basicConstraints=critical,CA:FALSE",
         "-keyout", key, "-out", cert])
    return key, cert

def config_path(path):
    """Quoted OpenSSL-config path, including Windows drive paths and spaces."""
    return '"' + path.as_posix().replace("\\", "\\\\").replace('"', '\\"') + '"'


def check_output(out):
    out = Path(out)
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise ValueError("--out must name an empty directory (or a new one)")


def export_bundle(folder, out):
    """A strict public-file allowlist: never copy the temporary directory or its keys."""
    out = Path(out)
    check_output(out)
    names = (("unsigned-standin.exe", "unsigned.exe"),
             ("signed-setup-standin.exe", "LongshotBuilder-Setup.exe"),
             ("signed-apphost-standin.exe", "LongshotBuilder.exe"),
             ("signed-library-standin.dll", "LongshotBuilder.dll"),
             ("standin-package.zip", "builder.zip"),
             ("signer.pem", "code.pem"), ("timestamp.pem", "TSA.pem"))
    files = {dest: (folder / source).read_bytes() for dest, source in names}
    for name in ("signer.pem", "timestamp.pem"):
        data = files[name]
        if (not data.startswith(b"-----BEGIN CERTIFICATE-----") or b"-----END CERTIFICATE-----" not in data
                or b"PRIVATE KEY" in data.upper()):
            raise ValueError("only public certificate PEM files may be exported")
    out.mkdir(parents=True, exist_ok=True)
    check_output(out)
    for name, data in files.items():
        with (out / name).open("xb") as f:
            f.write(data)
    sums = "".join("%s  %s\n" % (hashlib.sha256(data).hexdigest(), name) for name, data in sorted(files.items()))
    with (out / "SHA256SUMS").open("x", encoding="ascii", newline="\n") as f:
        f.write(sums)
    print("Exported tiny authored stand-ins, not Builder binaries: %s" % out)
    print("No private key was exported; the self-signed certificates do not establish public trust.")



@contextlib.contextmanager
def timestamp_server(folder, key, cert):
    serial = folder / "tsa.serial"
    serial.write_text("01\n")
    config = folder / "tsa.cnf"
    config.write_text("[tsa]\ndefault_tsa=server\n[server]\nserial=%s\ncrypto_device=builtin\n"
                      "signer_cert=%s\ncerts=%s\nsigner_key=%s\nsigner_digest=sha256\n"
                      "default_policy=1.2.3.4.1\ndigests=sha256\naccuracy=secs:1\nordering=yes\ntsa_name=yes\n"
                      "ess_cert_id_chain=no\ness_cert_id_alg=sha256\n" % tuple(config_path(p) for p in (serial, cert, cert, key)))
    errors = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                with tempfile.TemporaryDirectory(dir=folder) as tmp:
                    query, reply = Path(tmp) / "query.der", Path(tmp) / "reply.der"
                    query.write_bytes(self.rfile.read(int(self.headers["Content-Length"])))
                    run(["openssl", "ts", "-reply", "-config", config, "-queryfile", query, "-out", reply])
                    data = reply.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/timestamp-reply")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                errors.append(str(e))
                self.send_error(500)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port
        assert not errors, errors
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def rejected(fn, error):
    try:
        fn()
    except error:
        return
    raise AssertionError("the negative signature case was accepted")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--osslsigncode", required=True)
    parser.add_argument("--out", type=Path, help="retain public fixtures in a new or empty folder, never private keys")
    a = parser.parse_args()
    if a.out is not None:
        try:
            check_output(a.out)
        except (ValueError, OSError) as e:
            parser.error(str(e))
    tool = str(Path(a.osslsigncode).resolve())
    auth, bp, release = (load(n) for n in ("authenticode", "builder_package", "release"))
    with tempfile.TemporaryDirectory(prefix="authenticode-standin-") as tmp:
        folder = Path(tmp)
        source, obj, unsigned = (folder / n for n in ("test.c", "test.obj", "unsigned.exe"))
        source.write_text("volatile int standin_payload = 1;\nint mainCRTStartup(void) { return standin_payload; }\n")
        run(["clang", "--target=x86_64-pc-windows-msvc", "-c", source, "-o", obj])
        run(["lld-link", "/entry:mainCRTStartup", "/subsystem:console", "/nodefaultlib", "/out:" + str(unsigned), obj])
        key, cert = certificate(folder, "code", "codeSigning")
        tsa_key, tsa_cert = certificate(folder, "TSA", "timeStamping")
        trust = folder / "test-trust.pem"
        trust.write_bytes(cert.read_bytes() + tsa_cert.read_bytes())
        # (the library stand-in is the same authored PE under the builder library's name: Authenticode signs a DLL as an EXE)
        setup, builder, library = folder / "LongshotBuilder-Setup.exe", folder / "LongshotBuilder.exe", folder / "LongshotBuilder.dll"
        for file in (setup, builder, library):
            shutil.copyfile(unsigned, file)
        leaf = auth.fingerprint(cert.read_text())
        with timestamp_server(folder, tsa_key, tsa_cert) as tsa:
            # The real sign_files/run_tool/verify_file path; only key access and trust/TSA are stand-ins.
            auth.sign_files([setup, builder, library], tool, cert, ["-key", str(key)], ca_file=trust, timestamp=tsa)
        print("stand-in: production signing path verifies three embedded SHA256 signatures and loopback RFC 3161 timestamps")
        print("stand-in: unsigned %d bytes; signed setup %d bytes; signed apphost %d bytes; signed library %d bytes" % (
            unsigned.stat().st_size, setup.stat().st_size, builder.stat().st_size, library.stat().st_size))

        package, archive = folder / "package", folder / "builder.zip"
        bp.copy(str(builder), str(package / "LongshotBuilder.exe"))
        bp.copy(str(library), str(package / "LongshotBuilder.dll"))
        bp.copy(str(setup), str(package / "LongshotBuilder-Setup.exe"))
        with contextlib.redirect_stdout(io.StringIO()):
            bp.make_zip(str(package), "v3.0.0", str(setup), str(archive))
            release.main(["sums", str(archive), "--out", str(folder / "SHA256SUMS")])
        assert release.read_sums((folder / "SHA256SUMS").read_text()) == {"builder.zip": hashlib.sha256(archive.read_bytes()).hexdigest()}
        auth.verify_archive(archive, tool, leaf, trust)
        with zipfile.ZipFile(archive) as z:
            assert z.read(auth.ARCHIVE_FILES[0]) == z.read(auth.ARCHIVE_FILES[1]) == setup.read_bytes()
            assert z.read(auth.ARCHIVE_FILES[2]) == builder.read_bytes()
            assert z.read(auth.ARCHIVE_FILES[3]) == library.read_bytes()
        print("stand-in: four packaged project file paths verify through the production archive checker; final zip checksum matches")

        altered = bytearray(setup.read_bytes())
        pe = struct.unpack_from("<I", altered, 0x3c)[0]
        assert altered[pe:pe + 4] == b"PE\0\0"
        section = pe + 24 + struct.unpack_from("<H", altered, pe + 20)[0]
        assert altered[section:section + 8].rstrip(b"\0") == b".text"
        altered[struct.unpack_from("<I", altered, section + 20)[0]] ^= 1
        tampered = folder / "tampered.exe"
        tampered.write_bytes(altered)
        rejected(lambda: auth.verify_file(tool, tampered, leaf, trust), auth.SigningError)
        rejected(lambda: auth.verify_file(tool, setup, "0" * 64, trust), auth.SigningError)
        no_timestamp = folder / "no-timestamp.exe"
        run([tool, "sign", "-certs", cert, "-key", key, "-h", "sha256", "-in", unsigned, "-out", no_timestamp])
        rejected(lambda: auth.verify_file(tool, no_timestamp, leaf, trust), auth.SigningError)
        tampered_archive = folder / "tampered-package.zip"
        with zipfile.ZipFile(tampered_archive, "w") as z:
            for name in auth.ARCHIVE_FILES:
                z.writestr(name, tampered.read_bytes() if name == auth.ARCHIVE_FILES[2] else setup.read_bytes())
        rejected(lambda: auth.verify_archive(tampered_archive, tool, leaf, trust), auth.SigningError)
        print("stand-in: altered code, wrong leaf, missing timestamp and tampered package are rejected")
        if a.out is not None:
            export_bundle(folder, a.out)
    print("PASS: real command-path mechanics with temporary software keys/TSA; not PKCS11, public trust, Windows or live CI proof")


if __name__ == "__main__":
    main()
