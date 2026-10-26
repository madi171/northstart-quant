import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(name, overrides = {}) {
  const source = readFileSync(new URL('../src/' + name, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX}}).outputText;
  const context = {exports: {}, require: path => overrides[path] || require(path)};
  runInNewContext(compiled, context);
  return context.exports;
}
const logic = load('strategyMonitor.ts');
const components = {
  Badge: ({value}) => React.createElement('span', null, value),
  money: value => value == null ? '—' : value.toFixed(2),
  pct: value => value == null ? '—' : (value * 100).toFixed(2) + '%',
  time: value => value || '—',
};
const {paperReturn, paperDecisionRows, PaperTradingOverview} = load('PaperTradingOverview.tsx', {
  './api': {api: () => Promise.reject(new Error('offline'))},
  './components': components,
  './strategyMonitor': logic,
});
const emptyRun = {mode: 'demo', currency: 'USD', asset_id: 'a', account_id: 'paper-demo-USD', latest_attempt: null,
  latest_signal: null, latest_order: null, job_counts: {}, order_counts: {}};

test('returns are visible only for the matching account with fresh synchronized data', () => {
  const row = {mode: 'demo', currency: 'USD', complete: true, return_rate: 0, equity: 1000};
  assert.equal(paperReturn(row, 'demo', 'USD', false, ''), 0);
  assert.equal(paperReturn({...row, complete: false}, 'demo', 'USD', false, ''), null);
  assert.equal(paperReturn(row, 'live', 'USD', false, ''), null);
  assert.equal(paperReturn(row, 'demo', 'HKD', false, ''), null);
  assert.equal(paperReturn(row, 'demo', 'USD', true, ''), null);
  assert.equal(paperReturn(row, 'demo', 'USD', false, 'timeout'), null);
  assert.equal(paperReturn({...row, return_rate: null}, 'demo', 'USD', false, ''), null);
});

test('decision rows include approved without runs and exclude other currency and mode', () => {
  const monitor = {strategies: [
    {id: 'waiting', status: 'approved', runs: []},
    {id: 'active', status: 'approved', runs: [emptyRun, {...emptyRun, currency: 'CNY'}, {...emptyRun, mode: 'live'}]},
    {id: 'revoked', status: 'candidate', runs: [emptyRun]},
    {id: 'hidden', status: 'candidate', runs: []},
  ]};
  const rows = paperDecisionRows(monitor, 'demo', 'USD');
  assert.deepEqual(rows.map(({strategy}) => strategy.id), ['waiting', 'active', 'revoked']);
  assert.equal(rows[0].run, null);
  assert.equal(rows[1].run.account_id, 'paper-demo-USD');
  assert.equal(paperDecisionRows(null, 'demo', 'USD').length, 0);
});

test('historical mode does not present a paper account or live return', () => {
  const html = renderToStaticMarkup(React.createElement(PaperTradingOverview, {
    mode: 'historical', currency: 'USD', serverTime: '', onCurrency: () => {},
    onOpenJob: () => {}, onRunSignal: () => {}, canRun: false,
  }));
  assert.match(html, /历史回放没有模拟账户/);
  assert.doesNotMatch(html, /累计收益率/);
});
