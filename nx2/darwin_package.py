"""Inspect and relocate the exact macOS dependency closure; never load a library.

Only target Mach-O slices with a declared macOS deployment floor are admitted.
System SDK libraries stay on the system. All other dylibs are copied by value,
rewritten and ad-hoc signed in owned staging storage before replacing an output.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

MINIMUM_OS = "14.4"
MANIFEST = "longshot-macos.json"
CPU = {"arm64": 0x100000c, "x86_64": 0x1000007}
LOAD_DYLIB = {0xc, 0x80000018, 0x20, 0x8000001f, 0x80000023}
RUNTIME_NAMES = ("libSDL2-2.0.0.dylib", "libSDL3.0.dylib", "libSDL3.dylib", "libMoltenVK.dylib", "libvulkan.1.dylib")


class DependencyError(ValueError):
    pass


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while data := f.read(1 << 20):
            h.update(data)
    return h.hexdigest()


def version(value):
    try:
        parts = tuple(int(x) for x in value.split("."))
        if not 1 <= len(parts) <= 3 or any(x < 0 for x in parts):
            raise ValueError
        return parts + (0,) * (3 - len(parts))
    except (ValueError, AttributeError):
        raise DependencyError("invalid macOS version: %r" % value) from None


def _packed_version(value):
    return "%d.%d.%d" % (value >> 16, (value >> 8) & 255, value & 255)


def _read(f, offset, count, end):
    if offset < 0 or count < 0 or offset + count > end:
        raise DependencyError("truncated Mach-O/archive metadata")
    f.seek(offset)
    data = f.read(count)
    if len(data) != count:
        raise DependencyError("truncated Mach-O/archive metadata")
    return data


def _name(command, offset):
    if not 8 <= offset < len(command) or b"\0" not in command[offset:]:
        raise DependencyError("invalid Mach-O load-command string")
    return command[offset:].split(b"\0", 1)[0].decode("utf-8")


def _payload_digest(f, start, length):
    h = hashlib.sha256()
    end = start + length
    while start < end:
        data = _read(f, start, min(1 << 20, end - start), end)
        h.update(data)
        start += len(data)
    return h.hexdigest()


def _inspect(f, start, length, arch, read_symbols=False):
    end = start + length
    magic = _read(f, start, 8, end)
    if magic == b"!<arch>\n":
        position, members, strings = start + 8, [], b""
        while position < end:
            header = _read(f, position, 60, end)
            if header[58:] != b"`\n":
                raise DependencyError("invalid archive member header")
            name = header[:16].decode("ascii").strip()
            size = int(header[48:58])
            body, count = position + 60, size
            if name.startswith("#1/"):
                n = int(name[3:])
                name = _read(f, body, n, body + count).rstrip(b"\0").decode("utf-8")
                body, count = body + n, count - n
            elif name == "//":
                strings = _read(f, body, count, end)
            elif name.startswith("/") and name[1:].isdigit():
                name = strings[int(name[1:]):].split(b"/\n", 1)[0].decode("utf-8")
            if name not in ("/", "//", "/SYM64/") and not name.startswith("__.SYMDEF"):
                member = _inspect(f, body, count, arch, read_symbols=True)
                if member["kind"] != "object":
                    raise DependencyError("archive member is not a target Mach-O object: " + name)
                member.update(ordinal=len(members), name=name.removesuffix("/"), sha256=_payload_digest(f, body, count))
                members.append(member)
            position += 60 + size + (size & 1)
        if position != end or not members:
            raise DependencyError("empty or malformed Mach-O archive")
        floors = [m["minimum_os"] for m in members if m["minimum_os"] is not None]
        return {"kind": "archive", "arch": arch, "minimum_os": max(floors, key=version) if floors else None,
                "members": len(members), "archive_members": members, "loads": [], "rpaths": [], "id": ""}
    fat = {b"\xca\xfe\xba\xbe": (">", False), b"\xbe\xba\xfe\xca": ("<", False),
           b"\xca\xfe\xba\xbf": (">", True), b"\xbf\xba\xfe\xca": ("<", True)}
    if magic[:4] in fat:
        endian, wide = fat[magic[:4]]
        n = struct.unpack(endian + "I", magic[4:])[0]
        width = 32 if wide else 20
        if not n or n > (length - 8) // width:
            raise DependencyError("invalid Mach-O slice table")
        for i in range(n):
            data = _read(f, start + 8 + i * width, width, end)
            cpu, subtype = struct.unpack_from(endian + "II", data)
            offset, size = struct.unpack_from(endian + ("QQ" if wide else "II"), data, 8)
            if cpu == CPU[arch] and (subtype & 0xffffff) in ((0, 1) if arch == "arm64" else (3,)):
                if offset < 8 + n * width or offset + size > length:
                    raise DependencyError("Mach-O slice extends past the input or overlaps its header")
                return _inspect(f, start + offset, size, arch, read_symbols)
        raise DependencyError("no baseline %s Mach-O slice" % arch)
    if magic[:4] != b"\xcf\xfa\xed\xfe":
        raise DependencyError("not a 64-bit little-endian Mach-O input for " + arch)
    _, cpu, subtype, filetype, ncmds, sizeofcmds, flags, _ = struct.unpack("<8I", _read(f, start, 32, end))
    if cpu != CPU[arch] or (subtype & 0xffffff) not in ((0, 1) if arch == "arm64" else (3,)):
        raise DependencyError("not a baseline %s Mach-O slice (CPU %#x, subtype %#x)" % (arch, cpu, subtype))
    kinds = {1: "object", 2: "executable", 6: "dylib"}
    if filetype not in kinds:
        raise DependencyError("unsupported Mach-O file type %d" % filetype)
    if filetype != 1 and not flags & 0x80:  # MH_TWOLEVEL
        raise DependencyError("Mach-O image is not two-level namespace")
    commands = _read(f, start + 32, sizeofcmds, end)
    position, floor, loads, rpaths, install_id, symtab = 0, None, [], [], "", None
    code_sections, section_count, instruction_bytes = set(), 0, 0
    for _ in range(ncmds):
        if position + 8 > len(commands):
            raise DependencyError("truncated Mach-O load command")
        cmd, size = struct.unpack_from("<II", commands, position)
        if size < 8 or position + size > len(commands):
            raise DependencyError("invalid Mach-O load command size")
        data = commands[position:position + size]
        if cmd == 0x19:  # LC_SEGMENT_64: identify actual externally visible instruction-section entries
            sections = struct.unpack_from("<I", data, 64)[0]
            if 72 + 80 * sections > len(data):
                raise DependencyError("truncated Mach-O section table")
            for i in range(sections):
                at = 72 + i * 80
                section_count += 1
                section_flags = struct.unpack_from("<I", data, at + 64)[0]
                if section_flags & (0x80000000 | 0x400):
                    code_sections.add(section_count)
                    instruction_bytes += struct.unpack_from("<Q", data, at + 40)[0]
        elif cmd == 0x32:  # LC_BUILD_VERSION
            platform, minimum = struct.unpack_from("<II", data, 8)
            if floor is not None:
                raise DependencyError("multiple Mach-O deployment declarations")
            if platform != 1:
                raise DependencyError("Mach-O platform is not macOS (%d)" % platform)
            floor = _packed_version(minimum)
        elif cmd == 0x24:  # LC_VERSION_MIN_MACOSX
            if floor is not None:
                raise DependencyError("multiple Mach-O deployment declarations")
            floor = _packed_version(struct.unpack_from("<I", data, 8)[0])
        elif cmd in (0x25, 0x2f, 0x30):
            raise DependencyError("Mach-O deployment command is not macOS")
        elif cmd in LOAD_DYLIB or cmd == 0xd:
            name = _name(data, struct.unpack_from("<I", data, 8)[0])
            if cmd == 0xd:
                install_id = name
            else:
                loads.append(name)
        elif cmd == 0x8000001c:
            rpaths.append(_name(data, struct.unpack_from("<I", data, 8)[0]))
        elif cmd == 2:  # LC_SYMTAB; undefined dynamic-lookup ordinals evade the strict link contract
            symtab = struct.unpack_from("<4I", data, 8)
        position += size
    if position != len(commands):
        raise DependencyError("inconsistent Mach-O load-command table")
    if floor is None and filetype != 1:
        raise DependencyError("no declared macOS minimum")
    if floor is not None and version(floor) > version(MINIMUM_OS):
        raise DependencyError("requires macOS %s, newer than target %s" % (floor, MINIMUM_OS))
    if filetype == 6 and not install_id:
        raise DependencyError("dylib has no install name")
    defined, undefined, entries = set(), set(), set()
    if symtab and (filetype != 1 or read_symbols):
        offset, count, stroff, strsize = symtab
        symbols = _read(f, start + offset, count * 16, end)
        strings = _read(f, start + stroff, strsize, end) if read_symbols else b""
        for i in range(count):
            string_index, kind, section, desc, value = struct.unpack_from("<IBBHQ", symbols, i * 16)
            if kind & 0xe0 or not kind & 1:  # debugging/local symbols are not external bindings
                continue
            is_undefined = kind & 0x0e == 0 and not value
            if filetype != 1 and is_undefined and desc >> 8 in (0xfe, 0xff):
                raise DependencyError("unbound dynamic-lookup/executable import in Mach-O image; use explicit host services")
            if read_symbols:
                name_end = strings.find(b"\0", string_index)
                if string_index >= len(strings) or name_end < 0:
                    raise DependencyError("invalid Mach-O symbol string")
                name = strings[string_index:name_end].decode("utf-8")
                (undefined if is_undefined else defined).add(name)
                if not is_undefined and section in code_sections:
                    entries.add(name)
    result = {"kind": kinds[filetype], "arch": arch, "minimum_os": floor, "loads": loads,
              "rpaths": rpaths, "id": install_id}
    if read_symbols:
        result.update(defined_symbols=sorted(defined), undefined_symbols=sorted(undefined), entry_symbols=sorted(entries),
                      instruction_bytes=instruction_bytes)
    return result


def raw_metadata(path, arch, symbols=False):
    """Read identity/symbol metadata. An unstamped OBJECT is data here, not an admitted input."""
    if arch not in CPU:
        raise DependencyError("unsupported macOS architecture: " + arch)
    try:
        with open(path, "rb") as f:
            return _inspect(f, 0, os.fstat(f.fileno()).st_size, arch, symbols)
    except (OSError, ValueError, UnicodeError, struct.error) as e:
        raise DependencyError("%s: %s" % (path, e)) from e


def inspect(path, arch, qualifications=None):
    """Admit load-command-proven inputs, or precisely receipt-qualified archive members."""
    info = raw_metadata(path, arch)
    if info["kind"] == "archive":
        unstamped = [m for m in info["archive_members"] if m["minimum_os"] is None]
        if unstamped:
            if qualifications is None:
                raise DependencyError("%s: no declared macOS minimum for archive member %d (%s); an exact reviewed "
                                      "source/command/import/ISA receipt is required" %
                                      (path, unstamped[0]["ordinal"], unstamped[0]["name"]))
            qualifications.archive(path, info)
            info["minimum_os"] = MINIMUM_OS
            info["deployment_evidence"] = "load commands plus receipt-qualified unversioned assembly"
        else:
            info["deployment_evidence"] = "load commands"
        info["unversioned_qualified"] = len(unstamped)
    elif info["minimum_os"] is None:
        raise DependencyError("%s: no declared macOS minimum" % path)
    return info


def system_library(path):
    return os.path.normpath(path).startswith(("/usr/lib/", "/System/Library/"))


def _expand(path, loader, executable):
    for prefix, base in (("@loader_path", loader), ("@executable_path", executable)):
        if path == prefix or path.startswith(prefix + "/"):
            return os.path.join(base, path[len(prefix):].lstrip("/")) if base else None
    return path if os.path.isabs(path) else None


class Plan:
    """Exact inputs used by both preflight and production links, not a search-at-copy-time list."""
    def __init__(self, arch, sdk, executable_dir=""):
        self.arch, self.sdk, self.executable_dir = arch, Path(sdk), str(executable_dir)
        self.files: dict[str, dict] = {}
        self.names: dict[str, str] = {}
        self.search: list[str] = []
        self.ffmpeg: tuple[list[str], list[str]] | None = None
        self.sdl: tuple[list[str], list[str]] | None = None
        self.aliases: dict[str, str] = {}
        self.header_key = b""
        from darwin_archive import from_environment
        self.qualifications = from_environment(arch)

    def alias(self, name, target):
        if name != Path(name).name or name in ("", ".", "..", MANIFEST) or name in self.names:
            raise DependencyError("invalid or colliding runtime alias: " + name)
        if target not in self.names or self.files[self.names[target]]["kind"] != "dylib":
            raise DependencyError("runtime alias must name an admitted dylib: " + target)
        self.aliases[name] = target
        self.names[name] = self.names[target]

    def _resolve(self, name, source, rpaths):
        if system_library(name):
            return None
        if name.startswith("@rpath/"):
            suffix = name[len("@rpath/"):]
            candidates = [os.path.join(p, suffix) for p in rpaths + self.search]
        else:
            expanded = _expand(name, str(source.parent), self.executable_dir)
            candidates = [expanded] if expanded else []
        found = list(dict.fromkeys(str(Path(p).resolve()) for p in candidates if p and os.path.isfile(p)))
        if len(found) != 1:
            raise DependencyError("%s: %s dependency %s" % (source, "ambiguous" if found else "unresolved", name))
        return found[0]

    def add(self, path, name=None, inherited=()):
        source = Path(path).resolve(strict=True)
        key = str(source)
        if key in self.files:
            if name and name != self.files[key]["name"]:
                previous = self.files[key]["name"]
                if name in self.names and self.names[name] != key:
                    raise DependencyError("colliding packaged library name: " + name)
                del self.names[previous]
                self.names[name] = key
                self.files[key]["name"] = name
            return key
        info = inspect(source, self.arch, self.qualifications)
        name = name or (Path(info["id"]).name if info["id"] else source.name)
        if name != Path(name).name or name in ("", ".", "..", MANIFEST):
            raise DependencyError("invalid packaged filename: " + name)
        if name in self.names and self.names[name] != key:
            raise DependencyError("colliding packaged library name: " + name)
        self.names[name] = key
        item: dict = dict(info, name=name, sha256=digest(source), dependencies={})
        self.files[key] = item
        local = [_expand(p, str(source.parent), self.executable_dir) for p in info["rpaths"]]
        if any(p is None for p in local):
            raise DependencyError("unsupported relative rpath in " + key)
        rpaths = list(dict.fromkeys(local + list(inherited)))
        for dependency in info["loads"]:
            resolved = self._resolve(dependency, source, rpaths)
            if resolved:
                key_of_dependency = self.add(resolved, inherited=rpaths)
                if self.files[key_of_dependency]["kind"] != "dylib":
                    raise DependencyError("runtime load is not a dylib: " + resolved)
                item["dependencies"][dependency] = key_of_dependency
        return key

    def pin_link_flags(self, flags):
        """Resolve every -l against the chosen prefix/SDK and link that exact file."""
        directories, frameworks, rest = [], [], []
        it = iter(flags)
        for flag in it:
            if flag in ("-L", "-F"):
                target = next(it, None)
                if not target:
                    raise DependencyError("missing search path after " + flag)
                (directories if flag == "-L" else frameworks).append(target)
            elif flag.startswith(("-L", "-F")):
                (directories if flag[:2] == "-L" else frameworks).append(flag[2:])
            else:
                rest.append(flag)
        self.search += [str(Path(p).resolve()) for p in directories if str(Path(p).resolve()) not in self.search]
        pinned, inputs = [], []
        it = iter(rest)
        for flag in it:
            if flag == "-framework":
                name = next(it, "")
                if not name or "/" in name:
                    raise DependencyError("invalid -framework name")
                for directory in frameworks:
                    if (Path(directory) / (name + ".framework")).exists():
                        raise DependencyError("non-system framework packaging is not supported: " + name)
                stub = self.sdk / "System/Library/Frameworks" / (name + ".framework") / (name + ".tbd")
                if not stub.is_file():
                    raise DependencyError("missing SDK framework: " + str(stub))
                pinned.append(str(stub))
                continue
            if flag.startswith("-l"):
                name = flag[2:]
                if not name or "/" in name:
                    raise DependencyError("invalid -l library name")
                selected = next((Path(d) / ("lib" + name + suffix) for d in directories for suffix in (".dylib", ".a")
                                 if (Path(d) / ("lib" + name + suffix)).is_file()), None)
                if selected is None:
                    stub = self.sdk / "usr/lib" / ("lib" + name + ".tbd")
                    if not stub.is_file():
                        raise DependencyError("unresolved link library " + flag + " in selected prefix or SDK")
                    pinned.append(str(stub))
                    continue
            elif os.path.isabs(flag) and flag.endswith((".a", ".dylib")):
                selected = Path(flag)
            elif flag == "-pthread":
                pinned.append(flag)
                continue
            else:
                raise DependencyError("unsupported dependency linker argument: " + flag)
            key = self.add(selected)
            if self.files[key]["kind"] not in ("archive", "dylib"):
                raise DependencyError("link input is not an archive or dylib: " + key)
            pinned.append(key)
            inputs.append(key)
        return pinned, inputs

    def unchanged(self):
        if self.qualifications:
            self.qualifications.unchanged()
        for path, item in self.files.items():
            if digest(path) != item["sha256"]:
                raise DependencyError("dependency changed after admission: " + path)


def _run(tool, *args):
    import toolchain  # pyright: ignore[reportMissingImports] -- kit.py supplies the vendored tools path at runtime
    command = [toolchain.need(toolchain.find(tool)), *map(str, args)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise DependencyError("%s failed (%d): %s" % (tool, result.returncode, (result.stdout + result.stderr).strip()))


def package(out, plan, executable="longshot", renderer="longshot_vk.dylib"):
    """Relocate only copied files. A failed admission/rewrite/sign leaves inputs untouched."""
    out = Path(out).resolve()
    plan.unchanged()
    for name, kind in ((executable, "executable"), (renderer, "dylib")):
        key = str((out / name).resolve(strict=True))
        previous_input = plan.names.get(name)
        if previous_input and previous_input != key:
            # build.py copied an admitted prebuilt renderer; its bytes must still be exactly those admitted.
            if digest(key) != plan.files[previous_input]["sha256"]:
                raise DependencyError("prebuilt product differs from its admitted input: " + name)
            plan.files[key] = plan.files.pop(previous_input)
            plan.names[name] = key
            for item in plan.files.values():
                item["dependencies"] = {n: key if p == previous_input else p for n, p in item["dependencies"].items()}
        else:
            plan.add(key, name)
        if plan.files[key]["kind"] != kind:
            raise DependencyError("wrong product type: " + name)
    with tempfile.TemporaryDirectory(prefix=".macos-package-", dir=out) as temporary:
        staged = Path(temporary)
        products = {p: item for p, item in plan.files.items() if item["kind"] != "archive"}
        for path, item in products.items():
            target = staged / item["name"]
            # A universal input may carry a newer/foreign other slice. Ship only the qualified target slice.
            _run("lipo", path, "-thin", plan.arch, "-output", target) if _is_fat(path) else shutil.copy2(path, target)
            target.chmod(target.stat().st_mode | 0o200)
            changes = []
            if item["kind"] == "dylib":
                changes += ["-id", "@rpath/" + item["name"]]
            for old, dependency in item["dependencies"].items():
                changes += ["-change", old, "@loader_path/" + plan.files[dependency]["name"]]
            for rpath in dict.fromkeys(item["rpaths"]):
                if rpath not in ("@loader_path", "@executable_path"):
                    changes += ["-delete_rpath", rpath]
            if changes:
                _run("install_name_tool", *changes, target)
            _run("codesign", "--force", "--sign", "-", "--timestamp=none", target)
            _run("codesign", "--verify", "--strict", target)
        records = {}
        for path, item in products.items():
            target = staged / item["name"]
            info = inspect(target, plan.arch)
            for name in info["loads"]:
                if not system_library(name) and (not name.startswith("@loader_path/") or
                                                 not (staged / name[len("@loader_path/"):]).is_file()):
                    raise DependencyError("package still depends on an external path: " + name)
            records[item["name"]] = {"sha256": digest(target), "input_sha256": item["sha256"],
                                     "kind": info["kind"], "minimum_os": info["minimum_os"]}
        for alias, target in plan.aliases.items():
            (staged / alias).symlink_to(target)
            records[alias] = dict(records[target], kind="alias", target=target)
        plan.unchanged()
        manifest = {"schema": 1, "arch": plan.arch, "minimum_os": MINIMUM_OS, "files": records}
        (staged / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        previous = read_manifest(out, optional=True)
        check_destination(out, manifest, previous)
        for name in sorted(records) + [MANIFEST]:
            os.replace(staged / name, out / name)
        remove_stale(out, previous, manifest)
    return manifest


def _is_fat(path):
    with open(path, "rb") as f:
        return f.read(4) in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")


def read_manifest(folder, optional=False):
    path = Path(folder) / MANIFEST
    if optional and not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["schema"] != 1 or data["arch"] not in CPU or data["minimum_os"] != MINIMUM_OS or not data["files"]:
            raise ValueError("unknown manifest identity")
        for name, item in data["files"].items():
            if name != Path(name).name or name in ("", ".", "..", MANIFEST) or len(item["sha256"]) != 64:
                raise ValueError("invalid packaged file record")
            if item.get("kind") == "alias" and (item.get("target") not in data["files"] or
                                                  data["files"][item["target"]].get("kind") != "dylib"):
                raise ValueError("invalid package alias target")
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise DependencyError("invalid macOS package manifest %s: %s" % (path, e)) from e


def package_files(folder):
    data = read_manifest(folder)
    for name, item in data["files"].items():
        path = Path(folder) / name
        if item.get("kind") == "alias" and (not path.is_symlink() or os.readlink(path) != item["target"]):
            raise DependencyError("package alias changed before deployment: " + name)
        if digest(Path(folder) / name) != item["sha256"]:
            raise DependencyError("packaged file changed before deployment: " + name)
    return sorted(data["files"]) + [MANIFEST]


def check_destination(folder, current, previous=None):
    """A leftover SDL3 or loader must not override the newly admitted runtime."""
    for name in RUNTIME_NAMES:
        path = Path(folder) / name
        if name not in current["files"] and os.path.lexists(path):
            old = (previous or {}).get("files", {}).get(name)
            if not old or not path.is_file() or digest(path) != old["sha256"]:
                raise DependencyError("unmanaged or modified runtime would shadow this package: " + str(path))


def remove_stale(folder, previous, current):
    # Remove only unchanged files owned by our previous manifest, never arbitrary user dylibs.
    for name, item in (previous or {}).get("files", {}).items():
        path = Path(folder) / name
        if name not in current["files"] and path.is_file() and digest(path) == item["sha256"]:
            path.unlink()
