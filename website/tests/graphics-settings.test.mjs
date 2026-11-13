import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createMarkdownProcessor } from '@astrojs/markdown-remark';
import { readGraphicsSettings, formatGraphicsSettings } from '../scripts/graphics-settings.mjs';
import { matchesSetting, installGraphicsSettings } from '../src/lib/graphics-settings.mjs';

const readme = await readFile(new URL('../../README.md', import.meta.url), 'utf8');
const menu = await readFile(new URL('../../gpu/vk/vk_menu.cpp', import.meta.url), 'utf8');
const guide = await readFile(new URL('../../docs/graphics-and-controls.md', import.meta.url), 'utf8');
const processor = await createMarkdownProcessor();

test('all Graphics rows follow the source sections and preserve README choices/defaults', () => {
  const groups = readGraphicsSettings(readme, menu);
  const settings = groups.flatMap(group => group.settings);
  const lines = readme.slice(readme.indexOf('| Graphics |'), readme.indexOf('| Audio |')).trim().split('\n');
  assert.equal(settings.length, lines.length);
  settings.forEach((setting, i) => {
    const [, , name, choices, value] = lines[i].split('|').map(value => value.trim());
    assert.deepEqual([setting.name, setting.choices, setting.defaultValue], [name, choices, value]);
  });
  assert.equal(groups.find(group => group.name === 'HDR').settings[0].name, 'HDR');
  assert.equal(settings.find(setting => setting.name === 'Output resolution').restart, true);
  assert.equal(settings.find(setting => setting.name === 'Game frame rate cap').restart, false);
  assert(!settings.some(setting => setting.name === 'Volume'));
});

test('upstream value and section-name edits flow through without another settings list', () => {
  const groups = readGraphicsSettings(readme.replace('| 2048x1152 |', '| Screen size |'), menu.replace('"Resolution and upscaling", nullptr', '"Picture size", nullptr'));
  assert.equal(groups.find(group => group.name === 'Picture size').settings.find(setting => setting.name === 'Output resolution').defaultValue, 'Screen size');
});

test('missing tables, incomplete cells, added/removed/reordered rows stop publication', () => {
  assert.throws(() => readGraphicsSettings('', menu), /Missing README/);
  assert.throws(() => readGraphicsSettings(readme, ''), /Missing menu/);
  assert.throws(() => readGraphicsSettings(readme.replace('| | Sharpness |', '| | Gone |'), menu), /mismatch/);
  assert.throws(() => readGraphicsSettings(readme.replace(/^\| \| Sharpness .*\n/m, ''), menu), /mismatch/);
  assert.throws(() => readGraphicsSettings(readme.replace('| 2048x1152 |', '| |'), menu), /Incomplete/);
  assert.throws(() => readGraphicsSettings(readme.replace('| | Sharpness |', '| | New cell | Sharpness |'), menu), /structure/);
  assert.throws(() => readGraphicsSettings(readme, menu.replace('"Resolution and upscaling", nullptr', '"Resolution and upscaling", NULL')), /coverage mismatch/);
});

test('escaped pipes remain choices and do not become extra cells', () => {
  const groups = readGraphicsSettings(readme.replace('0 to 100%, in steps of 10', 'Off \\| On'), menu);
  assert.equal(groups.flatMap(group => group.settings).find(setting => setting.name === 'Sharpness').choices, 'Off | On');
});

