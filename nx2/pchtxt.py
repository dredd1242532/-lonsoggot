"""Compile active ExeFS .pchtxt records into the native image before lifting.

Only text patch files are read here. This does not apply IPS files or load
replacement subsdk modules from a mod's ExeFS directory.
"""
import os
import re


def _active_mods(root):
    """Match the runtime's active mod-folder walk and sort order."""
    found = []

    def walk(folder, name, depth):
        has_mod_content = any(os.path.isdir(os.path.join(folder, part)) for part in ("romfs", "exefs"))
        if has_mod_content and depth > 0:
            found.append((name, folder))
            return
        if depth >= 4:
            return
        try:
            with os.scandir(folder) as scanner:
                children = sorted(scanner, key=lambda entry: entry.name.casefold())
        except OSError:
            return
        for entry in children:
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            if entry.name.startswith(("-", "_")):
                continue
            child_name = entry.name if not name else name + "/" + entry.name
            walk(entry.path, child_name, depth + 1)

    if root and os.path.isdir(root):
        walk(root, "", 0)
    return sorted(found, key=lambda item: item[0].casefold())


def _patch_files(exefs):
    found = []

    def on_walk_error(_error):
        return

    for current, dirs, files in os.walk(exefs, followlinks=False, onerror=on_walk_error):
        depth = os.path.relpath(current, exefs).count(os.sep)
        if depth >= 8:
            dirs[:] = []
        else:
            dirs[:] = sorted((d for d in dirs if not os.path.islink(os.path.join(current, d))), key=str.casefold)
        found.extend(os.path.join(current, name) for name in sorted(files, key=str.casefold)
                     if name.lower().endswith(".pchtxt"))
    return found


def _bytes(encoded):
    out = bytearray()
    for token in re.split(r"\s+", encoded.strip()):
        if not token:
            continue
        if token == "//" or token.startswith(("//", "#")):
            break
        if token.lower().startswith("0x"):
            token = token[2:]
        if not token or len(token) & 1 or not re.fullmatch(r"[0-9a-fA-F]+", token):
            raise ValueError("invalid pchtxt byte sequence")
        out.extend(bytes.fromhex(token))
        if len(out) > 1024 * 1024:
            raise ValueError("pchtxt record is too large")
    if not out:
        raise ValueError("empty pchtxt record")
    return bytes(out)


def _records(path):
    module_id = None
    shift = 0
    enabled = False
    records = []
    bad = False
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(("#", "//")):
                continue
            if line.startswith("@nsobid-"):
                module_id = line[8:].strip().lower()
                continue
            if line.startswith("@flag"):
                fields = line[5:].split()
                if len(fields) == 2 and fields[0].lower() == "offset_shift":
                    try:
                        shift = int(fields[1], 0)
                    except ValueError:
                        bad = True
                continue
            if line.startswith("@enabled"):
                enabled = True
                continue
            if line.startswith(("@disabled", "@stop")):
                enabled = False
                continue
            if line.startswith("@") or not enabled:
                continue
            match = re.match(r"^(?:0[xX])?([0-9a-fA-F]+)\s+(.+)$", line)
            if not match:
                bad = True
                continue
            try:
                records.append((module_id, int(match.group(1), 16), shift, _bytes(match.group(2))))
            except ValueError:
                bad = True
    return records, bad


def _active_pchtxt_files(root):
    for name, mod_dir in _active_mods(root):
        exefs = os.path.join(mod_dir, "exefs")
        if os.path.isdir(exefs):
            for path in _patch_files(exefs):
                yield name, path


def _record_address(module, offset, shift, data):
    """Map an NSO pchtxt record to the loaded module image.

    Most records address bytes in an NSO file-backed segment, so offset_shift
    and the segment's file/memory offsets determine their loaded address. Some
    patches also initialize constants in zero-filled image gaps (or BSS); in
    that case there is no file segment to translate and the record's offset is
    already the module-image offset.
    """
    image_size = len(module.image)
    if offset >= 0 and len(data) <= image_size - offset:
        end = offset + len(data)
        in_segment = any(offset < moff + size and end > moff
                         for _foff, moff, size, _name in module.file_segments)
        if not in_segment:
            return module.base + offset

    text_offset = next((foff for foff, _moff, _size, name in module.file_segments if name == ".text"), 0x100)
    if shift != text_offset and text_offset > 0x100 and shift >= 0x100:
        shift += text_offset - 0x100
    file_offset = offset + shift
    segment = next(((foff, moff, size) for foff, moff, size, _name in module.file_segments
                    if file_offset >= foff and file_offset - foff <= size and
                    len(data) <= size - (file_offset - foff)), None)
    if segment is None:
        return None
    foff, moff, _size = segment
    return module.base + moff + file_offset - foff


def apply_to_image(img, mods_root):
    """Patch the relocated image and return the number of records and bytes applied."""
    import nx_image

    modules = {m.build_id.lower(): m for m in img.modules}
    applied_records = applied_bytes = 0
    for mod_name, path in _active_pchtxt_files(mods_root):
        records, bad = _records(path)
        prepared = []
        for module_id, offset, shift, data in records:
            module = modules.get(module_id or "")
            if module is None:
                bad = True
                continue
            address = _record_address(module, offset, shift, data)
            if address is None:
                bad = True
                continue
            start = address - nx_image.MAIN_BASE
            end = start + len(data)
            if start < 0 or end > len(img.mem):
                bad = True
                continue
            prepared.append((start, end, data))
        if bad:
            if records:
                print("pchtxt: %s: %s has invalid records; none of its patches were compiled" %
                      (mod_name, os.path.basename(path)), flush=True)
            continue
        if prepared:
            for start, end, data in prepared:
                img.mem[start:end] = data
                applied_bytes += len(data)
            applied_records += len(prepared)
    if applied_records:
        img._entries()  # rebuild entry candidates after patch bytes change control flow
    return applied_records, applied_bytes
