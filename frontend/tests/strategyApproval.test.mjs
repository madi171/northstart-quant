import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(name) {
  const source = readFileSync(new URL('../src/' + name, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX}}).outputText;
  const context = {exports: {}, require};
  runInNewContext(compiled, context);
  return context.exports;
}
const {createStateRefresher} = load('stateRefresh.ts');
const {StrategyApprovalButton} = load('StrategyApprovalButton.tsx');
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

test('ordinary refresh callers await the same in-flight state request', async () => {
  const request = deferred();
  let calls = 0;
  const states = [];
  const refresh = createStateRefresher(() => {calls++; return request.promise;}, state => states.push(state));
  const first = refresh();
  let finished = false;
  const second = refresh().then(() => {finished = true;});
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(finished, false);
  request.resolve('candidate');
  await Promise.all([first, second]);
  assert.deepEqual(states, ['candidate']);
});

test('post-mutation refresh waits for old request then reads a fresh state before unlocking', async () => {
  const old = deferred(), latest = deferred();
  let calls = 0, finished = false;
  const states = [];
  const refresh = createStateRefresher(() => (++calls === 1 ? old.promise : latest.promise), state => states.push(state));
  const pushed = refresh();
  const mutation = refresh(true).then(() => {finished = true;});
  old.resolve('candidate');
  await pushed;
  assert.equal(calls, 2);
  assert.equal(finished, false);
  latest.resolve('approved');
  await mutation;
  assert.deepEqual(states, ['candidate', 'approved']);
});

test('failed overlapping state reads do not prevent a new post-mutation read or retry', async () => {
  const old = deferred();
  let calls = 0;
  const refresh = createStateRefresher(() => ++calls === 1 ? old.promise : Promise.resolve('approved'), () => {});
  const failed = assert.rejects(refresh(), /offline/);
  const mutation = refresh(true);
  old.reject(new Error('offline'));
  await Promise.all([failed, mutation]);
  await refresh();
  assert.equal(calls, 3);
});

test('approve and revoke render disabled animated progress even when status already changes', () => {
  for (const action of ['approve', 'revoke']) {
    const button = StrategyApprovalButton({strategyId:'s1', status:action === 'approve' ? 'approved' : 'candidate', busy:false, allowRevoke:true, pending:{id:'s1', action}, onChange:() => {}});
    assert.equal(button.props.disabled, true);
    assert.equal(button.props['aria-busy'], true);
    const html = renderToStaticMarkup(button);
    assert.match(html, /is-spinning/);
    assert.ok(html.includes(action === 'approve' ? '批准中…' : '撤销中…'));
  }
});

test('other entries lock during mutation and current server status controls the idle button', () => {
  const base = {strategyId:'s1', busy:false, pending:null, onChange:() => {}};
  assert.equal(StrategyApprovalButton({...base, status:'candidate'}).props.disabled, false);
  assert.equal(StrategyApprovalButton({...base, status:'approved'}).props.disabled, true);
  assert.equal(StrategyApprovalButton({...base, status:'approved', allowRevoke:true}).props.disabled, false);
  assert.equal(StrategyApprovalButton({...base, status:undefined}).props.disabled, true);
  assert.equal(StrategyApprovalButton({...base, status:'candidate', pending:{id:'other', action:'approve'}}).props.disabled, true);
});
