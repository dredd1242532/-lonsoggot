import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { sectionsAt, formatManual, formatInstallHub, formatSaveGuide } from '../scripts/format-manual.mjs';

test('manual sections do not split headings inside backtick or tilde code fences', () => {
  const source = 'Introduction\n## First\n```sh\n## A shell comment\n```\n~~~text\n## Another example\n~~~\n## Second\nDone\n';
  const sections = sectionsAt(source, 2);
  assert.deepEqual(sections.map(section => section.title), ['', 'First', 'Second']);
  assert.equal(sections.map(section => section.heading + section.body).join(''), source);
});

test('manual presentation retains every source line except decorative heading tags', async () => {
  const source = await readFile(new URL('../../README.md', import.meta.url), 'utf8');
  const formatted = formatManual(source);
  const counts = new Map();
  for (const line of formatted.split('\n').map(line => line.trim()).filter(Boolean)) {
    counts.set(line, (counts.get(line) || 0) + 1);
  }
  const expected = source.replace('<h1 align="center">', '<div class="manual-project-mark" align="center">')
    .replace('</h1>', '</div>').replace('<h4 align="center">', '<p align="center">').replace('</h4>', '</p>');
  for (const line of expected.split('\n').map(line => line.trim()).filter(Boolean)) {
    assert((counts.get(line) || 0) > 0, `Source content lost: ${line}`);
    counts.set(line, counts.get(line) - 1);
  }
  assert(formatted.indexOf('## Install and play') < formatted.indexOf('## Project status'));
  assert.throws(() => formatManual('## Renamed installation section\nText'), /Install and play/);
});

test('installation hub retains source sections and links', () => {
  const source = 'Welcome\n\n## Windows\n[Instructions](../README.md#on-windows)\n\n## Linux\nChoose your platform.\n';
  const formatted = formatInstallHub(source);
  for (const line of source.split('\n').filter(Boolean)) assert(formatted.includes(line));
  assert.equal((formatted.match(/class="install-hub-card"/g) || []).length, 2);
});

test('save guide presentation retains commands, backup guidance and heading anchors', async () => {
  for (const source of ['saves', 'updates-and-saves']) {
    const markdown = await readFile(new URL(`../../docs/${source}.md`, import.meta.url), 'utf8');
    const formatted = formatSaveGuide(markdown);
    for (const line of markdown.split('\n').filter(line => line.trim())) {
      assert(formatted.includes(line), `${source}: source line lost: ${line}`);
    }
    assert(formatted.includes('href="#easy-beans"'));
    assert(formatted.includes('href="#advanced-beans"'));
    assert(formatted.includes('data-manual-expand'));
  }
});
