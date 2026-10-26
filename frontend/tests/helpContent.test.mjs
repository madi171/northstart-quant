import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/helpContent.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const context = {exports: {}};
runInNewContext(compiled, context);
const {helpArticles, helpDemos, helpManualMarkdown, searchHelp} = context.exports;

test('downloadable manual contains all chapters and full demo content', () => {
  const markdown = helpManualMarkdown();
  assert.ok(markdown.startsWith('# Northstar 操作手册'));
  for (const article of helpArticles) assert.ok(markdown.includes(`## ${article.title}`));
  for (const demo of helpDemos) assert.ok(markdown.includes(demo.content));
});

test('manual covers every functional destination and has valid chapter/demo references', () => {
  assert.equal(new Set(helpArticles.map(row => row.id)).size, helpArticles.length);
  assert.equal(new Set(helpDemos.map(row => row.id)).size, helpDemos.length);
  const destinations = new Set(helpArticles.map(row => row.destination));
  for (const page of ['overview','assets','strategies','backtests','autoresearch','trading','sources','models','jobs','settings']) assert.ok(destinations.has(page), page);
  assert.equal(helpArticles[0].id, 'quickstart');
  for (const article of helpArticles) {
    assert.ok(article.sections.length && article.title && article.summary);
    for (const id of article.related) assert.ok(helpArticles.some(row => row.id === id), id);
    for (const section of article.sections) {
      for (const id of section.demoIds || []) assert.ok(helpDemos.some(row => row.id === id), id);
    }
  }
  for (const demo of helpDemos) {
    assert.ok(demo.note && demo.filename && demo.content);
    assert.ok(helpArticles.some(article => article.sections.some(section => section.demoIds?.includes(demo.id))), demo.id);
  }
});

test('JSON examples are parseable and config values are strings rather than credentials', () => {
  for (const demo of helpDemos.filter(row => row.filename.endsWith('.json'))) assert.doesNotThrow(() => JSON.parse(demo.content));
  const assets = JSON.parse(helpDemos.find(row => row.id === 'assets').content);
  assert.ok(Array.isArray(assets) && assets.length <= 100);
  assert.ok(assets.every(row => Number.isInteger(row.lot) && row.lot > 0 && row.holding_quantity === 0));
  for (const id of ['akshare','parquet','model']) {
    const config = JSON.parse(helpDemos.find(row => row.id === id).content);
    assert.ok(Object.values(config).every(value => typeof value === 'string'));
    assert.ok(!('api_key' in config));
  }
});

test('search includes full text, field names, examples and multi-word case insensitive terms', () => {
  assert.equal(searchHelp('').length, helpArticles.length);
  assert.ok(searchHelp('持仓市值').some(row => row.id === 'assets'));
  assert.ok(searchHelp('available_at').some(row => row.id === 'features'));
  assert.ok(searchHelp('MODEL_KEY_PRIMARY').some(row => row.id === 'models'));
  assert.ok(searchHelp('offline 缓存').some(row => row.id === 'sources'));
  assert.equal(searchHelp('xyznomatchinghelpcontent12345').length, 0);
});

test('template parameter examples and field coverage match the backend catalogue', () => {
  const catalogue = readFileSync(new URL('../../backend/app/strategy_templates.py', import.meta.url), 'utf8');
  const mappings = [
    ['multi-horizon-momentum', 'momentum-params'],
    ['volatility-target-trend', 'ema-params'],
    ['adaptive-kama', 'kama-params'],
    ['risk-filtered-breakout', 'breakout-params'],
    ['regime-adaptive-ensemble', 'regime-params'],
  ];
  const reference = helpArticles.find(row => row.id === 'template-params');
  const text = JSON.stringify(reference);
  for (const [templateId, demoId] of mappings) {
    const match = catalogue.match(new RegExp(`'id': '${templateId}'[\\s\\S]*?'params': (\\{[^}]+\\})`));
    assert.ok(match, templateId);
    const expected = Object.fromEntries([...match[1].matchAll(/'([^']+)':\s*([\d.]+)/g)].map(row => [row[1], Number(row[2])]));
    const params = JSON.parse(helpDemos.find(row => row.id === demoId).content);
    assert.deepEqual(params, expected, templateId);
    assert.ok(text.includes(templateId));
    for (const key of Object.keys(expected)) {
      assert.ok(reference.sections.some(section => section.fields?.some(([label]) => label.includes(`${key} =`))), key);
      assert.ok(searchHelp(key).some(row => row.id === 'template-params'), key);
    }
  }
});

test('custom parameters are paired with code and linked from the strategy documentation', () => {
  const code = helpDemos.find(row => row.id === 'custom-params-strategy').content;
  const params = JSON.parse(helpDemos.find(row => row.id === 'custom-params').content);
  const keys = new Set([...code.matchAll(/params\["([^"]+)"\]/g)].map(row => row[1]));
  assert.deepEqual([...keys].sort(), Object.keys(params).sort());
  assert.equal(params.min_gap, 0.01);
  for (const id of ['strategy-params', 'template-params', 'indicators']) {
    assert.ok(helpArticles.find(row => row.id === 'strategies').related.includes(id));
    assert.ok(helpManualMarkdown().includes(helpArticles.find(row => row.id === id).title));
  }
  assert.ok(searchHelp('min_gap').some(row => row.id === 'strategy-params'));
  assert.ok(searchHelp('vol_floor').some(row => row.id === 'template-params'));
  assert.ok(searchHelp('Wilder').some(row => row.id === 'indicators'));
});

test('strategy code rules are searchable, linked and included in the exported manual', () => {
  const article = helpArticles.find(row => row.id === 'strategy-code');
  assert.ok(article);
  const text = JSON.stringify(article);
  for (const term of ['docstring', '类型注解', '12000', '600', '2000', 'changed_lines', 'available_at', '4个空格']) {
    assert.ok(text.includes(term), term);
  }
  assert.ok(searchHelp('策略代码规范').some(row => row.id === article.id));
  assert.ok(helpArticles.find(row => row.id === 'strategies').related.includes(article.id));
  assert.ok(helpManualMarkdown().includes(`## ${article.title}`));
});

test('Python demos have standalone comments and the editor uses the documented default', () => {
  for (const demo of helpDemos.filter(row => row.filename.endsWith('.py'))) {
    const comments = demo.content.split('\n').filter(line => line.trim().startsWith('#'));
    assert.ok(comments.length >= 3, demo.id);
    assert.ok(comments.every(line => /^(?: {4})+# \S/.test(line)), demo.id);
    assert.ok(demo.content.split('\n').every(line => line.length <= 120), demo.id);
    assert.ok(demo.content.endsWith('\n') && !demo.content.endsWith('\n\n'), demo.id);
  }
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const match = app.match(/strategy\?\.code\|\|'((?:\\.|[^'\\])*)'/);
  assert.ok(match);
  const code = runInNewContext(`'${match[1]}'`);
  assert.equal(code, helpDemos.find(row => row.id === 'strategy').content);
});
