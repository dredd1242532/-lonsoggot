import { readFile, readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { siteConfig } from '../site.config.mjs';
import { contentSources } from '../content-sources.mjs';
import { verifyCompatibilitySources } from './verify-compatibility-sources.mjs';
import { readTeam } from '../src/lib/team.mjs';
import { readProblemSearchHash } from '../src/lib/problem-search.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(site, 'dist');
await verifyCompatibilitySources(path.join(site, '..'));
const origin = siteConfig.origin;
const base = siteConfig.base;
const bug = siteConfig.issueUrl;
const files = [];
async function walk(dir) {
  for (const item of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, item.name);
    if (item.isDirectory()) await walk(full);
    else if (item.name.endsWith('.html')) files.push(full);
  }
}
await walk(dist);
const publishedRoutes = new Set([...contentSources.values(), '', 'about', 'team', 'antivirus', 'compatibility', 'progress', '404.html']);
for (const file of files) {
  const route = path.relative(dist, file).replaceAll(path.sep, '/').replace(/\/?index\.html$/, '');
  assert(publishedRoutes.has(route), `Unexpected published page: ${route}`);
}
const ids = new Map();
for (const file of files) {
  const html = await readFile(file, 'utf8');
  ids.set(file, new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1])));
}
const errors = [];
let links = 0;
for (const file of files) {
  const html = await readFile(file, 'utf8');
  const route = base + path.relative(dist, file).replaceAll(path.sep, '/').replace(/index\.html$/, '');
  assert(!/\/Hookshot\//.test(html.replace(/https:\/\/github\.com\/[^\s"<>]+/g, "")), `Retired local website route: ${route}`);
  assert(html.includes(`href="${bug}"`), `Missing Report a bug link: ${route}`);
  assert(/<h1\b/.test(html), `Missing page heading: ${route}`);
  for (const [, editUrl] of html.matchAll(/href="(https:\/\/git\.magicbean\.dev\/[^\"]+\/_edit\/[^\"]+)"/g)) {
    assert(editUrl.startsWith(siteConfig.editBase), `Wrong edit repository: ${editUrl}`);
    const source = editUrl.slice(siteConfig.editBase.length);
    await stat(path.join(site, '..', source));
  }
  for (const match of html.matchAll(/<(a|img|script|link)\b[^>]*?\b(?:href|src)="([^"]+)"/g)) {
    const [, tag, raw] = match;
    if (/^(?:data:|mailto:|tel:|javascript:)/.test(raw)) continue;
    const url = new URL(raw.replaceAll('&amp;', '&'), origin + route);
    if (url.origin !== origin) continue;
    // Team credit intentionally links to the separate main website on the same origin.
    if (tag === 'a' && url.href === 'https://magicbean.dev/') continue;
    // Astro emits a /404/ canonical URL but the static build emits /404.html.
    if (tag === 'link' && file.endsWith('/404.html') && url.pathname === base + '404/') continue;
    if (!url.pathname.startsWith(base)) {
      errors.push(`${route}: URL escapes project base: ${raw}`);
      continue;
    }
    let target = path.join(dist, decodeURIComponent(url.pathname.slice(base.length)));
    try {
      if ((await stat(target)).isDirectory()) target = path.join(target, 'index.html');
      await stat(target);
      links++;
      const anchor = url.pathname === base + 'progress/' && readProblemSearchHash(url.hash)
        ? 'problem-search' : decodeURIComponent(url.hash.slice(1));
      if (tag === 'a' && url.hash && ids.has(target) && !ids.get(target).has(anchor)) {
        errors.push(`${route}: missing anchor: ${raw}`);
      }
    } catch { errors.push(`${route}: missing target: ${raw}`); }
  }
}
const original = await readFile(path.join(site, '../assets/longshot.png'));
assert(original.equals(await readFile(path.join(dist, 'assets/longshot.png'))), 'Original README logo changed');
for (const asset of ['longshot.png', 'teary-elf-bean.png', 'grappling-hook-bean.png', 'glowing-fairy-bean.png', 'magicbean-green.png', 'magicbean-red.png', 'magicbean-blue.png', 'magicbean-team-logo.webp', 'PROVENANCE.txt']) {
  assert((await readFile(path.join(site, '../assets/branding', asset))).equals(await readFile(path.join(dist, 'branding', asset))), `Supplied branding changed: ${asset}`);
}
const commonIssues = await readFile(path.join(dist, 'guides/common-issues/index.html'), 'utf8');
assert(commonIssues.includes(`href="${base}antivirus/"`), 'Download safety link must stay on the site');
const team = await readFile(path.join(dist, 'team/index.html'), 'utf8');
for (const member of await readTeam()) assert(team.includes(`href="${member.profile}"`), `Missing team profile: ${member.id}`);
assert(team.includes('id="add-your-profile"'), 'Roster contribution section missing');
assert(team.includes(siteConfig.editBase.replace('/_edit/', '/src/branch/') + 'website/team/README.md'), 'Roster instructions must target the website branch');
const home = await readFile(path.join(dist, 'index.html'), 'utf8');
assert(!/hookshot/i.test(home), 'Retired front-page branding');
assert(home.includes('Longshot by team magicbean'), 'Longshot page identity missing');
assert(home.includes(`${base}branding/longshot.png`), 'Longshot artwork missing');
assert(home.includes(`${base}branding/grappling-hook-bean.png`), 'Main grappling bean artwork missing');
assert(home.includes(`${base}branding/glowing-fairy-bean.png`), 'Fairy bean Easter egg artwork missing');
assert(home.includes(`${base}branding/magicbean-green.png`), 'Support bean missing');
assert(/class="[^"]*\badventure-hero\b[^"]*"/.test(await readFile(path.join(dist, 'index.html'), 'utf8')), 'Custom landing page missing');
assert((await readdir(path.join(dist, 'pagefind'))).some((f) => f === 'pagefind.js'), 'Search index missing');
if (errors.length) {
  console.error([...new Set(errors)].join('\n'));
  process.exitCode = 1;
} else {
  console.log(`PASS: ${files.length} pages, ${links} local links/assets, all local anchors, project base path, Report a bug links, search index, and unchanged original logo.`);
}
