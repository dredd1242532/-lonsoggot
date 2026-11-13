import { sectionsAt } from './format-manual.mjs';

// Presentation labels only. All troubleshooting steps and links live in docs/common-issues.md.
const cards = new Map([
  ['The build rejects the game version', ['INSTALLATION', 'Game version rejected', 'Check your copy, update and setup steps.']],
  ['The game closes or shows a black screen', ['STARTUP', 'Won’t start or black screen', 'Check your graphics card, driver and startup error.']],
  ['Graphics are missing or corrupted', ['GRAPHICS', 'Something looks wrong', 'Missing walls, broken textures or odd lighting.']],
  ['The controller behaves differently than expected', ['CONTROLS', 'Buttons don’t feel right', 'Check your layout, bindings and button icons.']],
  ['Performance drops or stutters', ['PERFORMANCE', 'Slow or stuttering', 'Separate first-visit stutter from a repeat slowdown.']],
  ['An update or save transfer failed', ['SAVES & UPDATES', 'Update or save trouble', 'Protect your adventure before copying again.']],
  ['A download is flagged', ['DOWNLOADS', 'A security warning appeared', 'Check the source and the warning before continuing.']],
]);
const escape = text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');

export function formatCommonIssues(source) {
  const sections = sectionsAt(source, 2);
  const intro = sections.find(section => section.title === 'What’s going wrong?');
  const topics = sections.filter(section => section.title && section !== intro);
  if (!intro || topics.length !== cards.size || new Set(topics.map(section => section.title)).size !== cards.size) {
    throw new Error('Common issues topic structure changed; review the symptom cards');
  }
  const rendered = topics.map(section => {
    const labels = cards.get(section.title);
    const parts = sectionsAt(section.body, 3);
    const first = parts.find(part => part.title === 'Try this first');
    const stuck = parts.find(part => part.title === 'Still stuck?');
    if (!labels || !first?.body.trim() || !stuck?.body.trim() || parts.length !== 3 || parts[0].body.trim()) {
      throw new Error(`Common issues checklist structure changed: ${section.title}`);
    }
    // These source titles retain their existing Starlight/Git heading anchors.
    const id = section.title.toLowerCase().replaceAll(' ', '-');
    const [category, title, hint] = labels;
    return `<details class="issue-card">
<summary><h2 id="${id}"><span class="issue-category">${escape(category)}</span><span class="issue-title">${escape(title)}</span><span class="issue-hint">${escape(hint)}</span></h2></summary>
<div class="issue-content">
<p class="issue-step-label">Try this first</p>

${first.body.trim()}

<details class="issue-help"><summary>Still stuck?</summary>

${stuck.body.trim()}

</details>
</div>
</details>`;
  }).join('\n\n');
  return `<div class="manual-guide issues-guide">

${sections[0].body.trim()}

${intro.heading}
${intro.body.trim()}

<div class="issue-grid">

${rendered}

</div>
</div>\n`;
}
