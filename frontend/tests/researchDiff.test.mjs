import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/researchDiff.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const context = {exports: {}};
runInNewContext(compiled, context);
const parse = diff => JSON.parse(JSON.stringify(context.exports.parseUnifiedDiff(diff)));

test('unified diff preserves code and before/after line numbers', () => {
  const lines = parse('--- incumbent\n+++ candidate\n@@ -3,2 +3,3 @@\n x = 1\n-return 0\n+weight = 0.3\n+return weight');
  assert.deepEqual(lines.map(row => row.kind), ['header', 'header', 'hunk', 'context', 'removed', 'added', 'added']);
  assert.deepEqual(lines.slice(3).map(row => [row.before, row.after]), [[3, 3], [4, null], [null, 4], [null, 5]]);
  assert.equal(lines[5].text, '+weight = 0.3');
});

test('empty code diff remains empty for parameter-only changes', () => {
  assert.deepEqual(parse(''), []);
});

test('hunks reset line numbers and retain content as plain text', () => {
  const lines = parse('@@ -1 +1 @@\n-<script>\n+<tag>\n@@ -10,0 +11,1 @@\n+return 0\n');
  assert.equal(lines[2].text, '+<tag>');
  assert.equal(lines.at(-1).after, 11);
  assert.equal(lines.at(-1).before, null);
});
