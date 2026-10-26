import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/reportReturns.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const context = {exports: {}};
runInNewContext(compiled, context);
const {curveInitial} = context.exports;


test('new reports preserve first day loss and match final metric', () => {
  const points = [{equity: 8500, benchmark: 5000}, {equity: 8500, benchmark: 5000}];
  const initial = curveInitial(points, -.15, 10000);
  assert.equal(initial, 10000);
  assert.ok(Math.abs(points[0].equity / initial - 1 + .15) < 1e-12);
  assert.equal(curveInitial(points, -.5, 10000, 'benchmark'), 10000);
});

test('legacy reports recover original capital without changing saved metrics', () => {
  const points = [{equity: 8500, benchmark: 10000}, {equity: 9000, benchmark: 11000}];
  assert.equal(curveInitial(points, -.1), 10000);
  assert.equal(curveInitial(points, .1, undefined, 'benchmark'), 10000);
});

test('invalid or empty series do not silently rebase to first close', () => {
  assert.equal(curveInitial([], 0), undefined);
  assert.equal(curveInitial([{equity: 10}], -1), undefined);
  assert.equal(curveInitial([{equity: 10}], NaN), undefined);
});
