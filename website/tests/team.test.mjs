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

test('avatar URLs accept profile images and reject credentials, query tokens and lookalike hosts', () => {
  const good = ['https://git.magicbean.dev/avatars/abc123', 'https://codeberg.org/avatars/abc123',
    'https://avatars.githubusercontent.com/u/123', 'https://gitlab.com/uploads/-/system/user/avatar/123/photo.png'];
  for (const avatar of good) assert.equal(validateMember({ ...profile, avatar }, 'bean').avatar, avatar);
  for (const avatar of ['javascript:alert(1)', 'https://git.magicbean.dev.evil/avatars/abc',
    'https://token@git.magicbean.dev/avatars/abc', 'https://git.magicbean.dev/avatars/abc?token=secret',
    'https://git.magicbean.dev/api/v1/user']) assert.throws(() => validateMember({ ...profile, avatar }, 'bean'));
});

test('avatar refresh checks identity and keeps credentials on the MagicBean API', async () => {
  const { profileAvatar } = await import('../scripts/sync-team-avatars.mjs');
  const member = validateMember(profile, 'bean');
  let calls = 0;
  const avatar = await profileAvatar(member, { token: 'test-only', fetcher: async (url, options) => {
    calls++;
    assert.equal(url, 'https://git.magicbean.dev/api/v1/users/example-bean');
    assert.equal(options.headers.Authorization, 'Bearer test-only');
    assert.equal(options.redirect, 'error');
    return Response.json({ login: 'example-bean', avatar_url: 'https://git.magicbean.dev/avatars/abc123?size=128' });
  } });
  assert.equal(calls, 1);
  assert.equal(avatar, 'https://git.magicbean.dev/avatars/abc123');
  const github = validateMember({ ...profile, profile: 'https://github.com/bean' }, 'bean');
  await profileAvatar(github, { token: 'test-only', fetcher: async (_, options) => {
    assert.equal(options.headers.Authorization, undefined);
    return Response.json({ login: 'bean', avatar_url: 'https://avatars.githubusercontent.com/u/123?v=4' });
  } });
  await assert.rejects(profileAvatar(member, { fetcher: async () => Response.json({ login: 'different', avatar_url: avatar }) }), /identity mismatch/);
  await assert.rejects(profileAvatar(member, { fetcher: async () => new Response('', { status: 404 }) }), /404/);
});

test('avatar fallback handles cached failures and later load errors', async () => {
  const { attachAvatarFallback } = await import('../src/lib/team-avatar.mjs');
  function image(complete, naturalWidth) {
    const listeners = {};
    return { complete, naturalWidth, hidden: false, listeners,
      addEventListener: (name, fn) => { listeners[name] = fn; } };
  }
  const cached = image(true, 0); attachAvatarFallback(cached); assert.equal(cached.hidden, true);
  const loaded = image(true, 72); attachAvatarFallback(loaded); assert.equal(loaded.hidden, false);
  const pending = image(false, 0); attachAvatarFallback(pending); assert.equal(pending.hidden, false);
  pending.listeners.error(); assert.equal(pending.hidden, true);
});

test('explicit roster order takes priority over alphabetical order', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'longshot-team-order-'));
  await writeFile(path.join(dir, 'first.json'), JSON.stringify({ name: 'Zoe', profile: 'https://git.magicbean.dev/zoe', order: 10 }));
  await writeFile(path.join(dir, 'last.json'), JSON.stringify({ name: 'Amy', profile: 'https://github.com/amy', order: 10000 }));
  await writeFile(path.join(dir, 'new.json'), JSON.stringify({ name: 'Lee', profile: 'https://github.com/lee' }));
  assert.deepEqual((await readTeam(dir)).map(member => member.name), ['Zoe', 'Lee', 'Amy']);
  for (const order of [-1, 0.5, '1', 10001]) assert.throws(() => validateMember({ ...profile, order }, 'bean'));
});
