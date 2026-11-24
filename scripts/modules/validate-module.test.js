/**
 * Tests for scripts/modules/validate-module.js's module format v0.2 rules
 * (task 0035 requirement 7 / design §2.8): no fts5 table, the 3-index
 * verse_link contract, decode-aware content scanning, dictionary-iff-
 * compressed, and the content_sha256 recompute check.
 *
 * Fixtures are small sqlite databases built in a temp dir, the shape the
 * Bible repo's schemas define — the same convention scripts/query/db-cli.test.js
 * established. This exercises the JS validator only; it needs no SWORD
 * module and no Bible repo checkout.
 *
 * Run: node scripts/modules/validate-module.test.js
 */

'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const sqlite3 = require('sqlite3');
const zlib = require('zlib');
const { validateModule } = require('./validate-module');
const { computeContentSha256 } = require('../lib/content-digest');

let passed = 0;
let failed = 0;

async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  ok   ${name}`);
  } catch (err) {
    failed++;
    console.log(`  FAIL ${name}`);
    console.log(`       ${err.stack || err.message}`);
  }
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'validate-module-test-'));
process.on('exit', () => fs.rmSync(TMP, { recursive: true, force: true }));

/**
 * Build (or extend) a fixture database from a list of statement strings, each
 * of which may itself contain several `;`-separated SQL statements — hence
 * `db.exec()`, not `db.run()` (which only executes the first statement of a
 * multi-statement string and silently drops the rest).
 */
async function buildDb(name, statements) {
  const file = path.join(TMP, name);
  const db = new sqlite3.Database(file);
  await new Promise((resolve, reject) => {
    db.serialize(() => {
      for (const sql of statements) {
        db.exec(sql, err => { if (err) reject(new Error(`${name}: ${err.message}\n${sql}`)); });
      }
      db.run('PRAGMA user_version = 1', err => (err ? reject(err) : resolve()));
    });
  });
  await new Promise(resolve => db.close(() => resolve()));
  return file;
}

const VERSE_LINK_V1 = `CREATE TABLE verse_link (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL, source_id INTEGER NOT NULL,
  verse_id_start INTEGER NOT NULL, verse_id_end INTEGER NOT NULL,
  link_type TEXT NOT NULL DEFAULT 'reference', sort_order INTEGER NOT NULL DEFAULT 0,
  context TEXT, metadata TEXT);
  CREATE INDEX idx_verse_link_source ON verse_link(source_type, source_id, sort_order);
  CREATE INDEX idx_verse_link_start ON verse_link(verse_id_start);
  CREATE INDEX idx_verse_link_range ON verse_link(verse_id_start, verse_id_end);
  CREATE INDEX idx_verse_link_covering ON verse_link(verse_id_end, verse_id_start)`;

const VERSE_LINK_V2 = `CREATE TABLE verse_link (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL, source_id INTEGER NOT NULL,
  verse_id_start INTEGER NOT NULL, verse_id_end INTEGER NOT NULL,
  link_type TEXT NOT NULL DEFAULT 'reference', sort_order INTEGER NOT NULL DEFAULT 0,
  context TEXT, metadata TEXT);
  CREATE INDEX idx_verse_link_source ON verse_link(source_type, source_id, sort_order);
  CREATE INDEX idx_verse_link_range ON verse_link(verse_id_start, verse_id_end);
  CREATE INDEX idx_verse_link_covering ON verse_link(verse_id_end, verse_id_start)`;

const SCHEMA_VERSION = `CREATE TABLE schema_version (version_id INTEGER PRIMARY KEY AUTOINCREMENT,
  version_number TEXT NOT NULL, applied_date TEXT, notes TEXT, metadata TEXT);
  INSERT INTO schema_version (version_number, notes) VALUES ('0.2', 'test fixture')`;

function moduleInfoSql(overrides = {}) {
  const info = Object.assign({
    module_uuid: '11111111-1111-8111-8111-111111111111',
    module_type: 'commentary',
    abbreviation: 'TEST',
    full_name: 'Test Commentary',
    format: 'commentary-module',
    format_version: '0.2',
    content_version: '1.0',
    content_sha256: null,
    language_code: 'en',
    versification: 'kjv-english',
    license_spdx: 'PD',
    source_url: 'https://example.org',
    compression: 'none',
  }, overrides);

  const cols = Object.keys(info);
  const vals = cols.map(c => {
    const v = info[c];
    if (v === null) return 'NULL';
    if (typeof v === 'number') return String(v);
    return `'${String(v).replace(/'/g, "''")}'`;
  });
  return `CREATE TABLE module_info (
      info_id INTEGER PRIMARY KEY CHECK (info_id = 1), module_uuid TEXT NOT NULL,
      module_type TEXT NOT NULL, abbreviation TEXT NOT NULL, full_name TEXT NOT NULL,
      format TEXT NOT NULL, format_version TEXT NOT NULL, content_version TEXT,
      content_sha256 TEXT, language_code TEXT NOT NULL DEFAULT 'en', versification TEXT NOT NULL,
      license_spdx TEXT, source_url TEXT, compression TEXT NOT NULL DEFAULT 'none');
    INSERT INTO module_info (info_id, ${cols.join(', ')}) VALUES (1, ${vals.join(', ')})`;
}

