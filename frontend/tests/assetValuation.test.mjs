import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/assetValuation.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const context = {exports: {}};
runInNewContext(compiled, context);
const {holdingValue, sortAssetsByValue} = context.exports;
const asset = (id, quantity, currency = 'USD') => ({id, symbol: id, market: 'US', currency, holding_quantity: quantity});

test('value, not share count, determines descending order without mutating state', () => {
  const assets = [asset('cheap', 1000), asset('expensive', 10)];
  const quotes = [{id:'cheap',price:1}, {id:'expensive',price:500}];
  assert.equal(sortAssetsByValue(assets, quotes).map(row => row.id).join(','), 'expensive,cheap');
  assert.equal(assets[0].id, 'cheap');
});

test('unknown quotes sort last, zero holdings remain zero, and edits recalculate values', () => {
  assert.equal(holdingValue(asset('a', 0)), 0);
  for (const price of [NaN, Infinity, -1, 0]) assert.equal(holdingValue(asset('a', 2), {price}), undefined);
  assert.equal(holdingValue(asset('a', 20), {price:50}), 1000);
  const rows = [asset('missing',99999),asset('zero',0),asset('known',2)];
  assert.equal(sortAssetsByValue(rows,[{id:'known',price:50}]).map(row=>row.id).join(','),'known,zero,missing');
});

test('currencies remain separate, ties are deterministic', () => {
  const rows = [asset('B',10),asset('A',10),asset('C',1,'CNY')];
  const quotes = rows.map(row=>({id:row.id,price:10}));
  assert.equal(sortAssetsByValue(rows,quotes).map(row=>row.id).join(','),'C,A,B');
});
