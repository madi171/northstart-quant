import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/BacktestProgressPanel.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX}}).outputText;
const context = {exports: {}, require: path => path === './backtestProgress.css' ? {} : require(path)};
runInNewContext(compiled, context);
const {BacktestProgressPanel, backtestProgress} = context.exports;
const job = (status, progress) => ({kind: 'backtest', status, error: '', result: progress ? {backtest_progress: progress} : {}});

test('queued and data preparation do not invent a percentage', () => {
  const queued = renderToStaticMarkup(React.createElement(BacktestProgressPanel, {job: job('queued')}));
  const loading = renderToStaticMarkup(React.createElement(BacktestProgressPanel, {job: job('running')}));
  assert.match(queued, /等待后台计算节点领取任务/);
  assert.match(loading, /历史行情准备中/);
  assert.doesNotMatch(loading, /aria-valuenow="100"/);
});

test('processing percentage is tied to bar count and 100 requires success', () => {
  const progress = {phase: 'simulating', completed: 50, total: 100, date: '2024-03-18'};
  const running = renderToStaticMarkup(React.createElement(BacktestProgressPanel, {job: job('running', progress)}));
  assert.match(running, /已计算 50 \/ 100 个交易日/);
  assert.match(running, /aria-valuenow="45"/);
  assert.match(renderToStaticMarkup(React.createElement(BacktestProgressPanel, {job: job('running', {...progress, phase: 'finalizing', completed: 100})})), /aria-valuenow="90"/);
  assert.match(renderToStaticMarkup(React.createElement(BacktestProgressPanel, {job: job('succeeded')})), /aria-valuenow="100"/);
  assert.equal(backtestProgress(job('running', {...progress, completed: 110})), null);
});

test('failed backtest does not appear completed, unrelated jobs have no panel', () => {
  const failed = renderToStaticMarkup(React.createElement(BacktestProgressPanel, {job: {...job('failed'), error: '数据加载失败'}}));
  assert.match(failed, /回测失败/);
  assert.doesNotMatch(failed, /aria-valuenow="100"/);
  assert.equal(renderToStaticMarkup(React.createElement(BacktestProgressPanel, {job: {kind: 'optimization', status: 'running'}})), '');
});
