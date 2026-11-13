"""Game-free co-op asset generation: authored BYML/SARC fixtures, no dump or extracted tables."""
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('LONGSHOT_COOP_ASSETS_SOURCE', ROOT / 'nx2/coop_players_assets.py'))
spec = importlib.util.spec_from_file_location('coop_assets', SOURCE)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def fixture(root, endian='<'):
    """Independent small writer for authored strings, unsigned integers, lists and maps."""
    keys, strings = set(), set()
    def scan(v):
        if isinstance(v, str): strings.add(v)
        elif isinstance(v, dict):
            keys.update(v)
            for x in v.values(): scan(x)
        elif isinstance(v, list):
            for x in v: scan(x)
    scan(root)
    keys, strings = sorted(keys), sorted(strings)
    b = bytearray((b'YB' if endian == '<' else b'BY') + struct.pack(endian + 'HIII', 7, 0, 0, 0))
    order = 'little' if endian == '<' else 'big'
    def align():
        b.extend(bytes(-len(b) % 4))
        return len(b)
    def table(values):
        p = align()
        offsets = bytearray()
        text = bytearray()
        for v in values:
            offsets.extend(struct.pack(endian + 'I', 4 + 4 * (len(values) + 1) + len(text)))
            text.extend(v.encode() + b'\0')
        offsets.extend(struct.pack(endian + 'I', 4 + 4 * (len(values) + 1) + len(text)))
        b.extend(b'\xc2' + len(values).to_bytes(3, order) + offsets + text)
        return p
    kt, st = table(keys), table(strings)
    def val(v):
        if isinstance(v, str): return 0xa0, strings.index(v)
        if isinstance(v, int): return 0xd3, v
        pairs = sorted(v.items()) if isinstance(v, dict) else list(enumerate(v))
        children = [(k, *val(x)) for k, x in pairs]
        p = align()
        t = 0xc1 if isinstance(v, dict) else 0xc0
        b.extend(bytes([t]) + len(pairs).to_bytes(3, order))
        if t == 0xc1:
            for k, t, x in children:
                b.extend(keys.index(k).to_bytes(3, order) + bytes([t]) + struct.pack(endian + 'I', x))
        else:
            b.extend(bytes(t for _, t, _ in children)); align()
            for _, _, x in children: b.extend(struct.pack(endian + 'I', x))
        return 0xc1 if isinstance(v, dict) else 0xc0, p
    _, pos = val(root)
    struct.pack_into(endian + 'III', b, 4, kt, st, pos)
    return bytes(b)


class IdentityCompression:
    def ZstdCompressor(self, **kwargs): return self
    def compress(self, b): return b


class FakeRomfs:
    zstd = IdentityCompression()
    def __init__(self):
        self.files = {}
        names, sizes, rows = [], {}, []
        def param(n): return 'Actor/' + n + '.engine__actor__ActorParam.bgyml'
        for age, heap in [('Child', 10000), ('Adult', 20000)]:
            menu, world = 'PouchPlayer_' + age, 'Player_' + age
            asref = 'Component/ASInfo/' + age + '.bgyml'
            base = 'Component/ASInfo/Base.bgyml'
            for n, components in [(menu, {'ASRef': '?AS/menu.asb', 'ASInfoRef': '?menu', 'AIInfoRef': '?menu-ai'}),
                                  (world, {'ASRef': '?AS/' + age + '.asb', 'ASInfoRef': '?' + asref})]:
                pack = {param(n): fixture({'Components': components}), 'opaque.bin': b'not a model'}
                if n == world:
                    pack[asref] = fixture({'$parent': 'Work/Component/ASInfo/Base.gyml', 'Rate': 33})
                    pack[base] = fixture({'Slots': 9})
                path = 'Pack/Actor/' + n + '.pack.zs'
                self.files[path] = a.sarc_write(pack)
                sizes[path.removesuffix('.zs')] = len(self.files[path]) + 300
                sizes[param(n)] = len(pack[param(n)]) + 200
                rows.append({'__RowId': n, 'InstanceHeapSize': heap if n == world else 123, 'Opaque': 0xffffffff})
        for kind in ('ActorInfo', 'GameActorInfo'):
            path = 'RSDB/' + kind + '.Product.999.rstbl.byml.zs'
            self.files[path] = fixture(rows)
            sizes[path.removesuffix('.zs')] = len(self.files[path]) + 500
        header = b'RESTBL' + struct.pack('<II', 1, 128)
        entries = sorted((zlib.crc32(k.encode()), v) for k, v in sizes.items())
        self.files['System/Resource/ResourceSizeTable.fixture.rsizetable.zs'] = (
            header + struct.pack('<II', len(entries), 0) + b''.join(struct.pack('<II', *p) for p in entries))
    def read(self, path): return self.files[path]
    def one(self, pattern):
        import fnmatch
        matches = [p for p in self.files if fnmatch.fnmatch(p, pattern)]
        assert len(matches) == 1
        return matches[0]


