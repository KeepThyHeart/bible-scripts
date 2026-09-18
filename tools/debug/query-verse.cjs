#!/usr/bin/env node
/**
 * Debug utility: Query verse data from a Bible module database
 *
 * Usage:
 *   node tools/debug/query-verse.cjs <verse_id> [module]
 *   node tools/debug/query-verse.cjs 43003016          # John 3:16 in KJV (default)
 *   node tools/debug/query-verse.cjs 43003001 kjv      # John 3:1 in KJV
 *   node tools/debug/query-verse.cjs 1001001 asv       # Genesis 1:1 in ASV
 *
 * Verse ID format: (book * 1000000) + (chapter * 1000) + verse
 * Examples: Genesis 1:1 = 1001001, John 3:16 = 43003016, Rev 22:21 = 66022021
 */

const path = require('path');
const sqlite3 = require('sqlite3').verbose();
const paths = require('../../scripts/lib/paths');

const verseId = parseInt(process.argv[2]);
const modName = (process.argv[3] || 'kjv').toLowerCase();

if (!verseId || isNaN(verseId)) {
  console.log('Usage: node tools/debug/query-verse.cjs <verse_id> [module]');
  console.log('Example: node tools/debug/query-verse.cjs 43003016 kjv');
  process.exit(1);
}

const dbPath = path.join(paths.MODULES_DIR, `bible_${modName}.db`);

const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
  if (err) { console.error('Error opening DB:', err.message); process.exit(1); }
});

const book = Math.floor(verseId / 1000000);
const remainder = verseId % 1000000;
const chapter = Math.floor(remainder / 1000);
const verse = remainder % 1000;

console.log(`\n=== ${modName.toUpperCase()} - Book ${book}, Chapter ${chapter}, Verse ${verse} (ID: ${verseId}) ===\n`);

db.get('SELECT * FROM bible_verse WHERE verse_id = ?', [verseId], (err, row) => {
  if (err) { console.error('Error:', err.message); db.close(); return; }
  if (!row) { console.log('Verse not found.'); db.close(); return; }

  console.log('--- Verse Text ---');
  console.log('text:', (row.text || '').substring(0, 300) + ((row.text || '').length > 300 ? '...' : ''));
  console.log('word_count:', row.word_count);

  console.log('\n--- Formatting ---');
  if (row.formatting) {
    try {
      const fmt = JSON.parse(row.formatting);
      console.log(JSON.stringify(fmt, null, 2));
    } catch { console.log('Raw:', row.formatting); }
  } else {
    console.log('(none)');
  }

  db.all('SELECT * FROM interlinear_word WHERE verse_id = ? ORDER BY word_position_start', [verseId], (err2, words) => {
    if (err2) { console.log('\n(No interlinear table or error)'); db.close(); return; }

    console.log(`\n--- Interlinear Words (${words.length}) ---`);
    if (words.length > 0) {
      words.forEach((w, i) => {
        console.log(`  [${i + 1}] ${w.strongs_number || '?'}: original="${w.original_word || ''}" gloss="${w.gloss || ''}" lemma="${w.lemma || ''}"`);
      });
    } else {
      console.log('(none)');
    }
    db.close();
  });
});
