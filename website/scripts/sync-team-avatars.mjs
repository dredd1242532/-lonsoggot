import { readFile, writeFile, rename } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { readTeam, validAvatar } from '../src/lib/team.mjs';

export async function profileAvatar(member, { fetcher = fetch, token = '' } = {}) {
  const url = new URL(member.profile);
  const handle = encodeURIComponent(member.handle);
  const endpoint = url.hostname === 'github.com' ? `https://api.github.com/users/${handle}` :
    url.hostname === 'gitlab.com' ? `https://gitlab.com/api/v4/users?username=${handle}` : `${url.origin}/api/v1/users/${handle}`;
  const headers = { Accept: 'application/json', 'User-Agent': 'Longshot-team-roster' };
  if (url.hostname === 'git.magicbean.dev' && token) headers.Authorization = `Bearer ${token}`;
  const response = await fetcher(endpoint, { headers, redirect: 'error', signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`Profile API returned ${response.status}`);
  const data = await response.json();
  const user = url.hostname === 'gitlab.com' ? data.find(item => item.username?.toLowerCase() === member.handle.toLowerCase()) : data;
  if ((user?.login || user?.username)?.toLowerCase() !== member.handle.toLowerCase()) throw new Error('Profile identity mismatch');
  // Keep only the image URL, never the rest of the profile response or any token.
  const avatar = new URL(user.avatar_url);
  for (const key of avatar.searchParams.keys()) if (!['s', 'size', 'v', 'd'].includes(key)) throw new Error('Unexpected avatar query');
  avatar.search = '';
  if (!validAvatar(avatar.href)) throw new Error('Unsupported avatar URL');
  return avatar.href;
}

async function sync() {
  const directory = new URL('../team/', import.meta.url);
  let failures = 0;
  for (const member of await readTeam(directory)) {
    try {
      const avatar = await profileAvatar(member, { token: process.env.FORGEJO_TOKEN || '' });
      const file = new URL(`${member.id}.json`, directory);
      const raw = JSON.parse(await readFile(file, 'utf8'));
      if (raw.avatar !== avatar) {
        const temporary = new URL(`${member.id}.json.tmp`, directory);
        await writeFile(temporary, JSON.stringify({ ...raw, avatar }, null, 2) + '\n');
        await rename(temporary, file);
      }
      console.log(`Updated avatar: ${member.id}`);
    } catch (error) { failures++; console.warn(`${member.id}: avatar unchanged (${error.message}).`); }
  }
  if (failures) process.exitCode = 1;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await sync();