class Assets(unittest.TestCase):
    def test_edit_retains_scalar_type_and_original_payload(self):
        for endian in ('<', '>'):
            raw = fixture({'Name': 'before', 'Nested': [3, 0xffffffff, 'unchanged']}, endian)
            doc = a.Byml(raw)
            doc.root['Name'] = 'after'
            got = doc.encode()
            self.assertEqual(got[16:len(raw)], raw[16:])
            back = a.Byml(got)
            self.assertEqual(back.root['Name'], 'after')
            self.assertEqual(back.root['Nested'][1].kind, 0xd3)
            self.assertEqual(back.root['Nested'][1].word, 0xffffffff)
            self.assertEqual(back.root['Nested'][2], 'unchanged')

    def test_sarc_members_and_hash_order(self):
        files = {'z.bin': b'last', 'a.bin': b'first', 'empty': b''}
        raw = a.sarc_write(files)
        self.assertEqual(a.sarc_read(raw), files)
        self.assertEqual(raw, a.sarc_write(dict(reversed(list(files.items())))))
        hashes = [struct.unpack_from('<I', raw, 32 + 16 * i)[0] for i in range(3)]
        self.assertEqual(hashes, sorted(hashes))

    def test_private_child_actor_from_authored_files(self):
        rom = FakeRomfs()
        before = dict(rom.files)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(a.generate(rom, tmp), 4)
            self.assertFalse((Path(tmp) / 'Pack/Actor/CoopPuppet_Adult.pack.zs').exists())
            for age, heap in [('Child', 10000)]:
                name = 'CoopPuppet_' + age
                pack = a.sarc_read((Path(tmp) / ('Pack/Actor/' + name + '.pack.zs')).read_bytes())
                params = a.Byml(pack['Actor/' + name + '.engine__actor__ActorParam.bgyml']).root['Components']
                self.assertEqual(params['ASRef'], '?AS/' + age + '.asb')
                self.assertEqual(params['AIInfoRef'], '')
                self.assertIn('Component/ASInfo/Base.bgyml', pack)
                self.assertEqual(pack['opaque.bin'], b'not a model')
                for kind in ('ActorInfo', 'GameActorInfo'):
                    rows = a.Byml((Path(tmp) / ('RSDB/' + kind + '.Product.999.rstbl.byml.zs')).read_bytes()).root
                    self.assertEqual([r['__RowId'] for r in rows], sorted(r['__RowId'] for r in rows))
                    row = next(r for r in rows if r['__RowId'] == name)
                    self.assertEqual(row['InstanceHeapSize'].word, heap)
                    self.assertEqual(row['Opaque'].word, 0xffffffff)
                sizes = a.Sizes((Path(tmp) / 'System/Resource/ResourceSizeTable.fixture.rsizetable.zs').read_bytes())
                self.assertIn(zlib.crc32(('Pack/Actor/' + name + '.pack').encode()), sizes.values)
            self.assertEqual(rom.files, before)

    def test_missing_animation_component_refuses_generation(self):
        rom = FakeRomfs()
        path = 'Pack/Actor/Player_Child.pack.zs'
        pack = a.sarc_read(rom.files[path])
        params = 'Actor/Player_Child.engine__actor__ActorParam.bgyml'
        doc = a.Byml(pack[params])
        del doc.root['Components']['ASRef']
        pack[params] = doc.encode()
        rom.files[path] = a.sarc_write(pack)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                a.generate(rom, tmp)
            self.assertFalse(any(Path(tmp).rglob('*.zs')))


if __name__ == '__main__':
    unittest.main()
