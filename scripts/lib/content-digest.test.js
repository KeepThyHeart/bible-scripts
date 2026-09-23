/**
 * Tests for scripts/lib/content-digest.js — the canonical content_sha256
 * (design §2.7). Fixtures are small sqlite databases built in a temp dir, the
 * same convention scripts/query/db-cli.test.js established.
 *
 * Run: node scripts/lib/content-digest.test.js
 */

'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const sqlite3 = require('sqlite3');
const zlib = require('zlib');
const { computeContentSha256 } = require('./content-digest');

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

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'content-digest-test-'));
process.on('exit', () => fs.rmSync(TMP, { recursive: true, force: true }));

function openDb(file) {
  return new sqlite3.Database(file);
}

function run(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.run(sql, params, function (err) { if (err) reject(err); else resolve(this); });
  });
}

function close(db) {
  return new Promise(resolve => db.close(() => resolve()));
}

async function buildCommentaryDb(file, { compression = 'none', dict = null } = {}) {
  const db = openDb(file);
  await run(db, 'CREATE TABLE commentary_entry (entry_id INTEGER PRIMARY KEY, content TEXT)');
  await run(db, 'CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, compression TEXT)');
  await run(db, 'CREATE TABLE compression_dictionary (codec TEXT PRIMARY KEY, dict_blob BLOB)');
  await run(db, 'INSERT INTO module_info (info_id, compression) VALUES (1, ?)', [compression]);
  if (dict) await run(db, 'INSERT INTO compression_dictionary (codec, dict_blob) VALUES (?, ?)', [compression, dict]);

  const rows = [
    'In the beginning God created the heaven and the earth.',
    null,
    'For God so loved the world, that he gave his only begotten Son.',
  ];
  for (let i = 0; i < rows.length; i++) {
    const text = rows[i];
    if (text === null) {
      await run(db, 'INSERT INTO commentary_entry (entry_id, content) VALUES (?, NULL)', [i + 1]);
      continue;
    }
    let value = text;
    if (compression === 'deflate') {
      const opts = dict ? { dictionary: dict } : {};
      const frame = zlib.deflateRawSync(Buffer.from(text, 'utf8'), opts);
      if (frame.length + 16 < Buffer.byteLength(text, 'utf8')) value = frame;
    }
    await run(db, 'INSERT INTO commentary_entry (entry_id, content) VALUES (?, ?)', [i + 1, value]);
  }
  await close(db);
}

