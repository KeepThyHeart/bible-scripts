/**
 * Tests for scripts/lib/module-identity.js — identity helpers and the
 * name -> module_uuid reconversion mapping (task 0035 requirement 5).
 * Run: node scripts/lib/module-identity.test.js
 */

'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const identity = require('./module-identity');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ok   ${name}`);
  } catch (err) {
    failed++;
    console.log(`  FAIL ${name}`);
    console.log(`       ${err.stack || err.message}`);
  }
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'module-identity-test-'));
process.on('exit', () => fs.rmSync(TMP, { recursive: true, force: true }));

test('FORMAT_VERSION is 0.2', () => {
  assert.strictEqual(identity.FORMAT_VERSION, '0.2');
});

test('READABLE_FORMAT_VERSIONS includes both 0.1 and 0.2', () => {
  assert.deepStrictEqual([...identity.READABLE_FORMAT_VERSIONS].sort(), ['0.1', '0.2']);
});

test('deterministicUuid is stable for the same key', () => {
  const a = identity.deterministicUuid('bible:kjv:en');
  const b = identity.deterministicUuid('bible:kjv:en');
  assert.strictEqual(a, b);
});

test('deterministicUuid differs for different keys', () => {
  const a = identity.deterministicUuid('bible:kjv:en');
  const b = identity.deterministicUuid('bible:drc:en');
  assert.notStrictEqual(a, b);
});

test('deterministicUuid produces a version-8 (custom, hash-based) RFC 9562 UUID', () => {
  const uuid = identity.deterministicUuid('commentary:scofield:en');
  assert.match(uuid, /^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

// ── loadUuidMap / saveUuidMap / resolveModuleUuid / recordModuleUuid ───────

test('loadUuidMap returns {} for a missing file', () => {
  const map = identity.loadUuidMap(path.join(TMP, 'does-not-exist.json'));
  assert.deepStrictEqual(map, {});
});

test('saveUuidMap then loadUuidMap round-trips, sorted by key', () => {
  const file = path.join(TMP, 'map1.json');
  identity.saveUuidMap({ zebra: 'uuid-z', apple: 'uuid-a' }, file);
  const raw = fs.readFileSync(file, 'utf8');
  // Sorted: 'apple' appears before 'zebra' in the file text.
  assert.ok(raw.indexOf('"apple"') < raw.indexOf('"zebra"'));
  const map = identity.loadUuidMap(file);
  assert.deepStrictEqual(map, { apple: 'uuid-a', zebra: 'uuid-z' });
});

test('loadUuidMap rejects a non-object JSON file', () => {
  const file = path.join(TMP, 'bad.json');
  fs.writeFileSync(file, '[1,2,3]');
  assert.throws(() => identity.loadUuidMap(file), /flat JSON object/);
});

test('resolveModuleUuid mints a fresh uuid when the map has none, without mutating the map', () => {
  const map = {};
  const { uuid, isNew } = identity.resolveModuleUuid(map, 'commentary_scofield', 'commentary:scofield:en');
  assert.strictEqual(isNew, true);
  assert.strictEqual(uuid, identity.deterministicUuid('commentary:scofield:en'));
  assert.deepStrictEqual(map, {}); // resolving is not recording
});

test('resolveModuleUuid reuses a recorded uuid — the reconversion contract', () => {
  const existingUuid = '11111111-1111-8111-8111-111111111111';
  const map = { commentary_scofield: existingUuid };
  const { uuid, isNew } = identity.resolveModuleUuid(map, 'commentary_scofield', 'commentary:scofield:en');
  assert.strictEqual(isNew, false);
  // Reused, NOT the freshly-minted deterministic one — this is the whole point:
  // a reconversion keeps the uuid users' highlights/notes/positions are keyed to.
  assert.strictEqual(uuid, existingUuid);
  assert.notStrictEqual(uuid, identity.deterministicUuid('commentary:scofield:en'));
});

test('recordModuleUuid + a full mint -> record -> reuse cycle round-trips through disk', () => {
  const file = path.join(TMP, 'cycle.json');

  // First conversion: no entry yet, mints and records.
  let map = identity.loadUuidMap(file);
  const first = identity.resolveModuleUuid(map, 'bible_kjv', 'bible:kjv:en');
  assert.strictEqual(first.isNew, true);
  identity.recordModuleUuid(map, 'bible_kjv', first.uuid);
  identity.saveUuidMap(map, file);

  // Reconversion: a fresh process reloads the map and must reuse the uuid.
  map = identity.loadUuidMap(file);
  const second = identity.resolveModuleUuid(map, 'bible_kjv', 'bible:kjv:en');
  assert.strictEqual(second.isNew, false);
  assert.strictEqual(second.uuid, first.uuid);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
