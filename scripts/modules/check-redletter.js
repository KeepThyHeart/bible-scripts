#!/usr/bin/env node
/**
 * Check red-letter text data in Bible database
 */

const sqlite3 = require('sqlite3').verbose();
const path = require('path');
const { MODULES_DIR } = require('../lib/paths');

// Path to Bible database
const dbPath = path.join(MODULES_DIR, 'bible_kjv.db');

console.log('Opening database:', dbPath);

const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
  if (err) {
    console.error('Error opening database:', err.message);
    process.exit(1);
  }

  // First list all tables
  console.log('\n=== Database Tables ===');
  db.all("SELECT name FROM sqlite_master WHERE type='table'", [], (err, tables) => {
    if (err) {
      console.error('Error:', err.message);
      db.close();
      process.exit(1);
    }
    console.log('Tables:', tables.map(t => t.name).join(', '));

  // First check table schema
  console.log('\n=== Bible Verse Table Schema ===');
  db.all("PRAGMA table_info(bible_verse)", [], (err, columns) => {
    if (err) {
      console.error('Error:', err.message);
      db.close();
      process.exit(1);
    }
    console.log('Columns:', columns.map(c => c.name).join(', '));

  // Check John 3:16 (verse_id = 43003016)
  console.log('\n=== Checking John 3:16 ===');
  db.get('SELECT * FROM bible_verse WHERE verse_id = ?', [43003016], (err, verse) => {
    if (err) {
      console.error('Error:', err.message);
      db.close();
      process.exit(1);
    }

    if (verse) {
      console.log('\nVerse data:', JSON.stringify(verse, null, 2));
    } else {
      console.log('Verse not found');
    }

    // Check for any verses with words_of_christ in formatting
    console.log('\n\n=== Checking for verses with red-letter text ===');
    db.get("SELECT COUNT(*) as count FROM bible_verse WHERE formatting LIKE '%words_of_christ%'", [], (err, row) => {
      if (err) {
        console.error('Error:', err.message);
        db.close();
        process.exit(1);
      }

      console.log(`Found ${row.count} verses with red-letter text`);

      if (row.count > 0) {
        console.log('\nSample red-letter verses:');
        db.all("SELECT verse_id, text, formatting FROM bible_verse WHERE formatting LIKE '%words_of_christ%' LIMIT 3", [], (err, samples) => {
          if (err) {
            console.error('Error:', err.message);
          } else {
            samples.forEach(v => {
              console.log(`\nVerse ${v.verse_id}:`);
              console.log(v.text.substring(0, 100) + '...');
              console.log('Formatting:', v.formatting.substring(0, 200));
            });
          }

          db.close();
          console.log('\n✓ Done');
        });
      } else {
        db.close();
        console.log('\n✓ Done');
      }
    });
  });
  });
  });
});
