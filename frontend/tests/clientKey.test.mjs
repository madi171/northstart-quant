import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/clientKey.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
function generator(crypto) {
  const context = {exports: {}, crypto};
  runInNewContext(compiled, context);
  return context.exports.createClientKey;
}

test('generates client keys without the secure-context randomUUID API', () => {
  const create = generator({getRandomValues: webcrypto.getRandomValues.bind(webcrypto)});
  const keys = Array.from({length: 2048}, () => create());
  assert.equal(new Set(keys).size, keys.length);
  for (const key of keys) assert.match(key, uuidPattern);
});

test('sets UUID version and variant without changing the other random bytes', () => {
  const create = generator({getRandomValues: bytes => {
    for (let index = 0; index < bytes.length; index++) bytes[index] = index;
    return bytes;
  }});
  assert.equal(create(), '00010203-0405-4607-8809-0a0b0c0d0e0f');
});

test('uses fresh cryptographic randomness on every call', () => {
  let calls = 0;
  const create = generator({
    randomUUID: () => {throw new Error('secure-context API must not be required');},
    getRandomValues: bytes => {calls++; return webcrypto.getRandomValues(bytes);},
  });
  const first = create();
  const second = create();
  assert.notEqual(first, second);
  assert.equal(calls, 2);
  assert.match(first, uuidPattern);
  assert.match(second, uuidPattern);
});
