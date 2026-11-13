// Presentation helpers for the repository's documents: every source section, command and link is kept.
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
export function formatInstallHub(source) {
  return wrap('install-hub', sectionsAt(source, 2).map(section => section.title
    ? wrap('install-hub-card', section.heading + '\n' + downloadButtons(section.body)) : section.body).join('\n'));
}

function downloadButtons(body) {
  return body.replace(/^(?:- \[[^\]]+\]\(\/download\/[^)\s]+\)\n)+/m, (list) => {
    const links = [...list.matchAll(/- \[([^\]]+)\]\(([^)\s]+)\)/g)];
    return `<div class="download-buttons">\n${links.map(([, text, url], i) =>
      `<a class="button${i < 2 ? ' primary' : ''}" href="${url}">${text}</a>`).join('\n')}\n</div>\n`;
  });
}
