import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/researchManifest.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const context = {exports: {}};
runInNewContext(compiled, context);
const {researchManifest} = context.exports;

function job() {
  return {status:'succeeded', payload:{spec:{kind:'optimization', model_ids:['paid-model'], fee_bps:5},
    strategy:{id:'original', code:'original', params:{weight:.1}}},
    result:{snapshot:'snapshots/frozen.parquet', data_hash:'market-hash', source:{name:'frozen'},
      feature_snapshot:{hash:'features-hash', records:[{name:'macro.rate', value:.1}]},
      optimization:{candidate_id:'selected', selected:{code:'selected-code', params:{weight:.2}}, style:{rebalance_every:5}}}};
}

test('replay exports the selected strategy with frozen data, features and execution policy', () => {
  const original = job();
  const before = JSON.stringify(original);
  const manifest = researchManifest(original);
  assert.equal(manifest.spec.kind, 'backtest');
  assert.equal(manifest.spec.model_ids.length, 0);
  assert.equal(manifest.spec.fee_bps, 5);
  assert.equal(manifest.strategy.id, 'selected');
  assert.equal(manifest.strategy.code, 'selected-code');
  assert.equal(manifest.feature_snapshot.hash, 'features-hash');
  assert.equal(manifest.frozen_data.hash, 'market-hash');
  assert.equal(manifest.rebalance_every, 5);
  assert.equal(JSON.stringify(original), before);
});

test('unfinished or incomplete research cannot be exported as a final replay', () => {
  for (const status of ['running', 'cancelled', 'failed']) assert.equal(researchManifest({...job(), status}), null);
  const incomplete = job();
  delete incomplete.result.optimization.selected;
  assert.equal(researchManifest(incomplete), null);
});

test('a retained baseline exports its original identifier and parameters', () => {
  const baseline = job();
  delete baseline.result.optimization.candidate_id;
  baseline.result.optimization.selected = {code:'original', params:{weight:.1}};
  assert.equal(researchManifest(baseline).strategy.id, 'original');
  assert.equal(researchManifest(baseline).strategy.params.weight, .1);
});
