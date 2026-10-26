import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const storage = () => {
  const values = new Map();
  return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
};
const localStorage = storage(), sessionStorage = storage();
const dispatched = [];
const context = {exports: {}, localStorage, sessionStorage,
  window: {dispatchEvent: event => dispatched.push(event.type)},
  Event: class {constructor(type) {this.type = type;}},
  fetch: async () => ({status: 401, ok: false, json: async () => ({detail: '登录已过期'})})};
runInNewContext(compiled, context);
const {token, saveToken, clearToken, api} = context.exports;

test('remember login across browser sessions and migrate existing tab session', () => {
  sessionStorage.setItem('quant-token', 'old-session');
  assert.equal(token(), 'old-session');
  assert.equal(localStorage.getItem('quant-token'), 'old-session');
  assert.equal(sessionStorage.getItem('quant-token'), null);
  saveToken('long-session');
  assert.equal(token(), 'long-session');
  clearToken();
  assert.equal(token(), '');
});

test('expired server session clears persistent token and notifies the page', async () => {
  saveToken('expired');
  await assert.rejects(() => api('/state'), /登录已过期/);
  assert.equal(localStorage.getItem('quant-token'), null);
  assert.deepEqual(dispatched, ['quant-logout']);
});
