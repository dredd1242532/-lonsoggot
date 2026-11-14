"""Hash-bound qualification of otherwise unstamped NASM archive members.

The producer records facts; the operator separately reviews import availability
and ISA dispatch, then selects the exact final receipt digest. There is no
'approved' boolean or general unknown-member waiver. No object is rewritten.
"""
import hashlib
import json
import os
from pathlib import Path
import re

from darwin_package import DependencyError, MINIMUM_OS, digest, raw_metadata, system_library, version

SCHEMA = "longshot.macos-archive-qualification.v1"
REVIEW_SCHEMA = "longshot.macos-archive-review.v1"
MAX_BYTES = 8 << 20
BASELINE = {"x86-64", "x86-64-v2", "fpu", "cmov", "cx8", "fxsr", "mmx", "sse", "sse2", "sse3", "ssse3",
            "sse4.1", "sse4.2", "popcnt", "cx16", "lahf-sahf"}


def qualification_digest(member):
    return hashlib.sha256(json.dumps(member, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise DependencyError("invalid %s fields" % label)


def _list(value, bound, label):
    if not isinstance(value, list) or len(value) > bound:
        raise DependencyError("invalid or oversized " + label)
    return value


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise DependencyError("invalid SHA-256 in archive qualification")
    return value


def _json(path):
    with open(path, "rb") as f:
        body = f.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise DependencyError("archive qualification/review exceeds eight MiB")
    return json.loads(body)


def _option(argv, option):
    for i, arg in enumerate(argv):
        if arg == option:
            return argv[i + 1] if i + 1 < len(argv) else ""
        if arg.startswith(option) and len(arg) > len(option):
            return arg[len(option):]
    return ""


class Qualifications:
    def __init__(self, path, sha256, arch):
        self.arch = arch
        self.fingerprints: dict[str, str] = {}
        self.input_records: dict[str, dict] = {}
        self.tool_records: dict[str, dict] = {}
        self.generator_records: dict[str, dict] = {}
        self.archives: dict[str, dict] = {}
        self.by_digest: dict[str, dict] = {}
        self.admitted: dict[str, set] = {}
        self._file(path, sha256)
        data = _json(path)
        _keys(data, "schema target inputs tools generators archives review", "archive qualification")
        if data["schema"] != SCHEMA or data["target"] != {"arch": arch, "minimum_macos": MINIMUM_OS}:
            raise DependencyError("archive qualification target/schema mismatch")
        for item in _list(data["inputs"], 16384, "input records"):
            _keys(item, "id kind path sha256", "input")
            if item["kind"] not in ("source", "include", "generated", "evidence"):
                raise DependencyError("unknown archive input kind")
            self._insert(self.input_records, item)
            self._file(item["path"], item["sha256"])
        for item in _list(data["tools"], 16, "tool records"):
            _keys(item, "id path sha256 version process_arch", "tool")
            if not isinstance(item["version"], str) or not item["version"] or item["process_arch"] != "arm64":
                raise DependencyError("archive producer tool must have an identified native arm64 version")
            self._insert(self.tool_records, item)
            self._file(item["path"], item["sha256"])
        for item in _list(data["generators"], 4096, "generator records"):
            _keys(item, "id tool_id cwd argv input_ids output_ids", "generator")
            self._insert(self.generator_records, item)
            self._command(item)
            self._refs(item["input_ids"], {"source", "include", "generated"})
            self._refs(item["output_ids"], {"generated"})
        total = 0
        for archive in _list(data["archives"], 8, "archives"):
            _keys(archive, "path sha256 arch members", "archive")
            key = self._file(archive["path"], archive["sha256"])
            if archive["arch"] != arch or key in self.archives or archive["sha256"] in self.by_digest:
                raise DependencyError("duplicate archive or target mismatch")
            info = raw_metadata(key, arch)
            if info["kind"] != "archive":
                raise DependencyError("qualification input is not a Mach-O archive")
            records = _list(archive["members"], 4096, "unstamped member records")
            total += len(records)
            if total > 4096:
                raise DependencyError("too many unstamped archive members")
            actual = {m["ordinal"]: m for m in info["archive_members"] if m["minimum_os"] is None}
            if any(not isinstance(m, dict) or type(m.get("ordinal")) is not int for m in records):
                raise DependencyError("invalid member ordinal")
            if len({m["ordinal"] for m in records}) != len(records) or {m["ordinal"] for m in records} != set(actual):
                raise DependencyError("qualification must enumerate every and only unstamped archive member")
            saved = {"record": archive, "metadata": info, "unstamped": actual}
            self.archives[key] = self.by_digest[archive["sha256"]] = saved
        review = data["review"]
        _keys(review, "path sha256", "semantic review")
        self._file(review["path"], review["sha256"])
        reviewed = _json(review["path"])
        _keys(reviewed, "schema target qualifications", "semantic review packet")
        if reviewed["schema"] != REVIEW_SCHEMA or reviewed["target"] != data["target"]:
            raise DependencyError("semantic review target/schema mismatch")
        rows = _list(reviewed["qualifications"], 4096, "reviewed qualifications")
        accepted = {}
        for row in rows:
            _keys(row, "archive_sha256 ordinal member_sha256 qualification_sha256", "reviewed member")
            key = (_sha(row["archive_sha256"]), row["ordinal"], _sha(row["member_sha256"]))
            if type(row["ordinal"]) is not int or key in accepted:
                raise DependencyError("invalid or duplicate semantic-review member")
            accepted[key] = _sha(row["qualification_sha256"])
        expected = {}
        for path, saved in self.archives.items():
            archive = saved["record"]
            for member in archive["members"]:
                actual = saved["unstamped"][member["ordinal"]]
                self._member(member, actual)
                key = (archive["sha256"], member["ordinal"], member["sha256"])
                expected[key] = qualification_digest(member)
            self.admitted[path] = {(m["ordinal"], m["name"], m["sha256"]) for m in archive["members"]}
        if accepted != expected:
            raise DependencyError("semantic review is not bound to every exact member qualification")
        self.unchanged()

    @staticmethod
    def _insert(table, item):
        ident = item["id"]
        if not isinstance(ident, str) or not ident or len(ident) > 512 or ident in table:
            raise DependencyError("invalid or duplicate qualification record id")
        table[ident] = item

    def _file(self, path, sha256):
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file():
            raise DependencyError("qualification needs an absolute retained file: " + str(path))
        sha256 = _sha(sha256)
        key = str(Path(path).resolve())
        if self.fingerprints.get(key, sha256) != sha256 or digest(key) != sha256:
            raise DependencyError("qualified input hash mismatch: " + key)
        self.fingerprints[key] = sha256
        return key

    def _refs(self, ids, kinds, nonempty=True):
        _list(ids, 4096, "input references")
        if (nonempty and not ids) or any(not isinstance(i, str) or i not in self.input_records for i in ids) or len(set(ids)) != len(ids):
            raise DependencyError("missing or duplicate qualification evidence/input references")
        values = [self.input_records[i] for i in ids]
        if any(item["kind"] not in kinds for item in values):
            raise DependencyError("wrong qualification evidence/input kind")
        return values

    def _command(self, item):
        argv = _list(item["argv"], 4096, "producer argv")
        tool = self.tool_records.get(item["tool_id"])
        cwd = Path(item["cwd"])
        if not tool or not cwd.is_absolute() or not cwd.is_dir() or not argv or any(not isinstance(a, str) or not a for a in argv):
            raise DependencyError("incomplete original producer command")
        if Path(argv[0]).resolve() != Path(tool["path"]).resolve():
            raise DependencyError("producer argv does not begin with its recorded tool")
        return tool, cwd, argv

    def _member(self, record, actual):
        _keys(record, "ordinal name sha256 source_id include_ids generator_ids assembler undefined_symbols resolutions isa qualification_evidence_ids", "member")
        if self.arch != "x86_64" or record["name"] != actual["name"] or _sha(record["sha256"]) != actual["sha256"]:
            raise DependencyError("unstamped NASM member identity/architecture mismatch")
        source = self._refs([record["source_id"]], {"source"})[0]
        includes = self._refs(record["include_ids"], {"include", "generated"}, nonempty=False)
        generator_ids = _list(record["generator_ids"], 4096, "member generators")
        if len(set(generator_ids)) != len(generator_ids) or any(g not in self.generator_records for g in generator_ids):
            raise DependencyError("unknown or duplicate member generator")
        generated = {i for g in generator_ids for i in self.generator_records[g]["output_ids"]}
        if any(i["kind"] == "generated" and i["id"] not in generated for i in includes):
            raise DependencyError("generated assembly input has no recorded generator")
        assembler = record["assembler"]
        _keys(assembler, "tool_id cwd argv output", "assembler")
        tool, cwd, argv = self._command(assembler)
        if "nasm" not in tool["version"].lower() or _option(argv[1:], "-f") != "macho64":
            raise DependencyError("unstamped-member receipt is not native NASM macho64")
        if Path(source["path"]).resolve() not in {(cwd / arg).resolve() for arg in argv[1:] if not arg.startswith("-")}:
            raise DependencyError("NASM argv does not bind the recorded source")
        output = self._file(assembler["output"], record["sha256"])
        if (cwd / _option(argv[1:], "-o")).resolve() != Path(output):
            raise DependencyError("NASM argv does not bind the retained object")
        undefined = _list(record["undefined_symbols"], 1024, "member undefined symbols")
        if undefined != actual["undefined_symbols"]:
            raise DependencyError("actual undefined symbols differ from the member qualification")
        resolutions = _list(record["resolutions"], 1024, "member import resolutions")
        if any(not isinstance(r, dict) or not isinstance(r.get("symbol"), str) for r in resolutions):
            raise DependencyError("invalid import resolution record")
        if sorted(r["symbol"] for r in resolutions) != undefined:
            raise DependencyError("each actual undefined symbol needs exactly one closed resolution")
        for resolution in resolutions:
            self._resolution(resolution)
        self._isa(record["isa"], actual)
        self._refs(record["qualification_evidence_ids"], {"evidence"})

    def _resolution(self, record):
        kind, symbol = record.get("kind"), record["symbol"]
        if kind == "archive-member":
            _keys(record, "symbol kind archive_sha256 member_ordinal member_sha256", "archive-member resolution")
            provider = self.by_digest.get(record["archive_sha256"])
            ordinal = record["member_ordinal"]
            if not provider or type(ordinal) is not int or not 0 <= ordinal < len(provider["metadata"]["archive_members"]):
                raise DependencyError("unknown archive-member import provider")
            member = provider["metadata"]["archive_members"][ordinal]
            if member["sha256"] != _sha(record["member_sha256"]) or symbol not in member["defined_symbols"]:
                raise DependencyError("archive import provider does not define the exact symbol/member")
            # Every unstamped provider has its own fully reviewed entry; stamped providers were checked by raw_metadata.
        elif kind == "dylib":
            _keys(record, "symbol kind path sha256 evidence_ids", "dylib resolution")
            path = self._file(record["path"], record["sha256"])
            provider = raw_metadata(path, self.arch, symbols=True)
            if provider["kind"] != "dylib" or provider["minimum_os"] is None or symbol not in provider["defined_symbols"]:
                raise DependencyError("dylib import provider is not a qualified exporting slice")
            self._refs(record["evidence_ids"], {"evidence"})
        elif kind == "system":
            _keys(record, "symbol kind install_name introduced_macos evidence_ids", "system resolution")
            if not system_library(record["install_name"]) or os.path.normpath(record["install_name"]) != record["install_name"]:
                raise DependencyError("system provider is not a canonical macOS install path")
            if version(record["introduced_macos"]) > version(MINIMUM_OS):
                raise DependencyError("system symbol availability is newer than target macOS")
            self._refs(record["evidence_ids"], {"evidence"})
        else:
            raise DependencyError("unknown/unclosed archive import resolution")

    def _isa(self, isa, actual):
        _keys(isa, "baseline entries", "ISA mapping")
        entries = _list(isa["entries"], 4096, "ISA entry mappings")
        if isa["baseline"] != "x86-64-v2" or any(not isinstance(e, dict) or not isinstance(e.get("symbol"), str) for e in entries):
            raise DependencyError("invalid ISA baseline/entries")
        if sorted(e["symbol"] for e in entries) != actual["entry_symbols"] or (actual["instruction_bytes"] and not entries):
            raise DependencyError("ISA mapping must cover each actual externally visible code entry")
        for entry in entries:
            _keys(entry, "symbol classification features dispatch_source_ids evidence_ids", "ISA entry")
            features = _list(entry["features"], 128, "ISA features")
            if not features or any(not isinstance(f, str) or re.fullmatch(r"[a-z0-9_.-]+", f) is None or
                                   f in ("unknown", "unspecified", "unreviewed", "todo") for f in features):
                raise DependencyError("ISA features remain unknown")
            if entry["classification"] == "baseline":
                if not set(features) <= BASELINE or entry["dispatch_source_ids"]:
                    raise DependencyError("higher ISA cannot be labelled baseline")
            elif entry["classification"] == "runtime-dispatched":
                self._refs(entry["dispatch_source_ids"], {"source", "include"})
            else:
                raise DependencyError("unclassified ISA entry")
            self._refs(entry["evidence_ids"], {"evidence"})

    def archive(self, path, metadata):
        key = str(Path(path).resolve())
        actual = {(m["ordinal"], m["name"], m["sha256"]) for m in metadata["archive_members"] if m["minimum_os"] is None}
        if key not in self.archives or digest(key) != self.archives[key]["record"]["sha256"] or self.admitted[key] != actual:
            raise DependencyError("unstamped archive is not the exact reviewed member set: " + key)

    def unchanged(self):
        for path, sha256 in self.fingerprints.items():
            if digest(path) != sha256:
                raise DependencyError("archive qualification input changed after admission: " + path)


def from_environment(arch):
    path = os.environ.get("LONGSHOT_DARWIN_ARCHIVE_RECEIPT", "")
    sha256 = os.environ.get("LONGSHOT_DARWIN_ARCHIVE_RECEIPT_SHA256", "")
    if not path and not sha256:
        return None
    if not path or not sha256:
        raise DependencyError("archive qualification requires both a receipt path and its exact reviewed SHA-256")
    try:
        return Qualifications(path, sha256, arch)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise DependencyError("archive qualification refused: %s" % e) from e