const COMMENTARY_ENTRY = `CREATE TABLE commentary_entry (
  entry_id INTEGER PRIMARY KEY AUTOINCREMENT, verse_id_start INTEGER, verse_id_end INTEGER,
  entry_level TEXT NOT NULL, content TEXT NOT NULL, word_count INTEGER, metadata TEXT)`;

const COMPRESSION_DICTIONARY = `CREATE TABLE compression_dictionary (
  codec TEXT PRIMARY KEY, dict_id INTEGER NOT NULL, dict_blob BLOB NOT NULL)`;

async function digestFor(dbPath, moduleType, opts) {
  const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READWRITE);
  const digest = await computeContentSha256(db, moduleType, opts);
  await new Promise(resolve => db.close(() => resolve()));
  return digest;
}

async function main() {
  // ── no-fts5-table rule (v0.2) ─────────────────────────────────────────

  await test('v0.2 module with no FTS table passes the fts5 check', async () => {
    const file = await buildDb('v2-no-fts.db', [
      moduleInfoSql(), COMMENTARY_ENTRY,
      "INSERT INTO commentary_entry (verse_id_start, verse_id_end, entry_level, content) VALUES (1001001, 1001001, 'verse', 'A commentary entry with enough text to pass the length check.')",
      VERSE_LINK_V2, SCHEMA_VERSION,
    ]);
    const digest = await digestFor(file, 'commentary', { compression: 'none', dictionary: null });
    await buildDb('v2-no-fts.db', [`UPDATE module_info SET content_sha256 = '${digest}'`]);
    const result = await validateModule(file);
    assert.ok(!result.errors.some(e => e.code === 'fts5_table_present'), JSON.stringify(result.errors));
  });

  await test('v0.2 module WITH an FTS table fails fts5_table_present', async () => {
    const file = await buildDb('v2-with-fts.db', [
      moduleInfoSql(), COMMENTARY_ENTRY,
      "INSERT INTO commentary_entry (verse_id_start, verse_id_end, entry_level, content) VALUES (1001001, 1001001, 'verse', 'A commentary entry with enough text to pass the length check.')",
      `CREATE VIRTUAL TABLE commentary_entry_fts USING fts5(entry_id UNINDEXED, content, content='commentary_entry', content_rowid='entry_id')`,
      VERSE_LINK_V2, SCHEMA_VERSION,
    ]);
    const result = await validateModule(file);
    assert.ok(result.errors.some(e => e.code === 'fts5_table_present'), JSON.stringify(result.errors));
  });

  // ── verse_link index contract, version-gated ──────────────────────────

  await test('v0.2 module needs only 3 verse_link indexes (no idx_verse_link_start)', async () => {
    const file = await buildDb('v2-verselink.db', [
      moduleInfoSql(), COMMENTARY_ENTRY, VERSE_LINK_V2, SCHEMA_VERSION,
    ]);
    const result = await validateModule(file);
    assert.ok(!result.errors.some(e => e.code === 'missing_verse_link_index'), JSON.stringify(result.errors));
  });

  await test('a v0.1 module still requires all 4 verse_link indexes', async () => {
    const file = await buildDb('v1-verselink.db', [
      moduleInfoSql({ format_version: '0.1' }), COMMENTARY_ENTRY, VERSE_LINK_V2 /* only 3 indexes */, SCHEMA_VERSION,
    ]);
    const result = await validateModule(file);
    assert.ok(result.errors.some(e => e.code === 'missing_verse_link_index'), JSON.stringify(result.errors));
  });

  // ── dictionary iff compressed ──────────────────────────────────────────

  await test('v0.2, compression=none, no dictionary rows: passes', async () => {
    const file = await buildDb('v2-none-ok.db', [
      moduleInfoSql(), COMMENTARY_ENTRY,
      "INSERT INTO commentary_entry (verse_id_start, verse_id_end, entry_level, content) VALUES (1001001, 1001001, 'verse', 'Enough text to pass the length check here.')",
      VERSE_LINK_V2, SCHEMA_VERSION,
    ]);
    const digest = await digestFor(file, 'commentary', { compression: 'none', dictionary: null });
    await buildDb('v2-none-ok.db', [`UPDATE module_info SET content_sha256 = '${digest}'`]);
    const result = await validateModule(file);
    assert.ok(!result.errors.some(e => e.code === 'missing_compression_dictionary'), JSON.stringify(result.errors));
    assert.ok(!result.errors.some(e => e.code === 'unexpected_compression_dictionary'), JSON.stringify(result.errors));
  });

  await test('v0.2, compression=deflate with no dictionary row: missing_compression_dictionary', async () => {
    const file = await buildDb('v2-deflate-nodict.db', [
      moduleInfoSql({ compression: 'deflate' }), COMMENTARY_ENTRY, COMPRESSION_DICTIONARY,
      "INSERT INTO commentary_entry (verse_id_start, verse_id_end, entry_level, content) VALUES (1001001, 1001001, 'verse', 'Enough text to pass the length check here.')",
      VERSE_LINK_V2, SCHEMA_VERSION,
    ]);
    const result = await validateModule(file);
    assert.ok(result.errors.some(e => e.code === 'missing_compression_dictionary'), JSON.stringify(result.errors));
  });

  await test('v0.2, compression=none but a dictionary row exists anyway: unexpected_compression_dictionary', async () => {
    const dict = Buffer.from('some dictionary bytes');
    const file = await buildDb('v2-none-extradict.db', [
      moduleInfoSql({ compression: 'none' }), COMMENTARY_ENTRY, COMPRESSION_DICTIONARY,
      VERSE_LINK_V2, SCHEMA_VERSION,
    ]);
    const db = new sqlite3.Database(file);
    await new Promise((resolve, reject) => db.run(
      'INSERT INTO compression_dictionary (codec, dict_id, dict_blob) VALUES (?, ?, ?)',
      ['none', 1, dict], err => (err ? reject(err) : resolve())
    ));
    await new Promise(resolve => db.close(() => resolve()));
    const result = await validateModule(file);
    assert.ok(result.errors.some(e => e.code === 'unexpected_compression_dictionary'), JSON.stringify(result.errors));
  });

  // ── content_sha256 recompute ───────────────────────────────────────────

  await test('correct content_sha256 passes', async () => {
    const file = await buildDb('digest-ok.db', [
      moduleInfoSql(), COMMENTARY_ENTRY,
      "INSERT INTO commentary_entry (verse_id_start, verse_id_end, entry_level, content) VALUES (1001001, 1001001, 'verse', 'The correct content for this digest test.')",
      VERSE_LINK_V2, SCHEMA_VERSION,
    ]);
    const digest = await digestFor(file, 'commentary', { compression: 'none', dictionary: null });
    await buildDb('digest-ok.db', [`UPDATE module_info SET content_sha256 = '${digest}'`]);
    const result = await validateModule(file);
    assert.ok(!result.errors.some(e => e.code === 'content_sha256_mismatch'), JSON.stringify(result.errors));
  });

  await test('wrong content_sha256 fails content_sha256_mismatch', async () => {
    const file = await buildDb('digest-bad.db', [
      moduleInfoSql({ content_sha256: '0'.repeat(64) }), COMMENTARY_ENTRY,
      "INSERT INTO commentary_entry (verse_id_start, verse_id_end, entry_level, content) VALUES (1001001, 1001001, 'verse', 'This content does not match the stored digest.')",
      VERSE_LINK_V2, SCHEMA_VERSION,
    ]);
    const result = await validateModule(file);
    assert.ok(result.errors.some(e => e.code === 'content_sha256_mismatch'), JSON.stringify(result.errors));
  });

  // ── decode-aware content scan ──────────────────────────────────────────

  await test('decode-aware scan: compressed content passes when it decodes to healthy text', async () => {
    const longText = 'This commentary entry has plenty of real prose in it. '.repeat(30);
    const frame = zlib.deflateRawSync(Buffer.from(longText, 'utf8'));
    assert.ok(frame.length + 16 < Buffer.byteLength(longText, 'utf8'), 'test precondition: frame must actually shrink');

    const file = path.join(TMP, 'decode-aware-ok.db');
    const db = new sqlite3.Database(file);
    // exec() for (possibly multi-statement) plain SQL; run() only where a
    // parameter needs binding (a BLOB literal can't be spelled in SQL text).
    const exec = (sql) => new Promise((resolve, reject) => db.exec(sql, err => (err ? reject(err) : resolve())));
    const run = (sql, params = []) => new Promise((resolve, reject) => db.run(sql, params, err => (err ? reject(err) : resolve())));
    await exec(moduleInfoSql({ compression: 'deflate' }));
    await exec(COMMENTARY_ENTRY);
    await exec(COMPRESSION_DICTIONARY);
    await run('INSERT INTO compression_dictionary (codec, dict_id, dict_blob) VALUES (?, 0, ?)', ['deflate', Buffer.alloc(0)]);
    await run(
      "INSERT INTO commentary_entry (verse_id_start, verse_id_end, entry_level, content) VALUES (1001001, 1001001, 'verse', ?)",
      [frame]
    );
    await exec(VERSE_LINK_V2);
    await exec(SCHEMA_VERSION);
    await new Promise(resolve => db.close(() => resolve()));

    const digest = await digestFor(file, 'commentary', { compression: 'deflate', dictionary: Buffer.alloc(0) });
    await buildDb('decode-aware-ok.db', [`UPDATE module_info SET content_sha256 = '${digest}'`]);

    const result = await validateModule(file);
    assert.ok(!result.errors.some(e => e.code === 'content_corrupt'), JSON.stringify(result.errors));
    assert.ok(!result.errors.some(e => e.code === 'content_sha256_mismatch'), JSON.stringify(result.errors));
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
}

main();
