export function problemSearchHash(query = '') {
  const value = String(query).trim().slice(0, 300);
  return value ? `#problem-search?${new URLSearchParams({ q: value })}` : '#problem-search';
}

export function readProblemSearchHash(hash) {
  const [anchor, ...rest] = hash.split('?');
  if (anchor !== '#problem-search') return null;
  const params = new URLSearchParams(rest.join('?'));
  return { query: params.has('q') ? params.get('q').slice(0, 300) : null };
}
