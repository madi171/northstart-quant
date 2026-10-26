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
const components = {Badge: ({value}) => React.createElement('span', null, value), pct: x => (x * 100).toFixed(2) + '%', time: x => x || '—'};
const {RunRow} = load('StrategyMonitorPanel.tsx', {'./api': {api: () => Promise.reject(new Error('no network'))}, './components': components, './strategyMonitor': logic});
const run = (overrides = {}) => ({
  mode: 'demo', environment: 'paper', asset_id: 'asset-1', symbol: 'US.SPY', currency: 'USD',
  account_id: 'paper-demo-USD', job_counts: {}, order_counts: {}, latest_attempt: null,
  latest_signal: null, latest_order: null, latest_fill: null, ...overrides,
});

function buttons(element) {
  if (!element || typeof element !== 'object') return [];
  if (Array.isArray(element)) return element.flatMap(buttons);
  if (element.type === 'button') return [element];
  return buttons(element.props?.children);
}

test('all approved versions count even without runs; revoked pending orders remain visible', () => {
  const data = {strategies: [
    {status: 'approved', runs: []},
    {status: 'approved', runs: [run({job_counts: {queued: 2, running: 1}, order_counts: {pending: 3}})]},
    {status: 'candidate', runs: [run({order_counts: {pending: 1}})]},
  ]};
  assert.deepEqual({...logic.monitorTotals(data)}, {approved: 2, inProgress: 3, pending: 4});
});

test('failed latest attempt stays failed even with an older successful signal', () => {
  const item = run({latest_attempt: {status: 'failed', error: 'unavailable'}, latest_signal: {target_weight: 0.1}});
  assert.equal(logic.runState(item).text, '最近信号失败');
  assert.equal(logic.runState(run({latest_attempt: {status: 'running'}})).text, '信号计算中');
  assert.equal(logic.runState(run()).text, '尚无信号记录');
});

test('monitor row distinguishes null target from a real zero and links paper orders to its mode', () => {
  const item = run({
    mode: 'live', job_counts: {succeeded: 1}, order_counts: {pending: 1},
    latest_signal: {job_id: 'job-1', finished_at: '2026-10-25', target_weight: 0, quote_date: '2026-10-24', source_name: 'local', message: null, quote_stale: false},
    latest_order: {status: 'pending', side: 'buy', quantity: 2, created_at: '2026-10-25'},
  });
  let destination;
  const props = {run: item, onOpenJob: () => {}, onNavigate: (page, mode) => {destination = [page, mode];}};
  const element = RunRow(props);
  const html = renderToStaticMarkup(element);
  assert.match(html, /真实行情 \/ 虚拟盘/);
  assert.match(html, /目标仓位 0.00%/);
  assert.match(html, /1 笔待确认/);
  assert.match(html, /行情来源：local/);
  const orderButton = buttons(element).find(button => button.props.children.some?.(child => typeof child === 'string' && child.includes('查看模拟订单')));
  assert.ok(orderButton);
  orderButton.props.onClick();
  assert.deepEqual(destination, ['trading', 'live']);
  assert.match(renderToStaticMarkup(RunRow({...props, run: run({latest_signal: {...item.latest_signal, target_weight: null}})})), /目标权重未记录/);
});