async function main() {
  await test('digest is a 64-char lowercase hex string', async () => {
    const file = path.join(TMP, 'a.db');
    await buildCommentaryDb(file);
    const db = openDb(file);
    const digest = await computeContentSha256(db, 'commentary', { compression: 'none', dictionary: null });
    await close(db);
    assert.match(digest, /^[0-9a-f]{64}$/);
  });

  await test('digest is deterministic (same input -> same output)', async () => {
    const file = path.join(TMP, 'b.db');
    await buildCommentaryDb(file);
    const db = openDb(file);
    const d1 = await computeContentSha256(db, 'commentary', { compression: 'none', dictionary: null });
    const d2 = await computeContentSha256(db, 'commentary', { compression: 'none', dictionary: null });
    await close(db);
    assert.strictEqual(d1, d2);
  });

  await test('digest changes when content changes', async () => {
    const file1 = path.join(TMP, 'c1.db');
    const file2 = path.join(TMP, 'c2.db');
    await buildCommentaryDb(file1);
    const db1 = openDb(file1);
    await run(db1, "UPDATE commentary_entry SET content = 'changed' WHERE entry_id = 1");
    const changed = await computeContentSha256(db1, 'commentary', { compression: 'none', dictionary: null });
    await close(db1);

    await buildCommentaryDb(file2);
    const db2 = openDb(file2);
    const original = await computeContentSha256(db2, 'commentary', { compression: 'none', dictionary: null });
    await close(db2);

    assert.notStrictEqual(changed, original);
  });

  await test('digest is codec-invariant: none vs deflate (no dict, explicit options) match', async () => {
    // A conforming module never has compression != 'none' without a
    // compression_dictionary row (design §2.8: validator error) — this test
    // exercises the codec-invariance of the decode path itself by passing
    // compression/dictionary explicitly, bypassing the DB-driven dictionary
    // lookup that (correctly) enforces that invariant for a real file.
    const fileNone = path.join(TMP, 'd-none.db');
    const fileDeflate = path.join(TMP, 'd-deflate.db');
    await buildCommentaryDb(fileNone, { compression: 'none' });
    await buildCommentaryDb(fileDeflate, { compression: 'deflate' });

    const dbNone = openDb(fileNone);
    const digestNone = await computeContentSha256(dbNone, 'commentary', { compression: 'none', dictionary: null });
    await close(dbNone);

    const dbDeflate = openDb(fileDeflate);
    const digestDeflate = await computeContentSha256(dbDeflate, 'commentary', { compression: 'deflate', dictionary: Buffer.alloc(0) });
    await close(dbDeflate);

    assert.strictEqual(digestNone, digestDeflate);
  });

  await test('computeContentSha256 throws on a non-conforming file (compressed with no dictionary row)', async () => {
    // Confirms the DB-driven path (the one every real caller uses) refuses
    // to silently under-verify a file that violates §2.8's invariant, rather
    // than guessing at "no dictionary" and hashing wrongly.
    const file = path.join(TMP, 'h.db');
    await buildCommentaryDb(file, { compression: 'deflate' }); // no compression_dictionary row
    const db = openDb(file);
    await assert.rejects(() => computeContentSha256(db, 'commentary'), /compression_dictionary has no row/);
    await close(db);
  });

  await test('digest is codec-invariant: none vs deflate (with dictionary) match', async () => {
    const dict = Buffer.from('In the beginning God created the heaven and the earth For God so loved the world');
    const fileNone = path.join(TMP, 'e-none.db');
    const fileDeflate = path.join(TMP, 'e-deflate.db');
    await buildCommentaryDb(fileNone, { compression: 'none' });
    await buildCommentaryDb(fileDeflate, { compression: 'deflate', dict });

    const dbNone = openDb(fileNone);
    const digestNone = await computeContentSha256(dbNone, 'commentary');
    await close(dbNone);

    const dbDeflate = openDb(fileDeflate);
    const digestDeflate = await computeContentSha256(dbDeflate, 'commentary');
    await close(dbDeflate);

    assert.strictEqual(digestNone, digestDeflate);
  });

  await test('NULL cells are distinguishable from empty-string cells', async () => {
    const fileNull = path.join(TMP, 'f-null.db');
    const fileEmpty = path.join(TMP, 'f-empty.db');
    const db1 = openDb(fileNull);
    await run(db1, 'CREATE TABLE commentary_entry (entry_id INTEGER PRIMARY KEY, content TEXT)');
    await run(db1, 'CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, compression TEXT)');
    await run(db1, "INSERT INTO module_info VALUES (1, 'none')");
    await run(db1, 'INSERT INTO commentary_entry (entry_id, content) VALUES (1, NULL)');
    const digestNull = await computeContentSha256(db1, 'commentary');
    await close(db1);

    const db2 = openDb(fileEmpty);
    await run(db2, 'CREATE TABLE commentary_entry (entry_id INTEGER PRIMARY KEY, content TEXT)');
    await run(db2, 'CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, compression TEXT)');
    await run(db2, "INSERT INTO module_info VALUES (1, 'none')");
    await run(db2, "INSERT INTO commentary_entry (entry_id, content) VALUES (1, '')");
    const digestEmpty = await computeContentSha256(db2, 'commentary');
    await close(db2);

    assert.notStrictEqual(digestNull, digestEmpty);
  });

  await test('rejects an out-of-scope module type', async () => {
    const file = path.join(TMP, 'g.db');
    await buildCommentaryDb(file);
    const db = openDb(file);
    await assert.rejects(() => computeContentSha256(db, 'topical_index'));
    await close(db);
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
}

main();
