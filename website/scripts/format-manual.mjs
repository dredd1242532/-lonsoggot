// Presentation only: keep every source section, command and link in the imported README.
// Split actual Markdown headings, never heading-like lines inside fenced code samples.
export function sectionsAt(text, level) {
  const sections = [{ title: '', heading: '', body: '' }];
  let fence = null;
  for (const line of text.match(/[^\n]*\n|[^\n]+$/g) || []) {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = null;
      sections.at(-1).body += line;
      continue;
    }
    const heading = !fence && line.match(new RegExp(`^#{${level}} ([^\\n]+)\\n?$`));
    if (heading) sections.push({ title: heading[1], heading: line, body: '' });
    else sections.at(-1).body += line;
  }
  return sections;
}
const wrap = (css, content) => `\n<div class="${css}">\n\n${content.trim()}\n\n</div>\n`;
const fold = (label, body) => `\n<details class="manual-detail">\n<summary>${label}</summary>\n\n${body.trim()}\n\n</details>\n`;
export function formatRequirements(body) {
  // Keep the canonical Markdown table intact, with a keyboard-accessible scroll area on narrow screens.
  return body.replace(/(^\|[^\n]+\n(?:\|[^\n]+(?:\n|$))+)/m, table =>
    '\n<div class="specs-scroll" role="region" aria-label="Minimum and recommended system requirements" tabindex="0">\n\n' + table.trim() + '\n\n</div>\n');
}
const jumps = `<nav class="manual-jumps" aria-label="Installation shortcuts">
<a href="#what-you-need"><span>01 / CHECK YOUR SETUP</span><strong>Requirements</strong><small>Game version, hardware and space</small></a>
<a href="#on-windows"><span>02 / CHOOSE YOUR PLATFORM</span><strong>Windows ↘</strong><small>Install, build and launch</small></a>
<a href="#on-linux"><span>02 / CHOOSE YOUR PLATFORM</span><strong>Linux &amp; SteamOS ↘</strong><small>AppImage setup and permissions</small></a>
</nav>
<nav class="manual-shortcuts" aria-label="Manual shortcuts"><a href="#updating">Updating &amp; saves</a><a href="#in-game-menu">Settings &amp; controls</a><a href="#if-something-goes-wrong">Troubleshooting</a><a href="#build-from-source">Build from source</a></nav>`;

function installation(body) {
  return sectionsAt(body, 3).map(section => {
    if (!section.title) return wrap('manual-intro', section.body) + jumps;
    const content = section.heading + '\n' + section.body;
    if (section.title === 'What you need') return wrap('manual-requirements', section.heading + '\n' + formatRequirements(section.body));
    if (section.title === 'On Windows') return wrap('manual-platform', content);
    if (section.title === 'On Linux') {
      const at = section.body.indexOf('When the desktop gives the launcher its own systemd service,');
      const linux = at < 0 ? section.body : section.body.slice(0, at) + fold('Advanced Linux setup: permissions and command line', section.body.slice(at));
      return wrap('manual-platform', section.heading + '\n' + linux);
    }
    if (['Where the files go', 'What Longshot connects to'].includes(section.title)) {
      return wrap('manual-chapter', section.heading + fold('Read the details', section.body));
    }
    return wrap('manual-chapter', content);
  }).join('\n');
}

export function formatManual(source) {
  const sections = sectionsAt(source, 2);
  const install = sections.find(section => section.title === 'Install and play');
  if (!install) throw new Error('The manual needs an Install and play section for its presentation.');
  const labels = {
    'Project status': 'See compatibility and known limitations',
    'In-game menu': 'Explore settings, graphics options and controls',
    'Mods': 'Read the modding instructions',
    'Build from source': 'Open the developer build and command-line reference',
    'How it works': 'Read about native recompilation',
    'Want to help?': 'Explore ways to contribute',
    'Credits': 'View acknowledgements',
    'License': 'Read the licence and notices',
  };
  const intro = sections[0].body.replace(/<h1 align="center">/, '<div class="manual-project-mark" align="center">')
    .replace('</h1>', '</div>').replace(/<h4 align="center">/, '<p align="center">').replace('</h4>', '</p>');
  const reference = sections.filter(section => section.title && section !== install).map(section =>
    wrap('manual-chapter', section.heading + fold(labels[section.title] || 'Read this section', section.body))).join('\n');
  return wrap('manual-guide', install.heading + installation(install.body) +
    `<div class="manual-toolbar" data-manual-controls hidden><button type="button" data-manual-expand>Expand all details</button><button type="button" data-manual-collapse>Collapse details</button></div>\n` +
    fold('About Longshot and this project', intro) + reference);
}

export function formatInstallHub(source) {
  return wrap('install-hub', sectionsAt(source, 2).map(section => section.title
    ? wrap('install-hub-card', section.heading + '\n' + section.body) : section.body).join('\n'));
}

export function formatSaveGuide(source) {
  const groups = sectionsAt(source, 2);
  const jumps = `<nav class="manual-jumps saves-jumps" aria-label="Save guide shortcuts"><a href="#easy-beans"><span>START HERE</span><strong>Easy beans</strong><small>Launcher buttons and everyday steps</small></a><a href="#advanced-beans"><span>WHEN YOU NEED MORE CONTROL</span><strong>Advanced beans</strong><small>Reference details and extra options</small></a></nav>`;
  return wrap('manual-guide saves-guide', groups.map(group => {
    if (!group.title) return wrap('manual-intro', group.body) + jumps;
    const advanced = group.title === 'Advanced beans';
    const sections = sectionsAt(group.body, 3).map(section => {
      if (!section.title) return section.body;
      return wrap(advanced ? 'manual-chapter' : 'save-step-card', section.heading + '\n' +
        (advanced ? fold(`Open: ${section.title}`, section.body) : section.body));
    }).join('\n');
    const controls = advanced ? '<div class="manual-toolbar" data-manual-controls hidden><button type="button" data-manual-expand>Expand advanced details</button><button type="button" data-manual-collapse>Collapse details</button></div>\n' : '';
    return group.heading + '\n' + controls + sections;
  }).join('\n\n'));
}
