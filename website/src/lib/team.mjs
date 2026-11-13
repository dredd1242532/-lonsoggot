import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

const hosts = new Map([
  ['git.magicbean.dev', 'MagicBean Git'], ['github.com', 'GitHub'],
  ['gitlab.com', 'GitLab'], ['codeberg.org', 'Codeberg'],
]);
const fields = new Set(['name', 'profile', 'focus', 'bio', 'avatar', 'order']);
function text(value, max, field) {
  if (typeof value !== 'string' || !value.trim() || value.length > max || /[\x00-\x1f\x7f<>]/.test(value)) {
    throw new Error(`Invalid ${field}: use plain text, up to ${max} characters.`);
  }
  return value.trim();
}

export function validAvatar(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port && !url.search && !url.hash &&
      ((['git.magicbean.dev', 'codeberg.org'].includes(url.hostname) && /^\/avatars\/[a-zA-Z0-9._-]+$/.test(url.pathname)) ||
       (url.hostname === 'avatars.githubusercontent.com' && /^\/u\/\d+$/.test(url.pathname)) ||
       (url.hostname === 'gitlab.com' && /^\/uploads\/(?:-\/system\/)?user\/avatar\/\d+\/[a-zA-Z0-9._-]+$/.test(url.pathname)) ||
       (['secure.gravatar.com', 'www.gravatar.com'].includes(url.hostname) && /^\/avatar\/[a-f0-9]+$/.test(url.pathname)));
  } catch { return false; }
}

export function validateMember(raw, id) {
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(id)) throw new Error('Use a lowercase member filename with letters, digits or hyphens.');
  if (!raw || Array.isArray(raw) || typeof raw !== 'object' || Object.keys(raw).some(key => !fields.has(key))) {
    throw new Error('A member must be an object containing only name, profile, focus, bio, avatar and order.');
  }
  const name = text(raw.name, 60, 'name');
  let url;
  try { url = new URL(raw.profile); } catch { throw new Error('Invalid Git profile URL.'); }
  const match = url.pathname.match(/^\/([A-Za-z0-9][A-Za-z0-9._-]{0,63})\/?$/);
  if (url.protocol !== 'https:' || !hosts.has(url.hostname) || url.port || url.username || url.password || url.search || url.hash || !match) {
    throw new Error('Use a direct HTTPS user profile on MagicBean Git, GitHub, GitLab or Codeberg.');
  }
  if (raw.avatar !== undefined && !validAvatar(raw.avatar)) throw new Error('Invalid profile avatar URL.');
  const order = raw.order ?? 1000;
  if (!Number.isSafeInteger(order) || order < 0 || order > 10000) throw new Error('Order must be an integer from 0 to 10000.');
  const focus = raw.focus ?? [];
  if (!Array.isArray(focus) || focus.length > 3) throw new Error('Use at most three focus areas.');
  const labels = focus.map(value => text(value, 32, 'focus area'));
  if (new Set(labels.map(value => value.toLowerCase())).size !== labels.length) throw new Error('Duplicate focus area.');
  return { id, name, order, avatar: raw.avatar || null, profile: `${url.origin}/${match[1]}`, handle: match[1], host: hosts.get(url.hostname),
    focus: labels, bio: raw.bio === undefined ? '' : text(raw.bio, 240, 'bio'),
    initial: Array.from(name)[0].toUpperCase() };
}

export async function readTeam(directory = new URL('../../team/', import.meta.url)) {
  const members = [];
  const profiles = new Set();
  for (const file of (await readdir(directory)).filter(file => file.endsWith('.json')).sort()) {
    try {
      const location = directory instanceof URL ? new URL(file, directory) : path.join(directory, file);
      const member = validateMember(JSON.parse(await readFile(location, 'utf8')), file.slice(0, -5));
      const key = member.profile.toLowerCase();
      if (profiles.has(key)) throw new Error('This Git profile is already listed.');
      profiles.add(key);
      members.push(member);
    } catch (error) { throw new Error(`Team roster ${file}: ${error.message}`); }
  }
  return members.sort((a, b) => a.order - b.order || a.name.localeCompare(b.name, 'en') || a.id.localeCompare(b.id, 'en'));
}
