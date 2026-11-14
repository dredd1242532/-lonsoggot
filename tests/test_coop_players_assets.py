"""Game-free generation and the real mods.c loader over authored BYML/SARC files.

--mods-revision COMMIT compiles that commit's unchanged host/mods.c with today's
assertions and generator. LONGSHOT_COOP_MODS_SOURCE selects a saved mods.c instead;
LONGSHOT_COOP_ASSETS_SOURCE independently selects the generator, as before.
--asset-folder GAME/coop-players/romfs --romfs GAME/romfs checks an existing build
read-only: filenames, sizes and loader readiness only, never game asset contents.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

RUN_ALL_ARGS = ["--cc", "gcc"]

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('LONGSHOT_COOP_ASSETS_SOURCE', ROOT / 'nx2/coop_players_assets.py'))
spec = importlib.util.spec_from_file_location('coop_assets', SOURCE)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

MODS_SOURCE = Path(os.environ.get('LONGSHOT_COOP_MODS_SOURCE', ROOT / 'host/mods.c'))
MODS_REVISION = None
CC = os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang') or 'gcc'


def block(text, marker):
    """The source-extraction pattern used by test_mods_size_table.py."""
    start = text.index(marker)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def compile_loader(tmp, contract=None):
    """Compile all of mods.c; only unrelated host context is supplied by the fixture."""
    src = tmp / 'src'
    src.mkdir()
    if MODS_REVISION:
        source = subprocess.run(['git', 'show', MODS_REVISION + ':host/mods.c'], cwd=ROOT,
                                check=True, capture_output=True).stdout
        origin = MODS_REVISION + ':host/mods.c'
    else:
        source = MODS_SOURCE.read_bytes()
        origin = str(MODS_SOURCE)
    print('loader source: %s sha256=%s' % (origin, hashlib.sha256(source).hexdigest()), flush=True)
    (src / 'mods.c').write_bytes(source)
    if contract is not None:
        (src / 'coop_players_assets.h').write_bytes(contract.read_bytes())
    (src / 'hos.h').write_text(
        '#pragma once\n#include <stddef.h>\n#include <stdint.h>\nvoid hos_log(const char *, ...);\n',
        encoding='utf-8')
    (src / 'hos_paths.h').write_text(
        '#pragma once\n#include <windows.h>\n#include <stdio.h>\n#include <stdlib.h>\n'
        'static inline DWORD hos_game_path_a(char *out, DWORD cap) {\n'
        '    return (DWORD)snprintf(out, cap, "%s", getenv("TEST_GAME_EXE"));\n}\n',
        encoding='utf-8')
    # Native Windows uses its own Win32 API. Linux uses the same real filesystem
    # functions as production and test_mods_size_table.py, not a fake file index.
    (src / 'windows.h').write_text(
        '#ifdef _WIN32\n#include_next <windows.h>\n#else\n#include "%s"\n#endif\n'
        % (ROOT / 'host/posix/windows.h').as_posix(), encoding='utf-8')
    posix = (ROOT / 'host/posix/win32_posix.c').read_text(encoding='utf-8')
    pieces = [block(posix, 'static DWORD error_of('), block(posix, 'static BOOL fail_errno('),
              posix[posix.index('enum { H_THREAD'):posix.index('static int64_t now_ns(')],
              block(posix, 'static void slashes('), block(posix, 'DWORD GetFileAttributesA('),
              'typedef struct { HObj h; DIR *d; char dir[PATH_MAX], pat[256]; } HFind;']
    pieces += [block(posix, marker) for marker in
               ('static BOOL find_next(', 'HANDLE FindFirstFileA(', 'BOOL FindNextFileA(', 'BOOL FindClose(')]
    (src / 'filesystem.c').write_text(
        '#ifndef _WIN32\n#include <windows.h>\n#include <dirent.h>\n#include <errno.h>\n'
        '#include <fnmatch.h>\n#include <limits.h>\n#include <stdint.h>\n#include <stdio.h>\n'
        '#include <stdlib.h>\n#include <string.h>\n#include <sys/stat.h>\n#include <unistd.h>\n'
        'static DWORD last_error;\n' + '\n'.join(pieces) + '\n#endif\n', encoding='utf-8')
    exe = tmp / 'loader.exe'
    command = [CC, '-std=gnu11', '-D_GNU_SOURCE', '-D_CRT_SECURE_NO_WARNINGS', '-Wall', '-Wextra',
               '-I', str(src), '-I', str(ROOT / 'host'), str(src / 'mods.c'), str(src / 'filesystem.c'),
               str(ROOT / 'tests/coop_players_assets_loader_test.c'), '-o', str(exe)]
    print('compiler: ' + ' '.join(command), flush=True)
    subprocess.run(command, check=True, timeout=120)
    return exe


def contract_variant(root, entry):
    """Change only one contract entry beside an otherwise unchanged generator."""
    generator = root / 'nx2/coop_players_assets.py'
    generator.parent.mkdir(parents=True)
    generator.write_bytes(SOURCE.read_bytes())
    contract = root / 'host/coop_players_assets.h'
    contract.parent.mkdir()
    contract.write_text((ROOT / 'host/coop_players_assets.h').read_text(encoding='utf-8') + entry + '\n',
                        encoding='utf-8')
    spec = importlib.util.spec_from_file_location('coop_assets_contract_fixture', generator)
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)
    return subject, contract


def contract_entries(contract=ROOT / 'host/coop_players_assets.h'):
    text = contract.read_text(encoding='utf-8')
    entries = re.findall(r'^COOP_PLAYER_ASSET\((actor|animation|table), (\w+), "([^"]+)"\)$', text, re.M)
    if not entries:
        raise ValueError('no required asset filenames in the shared contract')
    return entries


def required_output_names(romfs, contract=ROOT / 'host/coop_players_assets.h'):
    """Independent contract reader; table names are selected from the source, never the output."""
    names = set()
    for _, _, pattern in contract_entries(contract):
        if '*' in pattern:
            paths = [p for p in romfs.glob(pattern) if p.is_file()]
            if len(paths) != 1:
                raise ValueError('expected one source file: ' + pattern)
            names.add(paths[0].relative_to(romfs).as_posix())
        else:
            names.add(pattern)
    return names


def run_loader(exe, game, romfs, names=(), *, players='1', animation=None, ini=0, mods=None):
    env = os.environ.copy()
    env.update(TEST_GAME_EXE=str(game / 'longshot.exe'), TEST_ROMFS=str(romfs),
               TEST_PLAYERS_OPTION=str(ini), HOS_MODS=str(mods if mods is not None else game / 'mods'))
    for key, value in (('HOS_COOP_PLAYERS', players), ('HOS_COOP_PLAYERS_ANIM', animation)):
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run([str(exe), *sorted(names)], env=env, capture_output=True, text=True,
                            check=True, timeout=30)
    ready, files = None, {}
    for line in result.stdout.splitlines():
        if line.startswith('READY\t'):
            ready = int(line.split('\t')[1])
        elif line.startswith('FILE\t'):
            _, name, host = line.split('\t')
            files[name] = None if host == '(base)' else Path(host.replace('\\', '/'))
    if ready is None:
        raise AssertionError('loader returned no readiness result:\n' + result.stdout + result.stderr)
    return ready, files, result.stdout + result.stderr


def check_existing_assets(exe, folder, romfs, empty_mods):
    """No writes inside the supplied folder or RomFS, and no asset bytes read."""
    folder, romfs = folder.resolve(), romfs.resolve()
    if folder.name != 'romfs' or folder.parent.name != 'coop-players':
        raise ValueError('--asset-folder must name GAME/coop-players/romfs')
    expected = required_output_names(romfs)
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual != expected:
        raise AssertionError('asset filenames differ: missing=%r unexpected=%r'
                             % (sorted(expected - actual), sorted(actual - expected)))
    ready, files, log = run_loader(exe, folder.parent.parent, romfs, expected, mods=empty_mods)
    print(log, end='')
    if ready != 1 or any(files.get(name) != folder / name for name in expected):
        raise AssertionError('generated files were not admitted and routed by the production loader')
    print('PASS: existing generated set matches every contract filename (%d files); loader ready=1; read-only'
          % len(expected))


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
        if isinstance(v, bool): return 0xd0, int(v)
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


def animation_fixture():
    """Authored ASB metadata: one lower-body slot and two distinct skeleton masks."""
    data = bytearray(0xc0)
    data[:8] = b'ASB ' + struct.pack('<I', 0x41b)
    struct.pack_into('<II', data, 0x24, 1, 0x80)
    struct.pack_into('<II', data, 0x58, 2, 0x90)
    strings = b'Lower\0All_PB\0FixtureBone\0'
    struct.pack_into('<I', data, 0x3c, len(data))
    struct.pack_into('<I', data, 0x60, len(strings))
    struct.pack_into('<IIII', data, 0x80, 0, 1, 0, 0)
    struct.pack_into('<III', data, 0x90, 0xa8, 6, 1)
    struct.pack_into('<III', data, 0x9c, 0xb0, 0, 1)
    struct.pack_into('<IhBB', data, 0xa8, 13, 0, 1, 0)
    struct.pack_into('<IhBB', data, 0xb0, 13, 1, 1, 0)
    return bytes(data + strings)


class IdentityCompression:
    def ZstdCompressor(self, **kwargs): return self
    def compress(self, b): return b


class FakeRomfs:
    zstd = IdentityCompression()
    def __init__(self):
        self.files = {}
        names, sizes, rows = [], {}, []
        def param(n): return 'Actor/' + n + '.engine__actor__ActorParam.bgyml'
        for age, heap in [('Child', 10000), ('Adult', 20000), ('Fixture', 30000)]:
            menu, world = 'PouchPlayer_' + age, 'Player_' + age
            asref = 'Component/ASInfo/' + age + '.bgyml'
            base = 'Component/ASInfo/Base.bgyml'
            eqref = 'Component/EquipmentUserParam/Menu_' + age + '.bgyml'
            for n, components in [(menu, {'ASRef': '?AS/menu.asb', 'ASInfoRef': '?menu', 'AIInfoRef': '?menu-ai', 'EquipmentUserRef': '?' + eqref}),
                                  (world, {'ASRef': '?AS/' + age + '.asb', 'ASInfoRef': '?' + asref})]:
                pack = {param(n): fixture({'Components': components}), 'opaque.bin': b'not a model'}
                if n == world:
                    pack[asref] = fixture({'$parent': 'Work/Component/ASInfo/Base.gyml', 'Rate': 33})
                    pack[base] = fixture({'Slots': 9})
                else:
                    pack[eqref] = fixture({'$parent': '?Component/EquipmentUserParam/Base.bgyml', 'IsPouchUser': False})
                    pack['Component/EquipmentUserParam/Base.bgyml'] = fixture({
                        'IsPouchUser': True, 'BlackboardTableRef': '?Component/Blackboard/Equipment.bgyml', 'BoneName': age + 'Hand'})
                    pack['Component/Blackboard/Equipment.bgyml'] = fixture({'Equipment': ['Weapon', 'Shield']})
                path = 'Pack/Actor/' + n + '.pack.zs'
                self.files[path] = a.sarc_write(pack)
                sizes[path.removesuffix('.zs')] = len(self.files[path]) + 300
                sizes[param(n)] = len(pack[param(n)]) + 200
                rows.append({'__RowId': n, 'InstanceHeapSize': heap if n == world else 123, 'Opaque': 0xffffffff})
            animation = 'AS/' + age + '.asb.zs'
            self.files[animation] = animation_fixture()
            sizes[animation.removesuffix('.zs')] = len(self.files[animation]) + 200
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

    def test_private_actors_from_authored_files_follow_contract(self):
        rom = FakeRomfs()
        before = dict(rom.files)
        with tempfile.TemporaryDirectory() as tmp:
            entries = contract_entries()
            self.assertEqual({age for kind, age, _ in entries if kind == 'actor'}, {'Child', 'Adult'})
            self.assertEqual({name for kind, name, _ in entries if kind == 'animation'},
                             {'Child_animation', 'Adult_animation'})
            self.assertEqual(len(entries), 7)
            required = {name: rom.one(path) if '*' in path else path for _, name, path in entries}
            expected = set(required.values())
            self.assertEqual(a.generate(rom, tmp), len(expected))
            self.assertEqual({p.relative_to(tmp).as_posix() for p in Path(tmp).rglob('*') if p.is_file()}, expected)
            source_rows = a.Byml(rom.files[required['actor_info']]).root
            for entry_kind, age, pack_path in entries:
                if entry_kind != 'actor':
                    continue
                heap = next(r['InstanceHeapSize'].word for r in source_rows if r['__RowId'] == 'Player_' + age)
                name = 'CoopPuppet_' + age
                pack = a.sarc_read((Path(tmp) / pack_path).read_bytes())
                params = a.Byml(pack['Actor/' + name + '.engine__actor__ActorParam.bgyml']).root['Components']
                self.assertEqual(params['ASRef'], '?AS/CoopPuppet_' + age + '.root.asb')
                self.assertEqual(params['AIInfoRef'], '')
                self.assertIn('Component/ASInfo/Base.bgyml', pack)
                self.assertEqual(params['EquipmentUserRef'], '?Component/EquipmentUserParam/Menu_' + age + '.bgyml')
                eq = a.Byml(pack[params['EquipmentUserRef'][1:]]).root
                self.assertEqual(eq['IsPouchUser'].word, 0)
                self.assertIn('Component/Blackboard/Equipment.bgyml', pack)
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

    def test_generated_primary_slot_covers_the_body_without_player_components(self):
        for age in ('Child', 'Adult'):
            with self.subTest(age=age), tempfile.TemporaryDirectory() as tmp:
                rom = FakeRomfs()
                source_path = 'AS/' + age + '.asb.zs'
                source = rom.files[source_path]
                a.generate(rom, tmp)
                name = 'CoopPuppet_' + age
                pack = a.sarc_read((Path(tmp) / ('Pack/Actor/' + name + '.pack.zs')).read_bytes())
                params = a.Byml(pack['Actor/' + name + '.engine__actor__ActorParam.bgyml']).root['Components']
                self.assertEqual(params['ASRef'], '?AS/' + name + '.root.asb')
                self.assertNotIn('PlayerRef', params)
                generated = (Path(tmp) / ('AS/' + name + '.root.asb.zs')).read_bytes()
                expected = bytearray(source)
                struct.pack_into('<I', expected, 0x84, 0)
                self.assertEqual(generated, expected)  # only the primary mask binding changes
                self.assertEqual(rom.files[source_path], source)

    def test_primary_mask_uses_named_source_index_and_refuses_unknown_layouts(self):
        source = bytearray(animation_fixture())
        # Full-body is now index 1, so a hard-coded zero cannot pass.
        source[0x90:0xa8] = source[0x9c:0xa8] + source[0x90:0x9c]
        struct.pack_into('<I', source, 0x84, 0)
        got = a.body_animation(bytes(source))
        self.assertEqual(struct.unpack_from('<I', got, 0x84)[0], 1)
        for bad in (b'', bytes(source[:0x80]), bytes(source).replace(b'All_PB', b'Absent')):
            with self.assertRaises(ValueError):
                a.body_animation(bad)

    def test_missing_full_body_mask_refuses_before_writing_assets(self):
        for age in ('Child', 'Adult'):
            with self.subTest(age=age), tempfile.TemporaryDirectory() as tmp:
                rom = FakeRomfs()
                rom.files['AS/' + age + '.asb.zs'] = animation_fixture().replace(b'All_PB', b'Absent')
                with self.assertRaisesRegex(ValueError, 'full-body mask'):
                    a.generate(rom, tmp)
                self.assertFalse(any(Path(tmp).rglob('*.zs')))


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

    def test_equipment_component_and_inheritance_fail_closed_for_each_age(self):
        for age in ('Child', 'Adult'):
            for broken in ('missing', 'parent', 'blackboard', 'cycle', 'pouch'):
                with self.subTest(age=age, broken=broken):
                    rom = FakeRomfs()
                    path = 'Pack/Actor/PouchPlayer_' + age + '.pack.zs'
                    pack = a.sarc_read(rom.files[path])
                    eq = 'Component/EquipmentUserParam/Menu_' + age + '.bgyml'
                    if broken == 'missing': del pack[eq]
                    elif broken == 'parent': del pack['Component/EquipmentUserParam/Base.bgyml']
                    elif broken == 'blackboard': del pack['Component/Blackboard/Equipment.bgyml']
                    else:
                        doc = a.Byml(pack[eq])
                        if broken == 'cycle': doc.root['$parent'] = '?' + eq
                        else: doc.root['IsPouchUser'] = a.Scalar(0xd0, 1)
                        pack[eq] = doc.encode()
                    rom.files[path] = a.sarc_write(pack)
                    with tempfile.TemporaryDirectory() as tmp:
                        with self.assertRaises((KeyError, ValueError)): a.generate(rom, tmp)
                        self.assertFalse(any(Path(tmp).rglob('*.zs')))


    def test_contract_is_checked_before_any_output_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            subject, _ = contract_variant(tmp, 'COOP_PLAYER_ASSET(table, unproduced, "Fixture/required.bin")')
            out = tmp / 'output'
            with self.assertRaisesRegex(ValueError, 'generated files do not match'):
                subject.generate(FakeRomfs(), out)
            self.assertFalse(out.exists())


class Loader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='coop-assets-loader-')
        cls.addClassCleanup(cls.build.cleanup)
        cls.exe = compile_loader(Path(cls.build.name))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='case-', dir=self.build.name)
        self.addCleanup(self.temp.cleanup)
        self.game = Path(self.temp.name) / 'game'
        self.romfs = Path(self.temp.name) / 'source-romfs'
        self.assets = self.game / 'coop-players/romfs'
        rom = FakeRomfs()
        for name, blob in rom.files.items():
            path = self.romfs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        a.generate(rom, self.assets)
        self.required = required_output_names(self.romfs)

    def test_contract_rows_add_an_actor_and_animation_for_generator_and_loader(self):
        root = Path(self.temp.name) / 'extended-contract'
        subject, contract = contract_variant(
            root, 'COOP_PLAYER_ASSET(actor, Fixture, "Pack/Actor/CoopPuppet_Fixture.pack.zs")\n'
                  'COOP_PLAYER_ASSET(animation, Fixture_animation, "AS/CoopPuppet_Fixture.root.asb.zs")')
        # This entry exists only in the authored temporary fixture, never in the shipped contract.
        expected = required_output_names(self.romfs, contract)
        self.assertEqual(subject.generate(FakeRomfs(), self.assets), len(expected))
        self.assertEqual({p.relative_to(self.assets).as_posix() for p in self.assets.rglob('*') if p.is_file()},
                         expected)
        exe = compile_loader(root, contract)
        ready, files, log = run_loader(exe, self.game, self.romfs, expected)
        self.assertEqual(ready, 1, log)
        for name in expected:
            self.assertEqual(files[name], self.assets / name)
        added = self.assets / 'Pack/Actor/CoopPuppet_Fixture.pack.zs'
        pack = a.sarc_read(added.read_bytes())
        params = a.Byml(pack['Actor/CoopPuppet_Fixture.engine__actor__ActorParam.bgyml']).root['Components']
        self.assertEqual(params['ASRef'], '?AS/CoopPuppet_Fixture.root.asb')
        self.assertEqual(params['AIInfoRef'], '')
        added.rename(added.with_name('unrelated-filler.pack.zs'))
        ready, _, log = run_loader(exe, self.game, self.romfs, expected)
        self.assertEqual(ready, 0, log)   # the same total cannot hide the new missing requirement

    def load(self, **kwargs):
        return run_loader(self.exe, self.game, self.romfs, self.required, **kwargs)

    def assert_ready(self, wanted, **kwargs):
        ready, files, log = self.load(**kwargs)
        self.assertEqual(ready, wanted, log)
        return files, log

    def test_generator_output_is_admitted_and_routed(self):
        files, log = self.assert_ready(1)
        self.assertIn('locally built animation assets loaded', log)
        for name in self.required:
            self.assertEqual(files[name], self.assets / name)
        self.assertEqual({p.relative_to(self.assets).as_posix() for p in self.assets.rglob('*') if p.is_file()},
                         self.required)

    def test_same_count_wrong_names_never_fill_a_required_slot(self):
        for name in sorted(self.required):
            original = self.assets / name
            wrong = original.with_name(original.name + '.unrelated')
            original.rename(wrong)
            try:
                # Neither the exact total nor unrelated extra files can hide a missing requirement.
                for filler in (False, True):
                    with self.subTest(missing=name, extra_file=filler):
                        extra = self.assets / 'unrelated-extra-file.bin'
                        if filler:
                            extra.write_bytes(b'authored unrelated file, not a required asset')
                        try:
                            self.assert_ready(0)
                        finally:
                            if filler:
                                extra.unlink()
            finally:
                wrong.rename(original)

    def test_each_missing_file_refuses(self):
        for name in sorted(self.required):
            with self.subTest(missing=name):
                path = self.assets / name
                blob = path.read_bytes()
                path.unlink()
                try:
                    _, log = self.assert_ready(0)
                    self.assertIn('missing or incomplete', log)
                finally:
                    path.write_bytes(blob)

    def test_missing_tree_refuses(self):
        self.assets.rename(self.assets.with_name('absent-romfs'))
        files, _ = self.assert_ready(0)
        self.assertTrue(all(path is None for path in files.values()))

    def test_directory_or_empty_file_cannot_satisfy_requirement(self):
        for name in sorted(self.required):
            path = self.assets / name
            blob = path.read_bytes()
            try:
                with self.subTest(name=name, kind='empty'):
                    path.write_bytes(b'')
                    self.assert_ready(0)
                with self.subTest(name=name, kind='directory'):
                    path.unlink()
                    path.mkdir()
                    try:
                        self.assert_ready(0)
                    finally:
                        path.rmdir()
            finally:
                path.write_bytes(blob)

    def test_other_table_version_does_not_satisfy_requirement(self):
        for name in sorted(n for n in self.required if n.startswith('RSDB/')):
            path = self.assets / name
            wrong = path.with_name(path.name.replace('.999.', '.998.'))
            path.rename(wrong)
            try:
                with self.subTest(table=name):
                    self.assert_ready(0)
            finally:
                wrong.rename(path)

    def test_missing_or_ambiguous_source_table_refuses(self):
        name = next(n for n in self.required if n.startswith('RSDB/ActorInfo.'))
        path = self.romfs / name
        extra = path.with_name(path.name.replace('.999.', '.998.'))
        path.rename(extra)
        try:
            self.assert_ready(0)   # the generated version is no longer the source's version
            path.write_bytes(extra.read_bytes())
            self.assert_ready(0)   # two source versions: Romfs.one cannot choose either
            path.unlink()
            extra.unlink()
            self.assert_ready(0)   # no source version
        finally:
            if extra.exists():
                extra.unlink()

    def test_mod_replacements_win_and_disable_animation(self):
        for name in sorted(self.required):
            with self.subTest(replacement=name):
                mod = self.game / 'mods/user/romfs' / name
                mod.parent.mkdir(parents=True, exist_ok=True)
                mod.write_bytes(b'authored user replacement')
                try:
                    files, log = self.assert_ready(0)
                    self.assertEqual(files[name], mod)
                    self.assertEqual(files[name].read_bytes(), b'authored user replacement')
                    self.assertIn('animation assets disabled: a mod replaces ' + name.lower(), log)
                finally:
                    mod.unlink()

    def test_case_folded_mod_replacement_disables_animation(self):
        name = next(n for n in self.required if n.startswith('Pack/'))
        mod = self.game / 'mods/user/romfs' / name.upper()
        mod.parent.mkdir(parents=True, exist_ok=True)
        mod.write_bytes(b'authored upper-case replacement')
        files, log = self.assert_ready(0)
        self.assertEqual(files[name], mod)
        self.assertIn('animation assets disabled: a mod replaces', log)

    def test_nonconflicting_mod_keeps_animation_and_generated_routes(self):
        name = 'Pack/Actor/Unrelated.pack.zs'
        mod = self.game / 'mods/user/romfs' / name
        mod.parent.mkdir(parents=True, exist_ok=True)
        mod.write_bytes(b'authored unrelated mod')
        ready, files, log = run_loader(self.exe, self.game, self.romfs, self.required | {name})
        self.assertEqual(ready, 1, log)
        self.assertEqual(files[name], mod)
        for required in self.required:
            self.assertEqual(files[required], self.assets / required)
        self.assertNotIn('animation assets disabled', log)

    def test_unrelated_generated_files_do_not_change_readiness(self):
        (self.assets / 'extra.bin').write_bytes(b'authored extra')
        (self.assets / 'another.bin').write_bytes(b'authored extra')
        self.assert_ready(1)

    def test_local_display_and_diagnostic_override_gate_indexing(self):
        for settings in ({'players': '0', 'ini': 1}, {'players': None, 'ini': 0},
                         {'animation': '0'}, {'animation': 'off'}, {'animation': 'false'}):
            with self.subTest(settings=settings):
                files, _ = self.assert_ready(0, **settings)
                self.assertTrue(all(path is None for path in files.values()))
        self.assert_ready(1, players=None, ini=1)
        self.assert_ready(1, animation=None)
        self.assert_ready(1, animation='1')


def main():
    global CC, MODS_REVISION, MODS_SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default=CC)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--mods-source', type=Path, default=MODS_SOURCE)
    selection.add_argument('--mods-revision', help='compile COMMIT:host/mods.c unchanged, no checkout or source edits')
    parser.add_argument('--asset-folder', type=Path, help='read-only post-build check instead of authored unit cases')
    parser.add_argument('--romfs', type=Path, help='source RomFS for exact table names (required with --asset-folder)')
    args, tests = parser.parse_known_args()
    CC, MODS_REVISION, MODS_SOURCE = args.cc, args.mods_revision, args.mods_source
    if args.asset_folder:
        if not args.romfs or tests:
            parser.error('--asset-folder requires --romfs and cannot select unit tests')
        with tempfile.TemporaryDirectory(prefix='coop-assets-existing-') as tmp:
            tmp = Path(tmp)
            check_existing_assets(compile_loader(tmp), args.asset_folder, args.romfs, tmp / 'no-user-mods')
    elif args.romfs:
        parser.error('--romfs requires --asset-folder')
    else:
        unittest.main(argv=[sys.argv[0], *tests])


if __name__ == '__main__':
    main()
