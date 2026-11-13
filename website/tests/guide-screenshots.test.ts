import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { repoAssets, repositoryAssets } from '../src/repo-docs/assets.ts';
import { renderPage } from '../src/repo-docs/pages.ts';

// A filesystem URL must be decoded: otherwise a checkout under "New Volume"
// writes into a literal New%20Volume directory and serves broken image URLs.
test('guide images copy unchanged into a public directory containing spaces', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'longshot guide assets '));
  try {
    await repoAssets().hooks['astro:config:setup']({ config: { publicDir: pathToFileURL(dir + path.sep) } } as never);
    for (const [source, target] of Object.entries(repositoryAssets)) {
      assert.deepEqual(await readFile(path.join(dir, target)), await readFile(path.resolve('..', source)), source);
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('canonical guide images resolve to this site, with descriptive alt text', async () => {
  for (const [page, files] of Object.entries({
    install: ['launcher-home.png'],
    'saves/transfer': ['launcher-saves.png', 'launcher-import.png'],
    settings: ['settings-graphics.png'],
  })) {
    const { markdown } = await renderPage(page);
    for (const file of files) {
      assert.ok(markdown.includes(`/docs/images/guide/${file}`), `${page}: ${file}`);
      const image = markdown.split('\n').find(line => line.includes(file));
      assert.match(image ?? '', /^!\[[^\]]{20,}\]\(\/docs\/images\/guide\//);
      assert.ok(!image?.includes('git.magicbean.dev'));
    }
  }
});
