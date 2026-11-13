import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { contentSources } from '../content-sources.mjs';
import { getSiteConfig } from '../site.config.mjs';
import { assertReviewedSources } from '../scripts/verify-compatibility-sources.mjs';

test('Forgejo source links retain the repository and slash-containing branch', () => {
  const config = getSiteConfig({});
  assert.equal(config.origin, 'https://magicbean.dev');
  assert.equal(config.projectRepository, 'https://git.magicbean.dev/MagicBeanDev/Longshot');
  assert.equal(config.editBase, 'https://git.magicbean.dev/bibiburl/Longshot/_edit/docs/longshot-website/');
  const other = getSiteConfig({ DOCS_SOURCE_REPOSITORY: 'someone/Longshot', DOCS_SOURCE_BRANCH: 'docs/example' });
  assert.equal(other.editBase, 'https://git.magicbean.dev/someone/Longshot/_edit/docs/example/');
  assert.throws(() => getSiteConfig({ DOCS_SITE_URL: 'https://example.com/subdirectory' }));
  assert.throws(() => getSiteConfig({ DOCS_SOURCE_REPOSITORY: '../wrong/path' }));
});

test('README requirements and GPU-status changes require a calculator review', async () => {
  const readme = await readFile(new URL('../../README.md', import.meta.url), 'utf8');
  const review = JSON.parse(await readFile(new URL('../compatibility-review.json', import.meta.url), 'utf8'));
  assert.doesNotThrow(() => assertReviewedSources(readme, review));
  for (const [before, after] of [['glibc 2.35', 'glibc 2.40'], ['NVIDIA cards', 'NVIDIA graphics cards']]) {
    assert(readme.includes(before));
    assert.throws(() => assertReviewedSources(readme.replace(before, after), review), /changed since compatibility review/);
  }
  assert.doesNotThrow(() => assertReviewedSources(readme + '\nUnrelated footer edit.\n', review));
});


test('reference and saves pages import the maintained originals', () => {
  assert.equal(contentSources.get('README.md'), 'reference/manual');
  assert.equal(contentSources.get('CONTRIBUTING.md'), 'reference/contributing');
  assert.equal(contentSources.get('docs/saves.md'), 'guides/saves');
  assert(![...contentSources.keys()].some(source => source.includes('/site-')));
});