test('cards retain markup, stable anchors, conditions and a readable no-JS fallback', async () => {
  const result = await formatGraphicsSettings(guide, readme, menu, processor);
  assert.equal((result.match(/class="graphics-card"/g) || []).length, readGraphicsSettings(readme, menu).flatMap(group => group.settings).length);
  assert(result.includes('id="setting-output-resolution"'));
  assert(result.includes('<kbd>F11</kbd>'));
  assert(result.includes('VK_NV_low_latency2'));
  assert(result.includes('only in builds with'));
  assert(result.includes('data-settings-filters hidden'));
  assert(!result.includes('<!-- graphics-settings -->'));
  for (const heading of guide.match(/^## .+$/gm)) assert(result.includes(heading));
  await assert.rejects(formatGraphicsSettings('no marker', readme, menu, processor), /insertion marker/);
});

test('search accepts case, whitespace and terms in any order without regex interpretation', () => {
  assert(matchesSetting('NVIDIA DLSS Frame Generation', '  generation NVIDIA '));
  assert(matchesSetting('Minimum (4GB)', '(4gb)'));
  assert(matchesSetting('NVIDIA Reflex', ''));
  assert(!matchesSetting('NVIDIA Reflex', 'AMD'));
  assert(!matchesSetting('NVIDIA Reflex', '.*'));
});

// A small DOM fixture exercises user-action sequences, not browser layout/native
// focus behavior. Those still need the rendered browser review.
function catalogueFixture() {
  const events = object => Object.assign(object, {
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
    emit(type, event = {}) { for (const fn of this.listeners[type] || []) fn(event); },
  });
  const card = (id, textContent) => {
    const details = { open: false, setAttribute(name) { if (name === 'open') this.open = true; } };
    const result = { id, textContent, hidden: false, querySelector: () => details };
    result.heading = { closest: () => result, scrollIntoView() {} };
    return result;
  };
  const cards = [card('setting-upscaler', 'Upscaler NVIDIA DLSS'), card('setting-sharpness', 'Sharpness 0 to 100%'), card('setting-hdr', 'HDR Off Expanded Native')];
  const groups = [
    { dataset: { settingsGroup: 'picture' }, querySelectorAll: () => cards.slice(0, 2) },
    { dataset: { settingsGroup: 'hdr' }, querySelectorAll: () => cards.slice(2) },
  ];
  const fields = { query: { value: '' }, group: { value: '' } };
  const form = events({ hidden: true, elements: { namedItem: name => fields[name] }, reset() { this.emit('reset'); } });
  const count = {}, empty = {};
  const root = events({
    querySelector: selector => ({ form, '[data-settings-count]': count, '[data-settings-empty]': empty })[selector],
    querySelectorAll: selector => selector === '[data-setting]' ? cards : groups,
    contains: target => cards.some(card => card.heading === target),
  });
  const document = { querySelector: () => root, getElementById: id => cards.find(card => card.id === id)?.heading };
  const window = events({ location: new URL('https://example.test/Longshot/guides/graphics-and-controls/'), requestAnimationFrame: fn => fn() });
  installGraphicsSettings(document, window);
  const search = (query, group = '') => { fields.query.value = query; fields.group.value = group; form.emit('input'); };
  return { cards, groups, fields, form, count, empty, root, window, search };
}

test('search and sections combine; reset restores all cards and previous disclosures', () => {
  const f = catalogueFixture();
  assert.equal(f.form.hidden, false);
  f.cards[1].querySelector().open = true;
  f.search('NVIDIA');
  assert.deepEqual(f.cards.map(card => card.hidden), [false, true, true]);
  assert.equal(f.cards[0].querySelector().open, true);
  assert.equal(f.groups[1].hidden, true);
  assert.equal(f.count.textContent, '1 of 3 settings shown.');
  f.search('NVIDIA', 'hdr');
  assert.equal(f.empty.hidden, false);
  assert.equal(f.count.textContent, '0 of 3 settings shown.');
  f.form.reset();
  assert(f.cards.every(card => !card.hidden));
  assert.deepEqual(f.cards.map(card => card.querySelector().open), [false, true, false]);
});

test('deep links clear filters, reveal a card and reopen the same permalink', () => {
  const f = catalogueFixture();
  f.search('NVIDIA');
  f.window.location.hash = '#setting-hdr';
  f.window.emit('hashchange');
  assert(f.cards.every(card => !card.hidden));
  assert.equal(f.cards[2].querySelector().open, true);
  f.cards[2].querySelector().open = false;
  f.root.emit('click', { target: { closest: () => ({ href: f.window.location.href }) } });
  assert.equal(f.cards[2].querySelector().open, true);
});

test('printing exposes the whole catalogue then restores filters and disclosure state', () => {
  const f = catalogueFixture();
  f.cards[1].querySelector().open = true;
  f.search('NVIDIA');
  f.window.emit('beforeprint');
  assert(f.cards.every(card => !card.hidden && card.querySelector().open));
  f.window.emit('afterprint');
  assert.equal(f.fields.query.value, 'NVIDIA');
  assert.deepEqual(f.cards.map(card => card.hidden), [false, true, true]);
  f.form.reset();
  assert.deepEqual(f.cards.map(card => card.querySelector().open), [false, true, false]);
});
