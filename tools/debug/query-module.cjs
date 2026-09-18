#!/usr/bin/env node
/**
 * Debug utility: Query module info and stats from a Bible module database
 *
 * Usage:
 *   node tools/debug/query-module.cjs [module]
 *   node tools/debug/query-module.cjs kjv     # KJV module stats
 *   node tools/debug/query-module.cjs asv     # ASV module stats
 */

const path = require('path');
const sqlite3 = require('sqlite3').verbose();
const paths = require('../../scripts/lib/paths');

const modName = (process.argv[2] || 'kjv').toLowerCase();
const dbPath = path.join(paths.MODULES_DIR, `bible_${modName}.db`);

const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
  if (err) { console.error('Error opening DB:', err.message); process.exit(1); }
});

// Helper to run queries sequentially
function query(sql, params = []) {
  return new Promise((resolve, reject) => {
    db.all(sql, params, (err, rows) => err ? reject(err) : resolve(rows));
  });
}
function queryOne(sql, params = []) {
  return new Promise((resolve, reject) => {
    db.get(sql, params, (err, row) => err ? reject(err) : resolve(row));
  });
}

async function main() {
  console.log(`\n=== Module: ${modName.toUpperCase()} ===\n`);

  // Module info
  const info = await queryOne('SELECT * FROM module_info LIMIT 1');
  if (info) {
    console.log('--- Module Info ---');
    console.log('Abbreviation:', info.abbreviation);
    console.log('Full Name:', info.full_name);
    console.log('Language:', info.language_code);
    console.log('Content version:', info.content_version);
    console.log('Format:', `${info.format} ${info.format_version}`);
    console.log('Original Language:', info.is_original_language ? 'Yes' : 'No');
  }

  // Verse stats
  const verseCount = await queryOne('SELECT COUNT(*) as cnt FROM bible_verse');
  console.log('\n--- Verse Stats ---');
  console.log('Total verses:', verseCount.cnt);

  const fmtCount = await queryOne('SELECT COUNT(*) as cnt FROM bible_verse WHERE formatting IS NOT NULL');
  console.log('Verses with formatting:', fmtCount.cnt);

  // Check for words of Christ
  const wocSample = await query(`
    SELECT verse_id, formatting FROM bible_verse
    WHERE formatting LIKE '%words_of_christ%'
    LIMIT 5
  `);
  console.log('Verses with words_of_christ spans:', wocSample.length > 0 ? `found (showing ${wocSample.length})` : 'NONE');
  wocSample.forEach(r => {
    const bk = Math.floor(r.verse_id / 1000000);
    const ch = Math.floor((r.verse_id % 1000000) / 1000);
    const v = r.verse_id % 1000;
    console.log(`  Book ${bk} ${ch}:${v} - ${r.formatting.substring(0, 120)}`);
  });

  // Interlinear stats
  try {
    const ilCount = await queryOne('SELECT COUNT(*) as cnt FROM interlinear_word');
    console.log('\n--- Interlinear Stats ---');
    console.log('Total interlinear words:', ilCount.cnt);

    const withOrig = await queryOne("SELECT COUNT(*) as cnt FROM interlinear_word WHERE original_word IS NOT NULL AND original_word != ''");
    console.log('Words with original text:', withOrig.cnt);

    const withoutOrig = await queryOne("SELECT COUNT(*) as cnt FROM interlinear_word WHERE original_word IS NULL OR original_word = ''");
    console.log('Words WITHOUT original text:', withoutOrig.cnt);

    // Sample English-only originals
    const engOrig = await query(`
      SELECT verse_id, strongs_number, original_word, gloss
      FROM interlinear_word
      WHERE original_word GLOB '[A-Za-z]*'
        AND original_word NOT GLOB '*[^A-Za-z ]*'
        AND length(original_word) > 1
      LIMIT 10
    `);
    console.log('Words with English-only original_word:', engOrig.length > 0 ? `found (${engOrig.length} samples)` : 'none');
    engOrig.forEach(w => {
      console.log(`  ${w.verse_id} ${w.strongs_number}: original="${w.original_word}" gloss="${w.gloss}"`);
    });
  } catch {
    console.log('\n(No interlinear_word table)');
  }

  // Features
  try {
    const features = await query('SELECT * FROM module_feature');
    if (features.length > 0) {
      console.log('\n--- Module Features ---');
      features.forEach(f => console.log(`  ${f.feature_name}: ${f.is_enabled ? 'enabled' : 'disabled'}`));
    }
  } catch { /* table may not exist */ }

  db.close();
}

main().catch(err => { console.error('Error:', err.message); db.close(); process.exit(1); });
