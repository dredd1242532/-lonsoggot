import test from 'node:test';
import assert from 'node:assert/strict';
import { problemSearchHash, readProblemSearchHash } from '../src/lib/problem-search.mjs';
test('front-page search round trips special characters inside the fragment', () => {
  const query = 'black screen & controller #123 / café?';
  const url = new URL('https://example.com/Hookshot/progress/');
  url.hash = problemSearchHash(query);
  assert.equal(url.search, '');
  assert.equal(readProblemSearchHash(url.hash).query, query);
});
test('plain header links preserve the existing query; other anchors do not activate search', () => {
  assert.deepEqual(readProblemSearchHash('#problem-search'), { query: null });
  assert.equal(readProblemSearchHash('#development'), null);
  assert.equal(problemSearchHash('  '), '#problem-search');
});
test('shared queries are bounded and treat markup as plain text', () => {
  assert.equal(readProblemSearchHash(problemSearchHash('x'.repeat(400))).query.length, 300);
  assert.equal(readProblemSearchHash(problemSearchHash('<script>')).query, '<script>');
});
