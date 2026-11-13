import { sectionsAt } from './format-manual.mjs';

const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const slug = value => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

// Choices and defaults have one maintained source. Fail on structural drift instead
// of publishing an empty/partial catalogue. Escaped table pipes remain cell content.
export function readGraphicsSettings(readme, menu) {
  const section = sectionsAt(readme, 2).find(section => section.title === 'In-game menu');
  const table = section?.body.match(/^\| Tab \| Option \| Choices \| Default \|\r?\n\|[-| :]+\|\r?\n((?:\|[^\n]+\r?\n?)+)/m);
  if (!table) throw new Error('Missing README in-game menu table');
  let tab = '';
  const settings = [];
  for (const line of table[1].trim().split('\n')) {
    const cells = line.trim().slice(1, -1).split(/(?<!\\)\|/).map(cell => cell.trim().replaceAll('\\|', '|'));
    if (cells.length !== 4) throw new Error('Changed README menu table structure');
    tab = cells[0] || tab;
    if (tab !== 'Graphics') continue;
    const [, name, choices, defaultValue] = cells;
    if (!name || !choices || !defaultValue) throw new Error('Incomplete README graphics setting');
    settings.push({ name, choices, defaultValue, restart: /after a restart/i.test(choices) });
  }
  if (!settings.length) throw new Error('No Graphics settings in README');

  // Read only names and section headings from the source row table, never evaluate
  // C++ or guess hardware-dependent choices from its callbacks.
  const rows = menu.match(/Row rows\[\]\s*=\s*\{([\s\S]*?)\n\};/)?.[1];
  if (!rows) throw new Error('Missing menu row table');
  const groups = [{ name: 'Quality preset', settings: [] }];
  const sourceNames = [];
  let recognizedRows = 0;
  for (const match of rows.matchAll(/\{TAB_GRAPHICS,\s*"([^"]+)",\s*(nullptr|&opts\.\w+),([\s\S]*?)\},/g)) {
    recognizedRows++;
    const [, name, value, rest] = match;
    if (value === 'nullptr') {
      if (!/,\s*-1,\s*true\s*$/.test(rest)) throw new Error(`Unrecognized graphics heading: ${name}`);
      groups.push({ name, settings: [] });
    } else {
      const setting = settings[sourceNames.length];
      if (setting?.name !== name) throw new Error(`README/menu graphics order mismatch at ${name}`);
      sourceNames.push(name);
      groups.at(-1).settings.push({ ...setting, id: `setting-${slug(name)}` });
    }
  }
  if (recognizedRows !== [...rows.matchAll(/\{TAB_GRAPHICS,/g)].length || sourceNames.length !== settings.length || new Set(sourceNames).size !== settings.length || groups.some(group => !group.settings.length)) {
    throw new Error('README/menu graphics coverage mismatch');
  }
  return groups.map(group => ({ ...group, id: `settings-${slug(group.name)}` }));
}

export async function formatGraphicsSettings(text, readme, menu, processor) {
  const marker = '<!-- graphics-settings -->';
  if (text.split(marker).length !== 2) throw new Error('Expected one graphics-settings insertion marker');
  const groups = readGraphicsSettings(readme, menu);
  const count = groups.reduce((sum, group) => sum + group.settings.length, 0);
  const render = async value => (await processor.render(value)).code;
  const rendered = [];
  for (const group of groups) {
    const cards = [];
    for (const setting of group.settings) {
      cards.push(`<article class="graphics-card" data-setting>
<h4 id="${setting.id}"><a href="#${setting.id}">${escape(setting.name)}</a></h4>
<div class="graphics-default"><span>Default</span>${await render(setting.defaultValue)}</div>
${setting.restart ? '<p class="graphics-restart">After a restart</p>' : ''}
<details><summary>Choices &amp; requirements</summary><div>${await render(setting.choices)}</div></details>
</article>`);
    }
    rendered.push(`<section class="graphics-group" data-settings-group="${group.id}">
<h3 id="${group.id}">${escape(group.name)}</h3>
<div class="graphics-grid">${cards.join('\n')}</div>
</section>`);
  }
  return text.replace(marker, `<div class="graphics-settings" data-graphics-settings>
<form class="graphics-filters" data-settings-filters hidden role="search" aria-label="Graphics settings">
<label>Find a setting<input type="search" name="query" placeholder="Try DLSS, HDR or frame rate" aria-controls="graphics-results" /></label>
<label>Section<select name="group" aria-controls="graphics-results"><option value="">All sections</option>${groups.map(group => `<option value="${group.id}">${escape(group.name)}</option>`).join('')}</select></label>
<button type="reset">Clear filters</button>
</form>
<p class="graphics-count" data-settings-count role="status" aria-live="polite">${count} settings. Open a card to see its choices.</p>
<p data-settings-empty hidden>No settings match. Try another term or clear the filters.</p>
<div id="graphics-results">${rendered.join('\n')}</div>
</div>`);
}
