"""Explicit Linux mechanics stand-in through the production Authenticode command path.

    python -I -B tests/authenticode_standin.py --osslsigncode /path/to/osslsigncode

Requires openssl, clang, lld-link and the pinned osslsigncode. Downloads nothing. Uses
throwaway software keys/certificates and a loopback RFC 3161 TSA, not a real token/service
or public timestamp authority. No trust store is changed and no Windows program is run.
This proves mechanics, not publisher trust, PKCS#11 credentials or Windows acceptance.
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


@contextlib.contextmanager
def timestamp_server(folder, key, cert):
    serial = folder / "tsa.serial"
    serial.write_text("01\n")
    config = folder / "tsa.cnf"
    config.write_text("[tsa]\ndefault_tsa=server\n[server]\nserial=%s\ncrypto_device=builtin\n"
                      "signer_cert=%s\ncerts=%s\nsigner_key=%s\nsigner_digest=sha256\n"
                      "default_policy=1.2.3.4.1\ndigests=sha256\naccuracy=secs:1\nordering=yes\ntsa_name=yes\n"
                      "ess_cert_id_chain=no\ness_cert_id_alg=sha256\n" % (serial, cert, cert, key))
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
    a = parser.parse_args()
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
        setup, builder = folder / "LongshotBuilder-Setup.exe", folder / "LongshotBuilder.exe"
        shutil.copyfile(unsigned, setup)
        shutil.copyfile(unsigned, builder)
        leaf = auth.fingerprint(cert.read_text())
        with timestamp_server(folder, tsa_key, tsa_cert) as tsa:
            # The real sign_files/run_tool/verify_file path; only key access and trust/TSA are stand-ins.
            auth.sign_files([setup, builder], tool, cert, ["-key", str(key)], ca_file=trust, timestamp=tsa)
        print("stand-in: production signing path verifies two embedded SHA256 signatures and loopback RFC 3161 timestamps")
        print("stand-in: unsigned %d bytes; signed setup %d bytes; signed apphost %d bytes" % (
            unsigned.stat().st_size, setup.stat().st_size, builder.stat().st_size))

        package, archive = folder / "package", folder / "builder.zip"
        bp.copy(str(builder), str(package / "LongshotBuilder.exe"))
        bp.copy(str(setup), str(package / "LongshotBuilder-Setup.exe"))
        with contextlib.redirect_stdout(io.StringIO()):
            bp.make_zip(str(package), "v3.0.0", str(setup), str(archive))
            release.main(["sums", str(archive), "--out", str(folder / "SHA256SUMS")])
        assert release.read_sums((folder / "SHA256SUMS").read_text()) == {"builder.zip": hashlib.sha256(archive.read_bytes()).hexdigest()}
        auth.verify_archive(archive, tool, leaf, trust)
        with zipfile.ZipFile(archive) as z:
            assert z.read(auth.ARCHIVE_FILES[0]) == z.read(auth.ARCHIVE_FILES[1]) == setup.read_bytes()
            assert z.read(auth.ARCHIVE_FILES[2]) == builder.read_bytes()
        print("stand-in: three packaged EXE paths verify through the production archive checker; final zip checksum matches")

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
        with zipfile.ZipFile(archive, "w") as z:
            for name in auth.ARCHIVE_FILES:
                z.writestr(name, tampered.read_bytes() if name == auth.ARCHIVE_FILES[2] else setup.read_bytes())
        rejected(lambda: auth.verify_archive(archive, tool, leaf, trust), auth.SigningError)
        print("stand-in: altered code, wrong leaf, missing timestamp and tampered package are rejected")
    print("PASS: real command-path mechanics with temporary software keys/TSA; not PKCS11, public trust, Windows or live CI proof")


if __name__ == "__main__":
    main()
