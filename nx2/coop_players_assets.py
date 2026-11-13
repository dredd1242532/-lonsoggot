"""Build co-op actors from the user's own RomFS; output is game data, never distributed.

The menu actor supplies the model without a second player controller. Its world counterpart
supplies animation references, settings and heap allowance. Its primary command is bound to
the existing full-body mask: this passive actor has no player controller to fill the upper body.
No model, animation or extracted bone list lives here.
"""
import argparse
import copy
from pathlib import Path
import re
import struct
import sys
import zlib


class Scalar:
    """An unchanged BYML scalar, including references to opaque data in the original file."""
    def __init__(self, kind, word):
        self.kind, self.word = kind, word


class Byml:
    """Append edited containers to a BYML, retaining scalar types and opaque payloads verbatim."""
    def __init__(self, data):
        self.data = bytes(data)
        if data[:2] not in (b'YB', b'BY') or len(data) < 16:
            raise ValueError('not a BYML')
        self.endian = '<' if data[:2] == b'YB' else '>'
        self.order = 'little' if self.endian == '<' else 'big'
        self.keys = self.table(self.u32(4))
        self.strings = self.table(self.u32(8))
        self.root = self.node(self.u32(12), set())

    def u32(self, at):
        return struct.unpack_from(self.endian + 'I', self.data, at)[0]

    def count(self, at):
        if at + 4 > len(self.data):
            raise ValueError('short BYML node')
        return int.from_bytes(self.data[at + 1:at + 4], self.order)

    def table(self, at):
        if not at:
            return []
        if self.data[at] != 0xc2:
            raise ValueError('not a BYML string table')
        return [self.data[at + self.u32(at + 4 + i * 4):
                          self.data.index(0, at + self.u32(at + 4 + i * 4))].decode('utf-8')
                for i in range(self.count(at))]

    def value(self, kind, word, ancestors):
        if kind == 0xa0:
            return self.strings[word]
        if kind in (0xc0, 0xc1):
            return self.node(word, ancestors)
        # Other node forms and out-of-line scalars are not changed: their offsets stay valid.
        return Scalar(kind, word)

    def node(self, at, ancestors):
        if at in ancestors or len(ancestors) > 100:
            raise ValueError('recursive BYML')
        ancestors = ancestors | {at}
        n = self.count(at)
        if self.data[at] == 0xc0:
            values = at + 4 + ((n + 3) & ~3)
            return [self.value(self.data[at + 4 + i], self.u32(values + i * 4), ancestors) for i in range(n)]
        if self.data[at] == 0xc1:
            out = {}
            for i in range(n):
                p = at + 4 + i * 8
                key = self.keys[int.from_bytes(self.data[p:p + 3], self.order)]
                out[key] = self.value(self.data[p + 3], self.u32(p + 4), ancestors)
            return out
        raise ValueError('not an editable BYML container')

    def encode(self):
        out = bytearray(self.data)
        strings = list(self.strings)
        string_ids = {s: i for i, s in enumerate(strings)}
        key_ids = {s: i for i, s in enumerate(self.keys)}

        def align():
            out.extend(bytes(-len(out) % 4))
            return len(out)

        def word(v):
            return struct.pack(self.endian + 'I', v)

        def value(v):
            if isinstance(v, Scalar):
                return v.kind, v.word
            if isinstance(v, str):
                if v not in string_ids:
                    string_ids[v] = len(strings)
                    strings.append(v)
                return 0xa0, string_ids[v]
            kind = 0xc1 if isinstance(v, dict) else 0xc0 if isinstance(v, list) else 0
            if not kind:
                raise ValueError('unsupported edited BYML value')
            items = sorted(v.items(), key=lambda p: key_ids[p[0]]) if kind == 0xc1 else list(enumerate(v))
            children = [(k, *value(x)) for k, x in items]
            pos = align()
            out.extend(bytes([kind]) + len(children).to_bytes(3, self.order))
            if kind == 0xc0:
                out.extend(bytes(t for _, t, _ in children))
                align()
                for _, _, w in children:
                    out.extend(word(w))
            else:
                for k, t, w in children:
                    out.extend(key_ids[k].to_bytes(3, self.order) + bytes([t]) + word(w))
            return kind, pos

        _, root = value(self.root)
        pos = align()
        text = bytearray()
        offsets = []
        for s in strings:
            offsets.append(4 + (len(strings) + 1) * 4 + len(text))
            text.extend(s.encode('utf-8') + b'\0')
        offsets.append(4 + (len(strings) + 1) * 4 + len(text))
        out.extend(b'\xc2' + len(strings).to_bytes(3, self.order))
        for off in offsets:
            out.extend(word(off))
        out.extend(text)
        struct.pack_into(self.endian + 'II', out, 8, pos, root)
        return bytes(out)


