/**
 * Tests for scripts/lib/schema.js — schema loading and its CLI bridge.
 *
 * The CLI mode (`node scripts/lib/schema.js <Name.sql>`) is what the C++
 * converters shell out to (schema_bridge.h's loadRepoSchema(), task 0035 /
 * design §6.1) instead of hand-copying DDL. These tests build a tiny fixture
 * "Bible repo" schema tree — this repo has no access to the real one — and
 * exercise both the library function and the CLI subprocess against it.
 *
 * Run: node scripts/lib/schema.test.js
 */

'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

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

const SCHEMA_JS = path.join(__dirname, 'schema.js');

// ── Fixture "Bible repo" ────────────────────────────────────────────────────

const FIXTURE_REPO = fs.mkdtempSync(path.join(os.tmpdir(), 'schema-test-repo-'));
const SCHEMAS_DIR = path.join(FIXTURE_REPO, 'packages', 'core', 'sql', 'schemas');
fs.mkdirSync(path.join(SCHEMAS_DIR, 'initial'), { recursive: true });
fs.mkdirSync(path.join(SCHEMAS_DIR, 'shared'), { recursive: true });

fs.writeFileSync(path.join(SCHEMAS_DIR, 'shared', 'module_info.sql'), [
  'PRAGMA foreign_keys = ON;',
  'CREATE TABLE module_info (',
  '  info_id INTEGER PRIMARY KEY CHECK (info_id = 1),',
  "  format_version TEXT NOT NULL DEFAULT '0.2',",
  "  compression TEXT NOT NULL DEFAULT 'none'",
  ');',
].join('\n'));

fs.writeFileSync(path.join(SCHEMAS_DIR, 'shared', 'verse_link.sql'),
  'CREATE TABLE verse_link (link_id INTEGER PRIMARY KEY AUTOINCREMENT);');

fs.writeFileSync(path.join(SCHEMAS_DIR, 'initial', 'Fixture.sql'), [
  '-- @include ../shared/module_info.sql',
  '-- @include ../shared/verse_link.sql',
  'CREATE TABLE fixture_content (id INTEGER PRIMARY KEY, text TEXT NOT NULL);',
].join('\n'));

fs.writeFileSync(path.join(SCHEMAS_DIR, 'initial', 'Circular.sql'), '-- @include ../initial/Circular.sql\n');

process.on('exit', () => fs.rmSync(FIXTURE_REPO, { recursive: true, force: true }));

// ── Library function ────────────────────────────────────────────────────────

test('loadSchema expands @include and strips PRAGMA lines', () => {
  const env = { ...process.env, BIBLE_REPO: FIXTURE_REPO };
  delete require.cache[require.resolve('./schema')];
  delete require.cache[require.resolve('./paths')];
  const savedRepo = process.env.BIBLE_REPO;
  process.env.BIBLE_REPO = FIXTURE_REPO;
  try {
    const { loadSchema } = require('./schema');
    const ddl = loadSchema('Fixture.sql');
    assert.ok(ddl.includes('CREATE TABLE module_info'));
    assert.ok(ddl.includes('CREATE TABLE verse_link'));
    assert.ok(ddl.includes('CREATE TABLE fixture_content'));
    assert.ok(!ddl.includes('PRAGMA'), 'PRAGMA lines must be dropped (module files are immutable artifacts)');
  } finally {
    if (savedRepo === undefined) delete process.env.BIBLE_REPO; else process.env.BIBLE_REPO = savedRepo;
    delete require.cache[require.resolve('./schema')];
    delete require.cache[require.resolve('./paths')];
    void env;
  }
});

test('loadSchema throws a clear error for a missing schema file', () => {
  const savedRepo = process.env.BIBLE_REPO;
  process.env.BIBLE_REPO = FIXTURE_REPO;
  try {
    delete require.cache[require.resolve('./schema')];
    delete require.cache[require.resolve('./paths')];
    const { loadSchema } = require('./schema');
    assert.throws(() => loadSchema('NoSuchType.sql'), /Schema file not found/);
  } finally {
    if (savedRepo === undefined) delete process.env.BIBLE_REPO; else process.env.BIBLE_REPO = savedRepo;
    delete require.cache[require.resolve('./schema')];
    delete require.cache[require.resolve('./paths')];
  }
});

test('loadSchema detects a circular @include', () => {
  const savedRepo = process.env.BIBLE_REPO;
  process.env.BIBLE_REPO = FIXTURE_REPO;
  try {
    delete require.cache[require.resolve('./schema')];
    delete require.cache[require.resolve('./paths')];
    const { loadSchema } = require('./schema');
    assert.throws(() => loadSchema('Circular.sql'), /Circular @include/);
  } finally {
    if (savedRepo === undefined) delete process.env.BIBLE_REPO; else process.env.BIBLE_REPO = savedRepo;
    delete require.cache[require.resolve('./schema')];
    delete require.cache[require.resolve('./paths')];
  }
});

// ── CLI bridge (what the C++ converters actually shell out to) ────────────

test('CLI mode prints the expanded schema to stdout', () => {
  const out = execFileSync(process.execPath, [SCHEMA_JS, 'Fixture.sql'], {
    encoding: 'utf8',
    env: { ...process.env, BIBLE_REPO: FIXTURE_REPO },
  });
  assert.ok(out.includes('CREATE TABLE module_info'));
  assert.ok(out.includes('CREATE TABLE fixture_content'));
});

test('CLI mode exits non-zero with no filename argument', () => {
  assert.throws(() => execFileSync(process.execPath, [SCHEMA_JS], {
    encoding: 'utf8',
    env: { ...process.env, BIBLE_REPO: FIXTURE_REPO },
  }));
});

test('CLI mode exits non-zero and reports the error for a missing schema file', () => {
  try {
    execFileSync(process.execPath, [SCHEMA_JS, 'NoSuchType.sql'], {
      encoding: 'utf8',
      env: { ...process.env, BIBLE_REPO: FIXTURE_REPO },
    });
    assert.fail('expected a non-zero exit');
  } catch (err) {
    assert.ok(err.stderr.includes('Schema file not found'));
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
