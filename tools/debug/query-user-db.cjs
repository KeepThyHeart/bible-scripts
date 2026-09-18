#!/usr/bin/env node
/**
 * Debug utility: Query user database for notes, highlights, sessions, etc.
 *
 * Usage:
 *   node tools/debug/query-user-db.cjs                  # Show stats
 *   node tools/debug/query-user-db.cjs notes [verse_id]  # Show notes (optionally for verse)
 *   node tools/debug/query-user-db.cjs highlights [verse_id]  # Show highlights
 *   node tools/debug/query-user-db.cjs sessions          # Show sessions
 *   node tools/debug/query-user-db.cjs tables             # List all tables
 */

const path = require('path');
const fs = require('fs');
const sqlite3 = require('sqlite3').verbose();
const paths = require('../../scripts/lib/paths');

const command = (process.argv[2] || 'stats').toLowerCase();
const arg = process.argv[3];

// Check multiple possible locations for user DB
const possiblePaths = [
  path.join(paths.USERS_DIR, 'user_default.db'),
  path.join(paths.DATA_DIR, 'user_default.db'),
  path.join(process.env.APPDATA || '', 'bible-desktop-app', 'data', 'users', 'user_default.db'),
];

const dbPath = possiblePaths.find(p => fs.existsSync(p));
if (!dbPath) {
  console.log('User database not found. Checked:');
  possiblePaths.forEach(p => console.log('  ' + p));
  process.exit(1);
}

const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
  if (err) { console.error('Error opening DB:', err.message); process.exit(1); }
});

function query(sql, params = []) {
  return new Promise((resolve, reject) => {
    db.all(sql, params, (err, rows) => err ? reject(err) : resolve(rows));
  });
}

async function main() {
  console.log(`\n=== User Database: ${dbPath} ===\n`);

  if (command === 'tables') {
    const tables = await query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name");
    console.log('Tables:');
    for (const t of tables) {
      try {
        const rows = await query(`SELECT COUNT(*) as cnt FROM "${t.name}"`);
        console.log(`  ${t.name}: ${rows[0].cnt} rows`);
      } catch { console.log(`  ${t.name}: (error)`); }
    }
  }
  else if (command === 'notes') {
    let notes;
    if (arg) {
      notes = await query(`
        SELECT un.* FROM user_note un
        JOIN content_verse_link cvl ON cvl.content_id = un.note_id
        WHERE cvl.content_type = 'note'
        AND cvl.verse_id_start <= ? AND (cvl.verse_id_end IS NULL OR cvl.verse_id_end >= ?)
        ORDER BY un.modified_date DESC LIMIT 20
      `, [parseInt(arg), parseInt(arg)]);
      console.log(`Notes for verse ${arg}:`);
    } else {
      notes = await query('SELECT * FROM user_note ORDER BY modified_date DESC LIMIT 20');
      console.log('Recent notes:');
    }
    notes.forEach(n => {
      console.log(`  [${n.note_id}] ${n.title || '(untitled)'} (${n.note_type}) - ${n.modified_date}`);
      if (n.content) console.log(`    ${n.content.substring(0, 100)}${n.content.length > 100 ? '...' : ''}`);
    });
    if (notes.length === 0) console.log('  (none)');
  }
  else if (command === 'highlights') {
    let highlights;
    if (arg) {
      highlights = await query('SELECT * FROM user_text_markup WHERE start_verse_id <= ? AND end_verse_id >= ? LIMIT 20', [parseInt(arg), parseInt(arg)]);
      console.log(`Highlights for verse ${arg}:`);
    } else {
      highlights = await query('SELECT * FROM user_text_markup ORDER BY created_date DESC LIMIT 20');
      console.log('Recent highlights:');
    }
    highlights.forEach(h => {
      console.log(`  [${h.markup_id}] verse ${h.start_verse_id}-${h.end_verse_id} words ${h.start_word_index}-${h.end_word_index} color=${h.color}`);
    });
    if (highlights.length === 0) console.log('  (none)');
  }
  else if (command === 'sessions') {
    const sessions = await query('SELECT session_id, session_name, is_active, last_accessed, created_date FROM study_session ORDER BY last_accessed DESC LIMIT 10');
    console.log('Sessions:');
    sessions.forEach(s => {
      console.log(`  [${s.session_id}] ${s.session_name} ${s.is_active ? '(active)' : ''} - last: ${s.last_accessed}`);
    });
    if (sessions.length === 0) console.log('  (none)');
  }
  else {
    // Stats
    const tables = await query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name");
    console.log('Table row counts:');
    for (const t of tables) {
      try {
        const rows = await query(`SELECT COUNT(*) as cnt FROM "${t.name}"`);
        console.log(`  ${t.name}: ${rows[0].cnt}`);
      } catch { console.log(`  ${t.name}: (error)`); }
    }
  }

  db.close();
}

main().catch(err => { console.error('Error:', err.message); db.close(); process.exit(1); });