def sarc_read(data):
    if data[:4] != b'SARC' or data[6:8] not in (b'\xff\xfe', b'\xfe\xff'):
        raise ValueError('not a SARC')
    endian = '<' if data[6:8] == b'\xff\xfe' else '>'
    header, = struct.unpack_from(endian + 'H', data, 4)
    start, = struct.unpack_from(endian + 'I', data, 12)
    n, = struct.unpack_from(endian + 'H', data, header + 6)
    names = header + 12 + 16 * n + 8
    entries = {}
    for i in range(n):
        attr, lo, hi = struct.unpack_from(endian + 'III', data, header + 16 + i * 16)
        name = names + (attr & 0xffffff) * 4
        if attr >> 24 != 1 or start + hi > len(data) or lo > hi:
            raise ValueError('invalid SARC entry')
        key = data[name:data.index(0, name)].decode('utf-8')
        entries[key] = data[start + lo:start + hi]
    return entries


def sarc_write(entries):
    def name_hash(name):
        h = 0
        for c in name.encode('utf-8'):
            h = (h * 0x65 + c) & 0xffffffff
        return h
    names, body, nodes = bytearray(), bytearray(), bytearray()
    for name in sorted(entries, key=lambda s: (name_hash(s), s)):
        names.extend(bytes(-len(names) % 4))
        attr = 0x1000000 | (len(names) // 4)
        names.extend(name.encode('utf-8') + b'\0')
        body.extend(bytes(-len(body) % 8))
        start = len(body)
        body.extend(entries[name])
        nodes.extend(struct.pack('<IIII', name_hash(name), attr, start, len(body)))
    header = bytearray(struct.pack('<4sHHIIHH', b'SARC', 20, 0xfeff, 0, 0, 0x100, 0))
    header.extend(struct.pack('<4sHHI', b'SFAT', 12, len(entries), 0x65) + nodes)
    header.extend(struct.pack('<4sHH', b'SFNT', 8, 0) + names)
    header.extend(bytes(-len(header) % 8))
    struct.pack_into('<II', header, 8, len(header) + len(body), len(header))
    return bytes(header + body)


class Sizes:
    def __init__(self, data):
        if data[:6] != b'RESTBL':
            raise ValueError('not a resource size table')
        self.header = data[:14]
        n, named = struct.unpack_from('<II', data, 14)
        self.values = dict(struct.unpack_from('<II', data, 22 + i * 8) for i in range(n))
        self.named = named
        self.rest = data[22 + n * 8:]

    def grow(self, source, dest, old, new):
        src = zlib.crc32(source.removesuffix('.zs').encode())
        dst = zlib.crc32(dest.removesuffix('.zs').encode())
        if src not in self.values:
            raise ValueError('source resource has no heap allowance: ' + source)
        # Keep the source allocation's overhead and room for the added container nodes.
        want = self.values[src] + ((max(0, new - old) + 31) & ~31) + 4096
        if want > 0xffffffff:
            raise ValueError('resource allocation too large')
        self.values[dst] = max(self.values.get(dst, 0), want)

    def encode(self):
        return (self.header + struct.pack('<II', len(self.values), self.named) +
                b''.join(struct.pack('<II', k, v) for k, v in sorted(self.values.items())) + self.rest)


class Romfs:
    def __init__(self, root):
        import zstandard
        self.root, self.zstd = Path(root), zstandard
        raw = zstandard.ZstdDecompressor().decompressobj().decompress((self.root / 'Pack/ZsDic.pack.zs').read_bytes())
        self.dicts = {}
        for blob in sarc_read(raw).values():
            d = zstandard.ZstdCompressionDict(blob)
            self.dicts[d.dict_id()] = d

    def read(self, rel):
        data = (self.root / rel).read_bytes()
        if not rel.endswith('.zs'):
            return data
        d = self.dicts.get(self.zstd.get_frame_parameters(data).dict_id)
        return self.zstd.ZstdDecompressor(dict_data=d).decompressobj().decompress(data)

    def one(self, pattern):
        paths = sorted(p for p in self.root.glob(pattern) if p.is_file())
        if len(paths) != 1:
            raise ValueError('expected one ' + pattern)
        return paths[0].relative_to(self.root).as_posix()


def asset_files():
    """Read the same filename contract compiled into the runtime loader."""
    path = Path(__file__).resolve().parents[1] / 'host/coop_players_assets.h'
    text = re.sub(r'/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)
    files = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        entry = re.fullmatch(r'COOP_PLAYER_ASSET\((actor|animation|table), (\w+), "([^"]+)"\)', line.strip())
        if not entry or entry[2] in files:
            raise ValueError('invalid co-op asset filename contract: ' + line)
        files[entry[2]] = entry[1], entry[3]
    if not files:
        raise ValueError('empty co-op asset filename contract')
    return files


def body_animation(data):
    """Bind the primary Lower slot to the source graph's full-body mask (ASB 0x41b).

    Commands, clips, other partials and their overlays are unchanged. This is a base-body
    pose, not the real player's controller-driven upper-body/IK/equipment composition.
    """
    if len(data) < 0x80 or data[:8] != b'ASB \x1b\x04\x00\x00':
        raise ValueError('unsupported co-op animation graph')
    word = lambda at: struct.unpack_from('<I', data, at)[0]
    pool, pool_size = word(0x3c), word(0x60)
    if pool < 0x80 or pool_size > len(data) - pool:
        raise ValueError('invalid co-op animation string pool')

    def text(offset):
        if offset >= pool_size:
            raise ValueError('invalid co-op animation name')
        end = data.find(b'\0', pool + offset, pool + pool_size)
        if end < 0:
            raise ValueError('unterminated co-op animation name')
        return data[pool + offset:end]

    def table(count_at, start_at, stride):
        count, start = word(count_at), word(start_at)
        if not count or start < 0x80 or start > len(data) or count > (len(data) - start) // stride:
            raise ValueError('invalid co-op animation table')
        return range(start, start + count * stride, stride)

    partials = table(0x24, 0x28, 0x10)
    skeletons = table(0x58, 0x5c, 0x0c)
    primary = [at for at in partials if text(word(at)) == b'Lower']
    full = [i for i, at in enumerate(skeletons) if text(word(at + 4)) == b'All_PB']
    if primary != [partials.start] or len(full) != 1 or word(primary[0] + 4) >= len(skeletons):
        raise ValueError('co-op animation needs a primary Lower slot and one full-body mask')
    out = bytearray(data)
    struct.pack_into('<I', out, primary[0] + 4, full[0])
    return bytes(out)


def generate(rom, out):
    """The output tree is private builder output, not a mod to be distributed."""
    contract = asset_files()
    required = {name: rom.one(path) if '*' in path else path for name, (_, path) in contract.items()}
    tables = [required[name] for name in ('actor_info', 'game_actor_info')]
    table_docs = [Byml(rom.read(path)) for path in tables]
    size_path = required['resource_sizes']
    sizes = Sizes(rom.read(size_path))
    changed = {}
    for age, (kind, _) in contract.items():
        if kind != 'actor':
            continue
        menu, world, puppet = 'PouchPlayer_' + age, 'Player_' + age, 'CoopPuppet_' + age
        menu_path, world_path = ['Pack/Actor/' + n + '.pack.zs' for n in (menu, world)]
        source = rom.read(menu_path)
        pack, player = sarc_read(source), sarc_read(rom.read(world_path))
        param = lambda n: 'Actor/' + n + '.engine__actor__ActorParam.bgyml'
        doc = Byml(pack[param(menu)])
        world_doc = Byml(player[param(world)])
        components = doc.root['Components']
        def packed_ref(ref):
            return ref.lstrip('?').removeprefix('Work/').removesuffix('.gyml') + ('.bgyml' if ref.endswith('.gyml') else '')
        # Keep the passive menu equipment user, not the world player's pouch-driven updater.
        # Its local bone bindings and blackboard inheritance must all exist in this pack.
        visiting, checked = set(), {}
        def equipment_resource(ref):
            path = packed_ref(ref)
            if path in visiting:
                raise ValueError('cyclic equipment resource: ' + path)
            if path in checked:
                return checked[path]
            visiting.add(path)
            resource = Byml(pack[path]).root
            merged = dict(equipment_resource(resource['$parent'])) if resource.get('$parent') else {}
            merged.update(resource)
            if resource.get('BlackboardTableRef'):
                equipment_resource(resource['BlackboardTableRef'])
            visiting.remove(path)
            checked[path] = merged
            return merged
        equipment = equipment_resource(components['EquipmentUserRef'])
        pouch = equipment.get('IsPouchUser')
        if not isinstance(pouch, Scalar) or pouch.kind != 0xd0 or pouch.word:
            raise ValueError('co-op equipment must use the passive menu component, not the local pouch')
        # Follow inheritance in the user's file rather than carrying an extracted component table.
        def inherited(resource):
            merged = {}
            parent = resource.get('$parent')
            if parent:
                merged.update(inherited(Byml(player[packed_ref(parent)]).root))
            merged.update(resource.get('Components', {}))
            return merged
        world_components = inherited(world_doc.root)
        animation_id = age + '_animation'
        animation_path = required[animation_id]
        if contract[animation_id][0] != 'animation' or not animation_path.endswith('.asb.zs'):
            raise ValueError('actor needs its private animation output: ' + age)
        original_path = packed_ref(world_components['ASRef']) + '.zs'
        original = rom.read(original_path)
        changed[animation_path] = body_animation(original)
        sizes.grow(original_path, animation_path, len(original), len(changed[animation_path]))
        components['ASRef'] = '?' + animation_path.removesuffix('.zs')
        components['ASInfoRef'] = world_components['ASInfoRef']
        components['AIInfoRef'] = ''
        # The ASInfo may itself inherit settings: copy its chain from the world actor.
        ref = packed_ref(components['ASInfoRef'])
        copied = set()
        while ref in player and ref not in copied:
            copied.add(ref)
            pack[ref] = player[ref]
            ref = packed_ref(Byml(player[ref]).root.get('$parent', ''))
        raw = doc.encode()
        pack[param(puppet)] = raw
        sizes.grow(param(menu), param(puppet), len(pack[param(menu)]), len(raw))
        path = required[age]
        changed[path] = sarc_write(pack)
        sizes.grow(menu_path, path, len(source), len(changed[path]))
        for table in table_docs:
            rows = {r['__RowId']: r for r in table.root}
            row = copy.deepcopy(rows[menu])
            row['__RowId'] = puppet
            if 'InstanceHeapSize' in row:
                row['InstanceHeapSize'] = copy.deepcopy(rows[world]['InstanceHeapSize'])
            table.root.append(row)
            table.root.sort(key=lambda r: r['__RowId'])
    for path, doc in zip(tables, table_docs):
        changed[path] = doc.encode()
        sizes.grow(path, path, len(doc.data), len(changed[path]))
    changed[size_path] = sizes.encode()
    if set(changed) != set(required.values()):
        raise ValueError('generated files do not match the co-op asset filename contract')
    compress = rom.zstd.ZstdCompressor(level=3)
    for path, blob in changed.items():
        target = Path(out) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(compress.compress(blob) if path.endswith('.zs') else blob)
    return len(changed)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--romfs', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    try:
        count = generate(Romfs(a.romfs), a.out)
    except (OSError, ValueError, KeyError, IndexError, struct.error) as e:
        sys.exit('co-op actor assets: ' + str(e))
    print('co-op actor assets: %d files made from your game; never distribute them' % count)


if __name__ == '__main__':
    main()
