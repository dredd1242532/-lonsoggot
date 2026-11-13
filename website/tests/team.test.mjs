import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, unlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { validateMember, readTeam } from '../src/lib/team.mjs';

const profile = { name: 'Example bean', profile: 'https://git.magicbean.dev/example-bean' };
test('minimal profiles need no personal data, bio or remote avatar', () => {
  const member = validateMember(profile, 'example-bean');
  assert.equal(member.handle, 'example-bean');
  assert.equal(member.host, 'MagicBean Git');
  assert.deepEqual(member.focus, []);
  assert.equal(member.bio, '');
  assert.equal(member.initial, 'E');
  assert.equal(validateMember({ ...profile, profile: profile.profile + '/' }, 'example-bean').profile, profile.profile);
});

test('profile URLs reject credentials, lookalike hosts, repositories and unsafe schemes', () => {
  for (const url of ['javascript:alert(1)', 'http://github.com/bean', 'https://github.com.evil.test/bean',
    'https://secret@github.com/bean', 'https://github.com/bean?token=secret', 'https://github.com/bean#profile',
    'https://github.com/bean/repo', 'https://git.magicbean.dev:8080/bean', 'https://github.com/%2fbean']) {
    assert.throws(() => validateMember({ ...profile, profile: url }, 'bean'));
  }
  for (const host of ['github.com', 'gitlab.com', 'codeberg.org']) {
    assert.equal(validateMember({ ...profile, profile: `https://${host}/bean` }, 'bean').profile, `https://${host}/bean`);
  }
});

test('profile format rejects unknown fields, markup, invalid names and duplicate focus labels', () => {
  for (const raw of [null, [], { ...profile, email: 'private@example.test' }, { ...profile, name: '' },
    { ...profile, name: '<script>' }, { ...profile, bio: 'x'.repeat(241) },
    { ...profile, focus: ['Audio', 'audio'] }, { ...profile, focus: 'Graphics' },
    { ...profile, focus: ['A', 'B', 'C', 'D'] }]) assert.throws(() => validateMember(raw, 'bean'));
  assert.throws(() => validateMember(profile, '../bean'));
});

test('file roster rejects duplicate identities and reports the problem filename', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'longshot-team-'));
  assert.deepEqual(await readTeam(dir), []);
  await writeFile(path.join(dir, 'first.json'), JSON.stringify(profile));
  await writeFile(path.join(dir, 'second.json'), JSON.stringify({ ...profile, profile: profile.profile.toUpperCase() }));
  await assert.rejects(readTeam(dir), /second.json: This Git profile is already listed/);
});

test('new members are loaded alphabetically and can be removed without changing code', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'longshot-team-lifecycle-'));
  await writeFile(path.join(dir, 'zoe.json'), JSON.stringify({ name: 'Zoe', profile: 'https://git.magicbean.dev/zoe' }));
  await writeFile(path.join(dir, 'amy.json'), JSON.stringify({ name: 'Amy', profile: 'https://github.com/amy' }));
  assert.deepEqual((await readTeam(dir)).map(member => member.name), ['Amy', 'Zoe']);
  await unlink(path.join(dir, 'amy.json'));
  assert.deepEqual((await readTeam(dir)).map(member => member.name), ['Zoe']);
});
