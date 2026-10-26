import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/SignalLibrary.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX}}).outputText;
const context = {exports: {}, require: path => path === './api'
  ? {api: () => Promise.reject(new Error('offline')), download: () => {}}
  : path === './components' ? {time: value => value || '—'} : path === './signalLibrary.css' ? {} : require(path)};
runInNewContext(compiled, context);
const {SignalLibrary} = context.exports;

test('signals page offers manual sentiment and validated numeric feature inputs', () => {
  const markup = renderToStaticMarkup(React.createElement(SignalLibrary, {newsError: '', assets: [
    {id: 'a', symbol: 'US.TEST', name: '测试标的'},
  ], news: []}));
  assert.match(markup, /利好/);
  assert.match(markup, /利空/);
  assert.match(markup, /中性/);
  assert.match(markup, /首次实际可用时间/);
  assert.match(markup, /导出数值特征/);
  assert.match(markup, /US.TEST/);
  assert.match(markup, /不会自动下单/);
});

test('incoming news is unclassified until a user selects and saves a direction', () => {
  const markup = renderToStaticMarkup(React.createElement(SignalLibrary, {newsError: '', assets: [], news: [{
    title: '公司经营公告', provider: '公告来源', body: '待核对', first_seen_at: '2025-01-02T00:00:00Z', published_at: '2025-01-01T00:00:00Z', url: '',
  }]}));
  assert.match(markup, /资讯待标注/);
  assert.match(markup, /公司经营公告/);
  assert.match(markup, /不会自动判断利好或利空/);
});
