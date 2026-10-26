import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/dataMode.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
function load(storage) {
  const context = {exports: {}, localStorage: storage};
  runInNewContext(compiled, context);
  return context.exports;
}

test('first visit and invalid saved values default to historical data', () => {
  for (const value of [null, '', 'invalid', 'historical']) {
    assert.equal(load({getItem: () => value}).readDataMode(), 'historical');
  }
});

test('remembers an explicit environment across reloads', () => {
  const values = new Map();
  const storage = {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
  for (const mode of ['historical', 'demo', 'live']) {
    const api = load(storage);
    api.saveDataMode(mode);
    assert.equal(values.get(api.DATA_MODE_KEY), mode);
    assert.equal(load(storage).readDataMode(), mode);
  }
});

test('unavailable browser storage does not break rendering or selection', () => {
  const api = load({getItem() {throw new Error('denied');}, setItem() {throw new Error('denied');}});
  assert.equal(api.readDataMode(), 'historical');
  assert.doesNotThrow(() => api.saveDataMode('demo'));
  assert.equal(load(undefined).readDataMode(), 'historical');
});

test('research uses demo only when explicitly selected, never as live fallback', () => {
  const {researchDataMode} = load();
  assert.equal(researchDataMode('demo'), 'demo');
  for (const mode of ['historical', 'live', '', 'invalid']) {
    assert.equal(researchDataMode(mode), 'historical');
  }
});
