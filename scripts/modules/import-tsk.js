#!/usr/bin/env node

/**
 * Import TSK: commentary_tsk.db → xref_tsk.db
 *
 * Parses the TSK commentary HTML entries (phrase-grouped cross-references
 * stored as HTML with <a> tags) and converts them into the cross-reference
 * database schema (cross_reference_group + verse_link; see the Bible repo's
 * packages/core/sql/schemas/initial/CrossReference.sql).
 *
 * Uses the async `sqlite3` package (NOT better-sqlite3) so it runs
 * with system Node.js without needing Electron-compatible native modules.
 *
 * Usage:
 *   node scripts/modules/import-tsk.js [--force]
 *   node scripts/modules/import-tsk.js --input=<commentary_tsk.db> --output=<xref_tsk.db> [--force]
 */

const path = require('path');
const fs = require('fs');
const sqlite3 = require('sqlite3').verbose();
const identity = require('../lib/module-identity');
const { MODULES_DIR } = require('../lib/paths');
const { loadSchema } = require('../lib/schema');

// ============================================================================
// CLI arguments
// ============================================================================

const FORCE = process.argv.includes('--force');

// --input names the source commentary module. (Not --source=, which means
// something else in convert-topical-index.js: there it selects nave|torrey.)
const inputArg = process.argv.find(a => a.startsWith('--input='));
const SOURCE_DB_PATH = inputArg
  ? path.resolve(inputArg.split('=')[1])
  : path.join(MODULES_DIR, 'commentary_tsk.db');

// --output lets a conversion be written somewhere other than the modules
// directory, which is what makes this script testable without touching shipped
// data. Matches the same flag on convert-topical-index.js.
const outputArg = process.argv.find(a => a.startsWith('--output='));
const TARGET_DB_PATH = outputArg
  ? path.resolve(outputArg.split('=')[1])
  : path.join(MODULES_DIR, 'xref_tsk.db');

// ============================================================================
// SQLite async helpers (copied from convert-topical-index.js)
// ============================================================================

function dbRun(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.run(sql, params, function (err) {
      if (err) reject(err);
      else resolve({ changes: this.changes, lastID: this.lastID });
    });
  });
}

function dbGet(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.get(sql, params, (err, row) => {
      if (err) reject(err);
      else resolve(row);
    });
  });
}

function dbAll(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.all(sql, params, (err, rows) => {
      if (err) reject(err);
      else resolve(rows || []);
    });
  });
}

function openDb(dbPath, mode = sqlite3.OPEN_READWRITE) {
  return new Promise((resolve, reject) => {
    const db = new sqlite3.Database(dbPath, mode, (err) => {
      if (err) reject(err);
      else resolve(db);
    });
  });
}

function openDbCreate(dbPath) {
  return new Promise((resolve, reject) => {
    const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READWRITE | sqlite3.OPEN_CREATE, (err) => {
      if (err) reject(err);
      else resolve(db);
    });
  });
}

function closeDb(db) {
  return new Promise((resolve, reject) => {
    db.close((err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

function dbExec(db, sql) {
  return new Promise((resolve, reject) => {
    db.exec(sql, (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

// ============================================================================
// Book abbreviation map (SWORD-style abbreviations → book number 1-66)
// ============================================================================

const BOOK_ABBREV_MAP = {
  // Genesis
  'genesis': 1, 'gen': 1, 'ge': 1,
  // Exodus
  'exodus': 2, 'exod': 2, 'exo': 2, 'ex': 2,
  // Leviticus
  'leviticus': 3, 'lev': 3, 'le': 3,
  // Numbers
  'numbers': 4, 'num': 4, 'nu': 4,
  // Deuteronomy
  'deuteronomy': 5, 'deut': 5, 'deu': 5, 'de': 5, 'dt': 5,
  // Joshua
  'joshua': 6, 'josh': 6, 'jos': 6,
  // Judges
  'judges': 7, 'judg': 7, 'jdg': 7, 'jud': 7,
  // Ruth
  'ruth': 8, 'ru': 8, 'rut': 8,
  // 1 Samuel
  '1samuel': 9, '1sam': 9, '1sa': 9, 'isamuel': 9, 'isam': 9,
  // 2 Samuel
  '2samuel': 10, '2sam': 10, '2sa': 10, 'iisamuel': 10, 'iisam': 10,
  // 1 Kings
  '1kings': 11, '1king': 11, '1ki': 11, '1kin': 11, 'ikings': 11,
  // 2 Kings
  '2kings': 12, '2king': 12, '2ki': 12, '2kin': 12, 'iikings': 12,
  // 1 Chronicles
  '1chronicles': 13, '1chron': 13, '1chr': 13, '1ch': 13, 'ichronicles': 13,
  // 2 Chronicles
  '2chronicles': 14, '2chron': 14, '2chr': 14, '2ch': 14, 'iichronicles': 14,
  // Ezra
  'ezra': 15, 'ezr': 15,
  // Nehemiah
  'nehemiah': 16, 'neh': 16, 'ne': 16,
  // Esther
  'esther': 17, 'est': 17, 'es': 17, 'esth': 17,
  // Job
  'job': 18,
  // Psalms
  'psalms': 19, 'psalm': 19, 'psa': 19, 'ps': 19,
  // Proverbs
  'proverbs': 20, 'prov': 20, 'pro': 20, 'pr': 20,
  // Ecclesiastes
  'ecclesiastes': 21, 'eccles': 21, 'eccl': 21, 'ecc': 21, 'ec': 21,
  // Song of Solomon
  'songofsolomon': 22, 'song': 22, 'so': 22, 'sos': 22, 'canticles': 22, 'cant': 22, 'ca': 22,
  // Isaiah — 'isa' handled specially in resolveBookNumber
  'isaiah': 23, 'iss': 23,
  // Jeremiah
  'jeremiah': 24, 'jer': 24, 'je': 24,
  // Lamentations
  'lamentations': 25, 'lam': 25, 'la': 25,
  // Ezekiel
  'ezekiel': 26, 'ezek': 26, 'eze': 26,
  // Daniel
  'daniel': 27, 'dan': 27, 'da': 27,
  // Hosea
  'hosea': 28, 'hos': 28, 'ho': 28,
  // Joel
  'joel': 29, 'joe': 29,
  // Amos
  'amos': 30, 'am': 30,
  // Obadiah
  'obadiah': 31, 'obad': 31, 'ob': 31,
  // Jonah
  'jonah': 32, 'jon': 32,
  // Micah
  'micah': 33, 'mic': 33,
  // Nahum
  'nahum': 34, 'nah': 34, 'na': 34,
  // Habakkuk
  'habakkuk': 35, 'hab': 35,
  // Zephaniah
  'zephaniah': 36, 'zeph': 36, 'zep': 36,
  // Haggai
  'haggai': 37, 'hag': 37,
  // Zechariah
  'zechariah': 38, 'zech': 38, 'zec': 38,
  // Malachi
  'malachi': 39, 'mal': 39,
  // Matthew
  'matthew': 40, 'matt': 40, 'mat': 40, 'mt': 40,
  // Mark
  'mark': 41, 'mr': 41, 'mk': 41, 'mar': 41,
  // Luke
  'luke': 42, 'lu': 42, 'lk': 42, 'luk': 42,
  // John (gospel)
  'john': 43, 'joh': 43, 'jn': 43,
  // Acts
  'acts': 44, 'act': 44, 'ac': 44,
  // Romans
  'romans': 45, 'rom': 45, 'ro': 45,
  // 1 Corinthians
  '1corinthians': 46, '1cor': 46, '1co': 46, 'icorinthians': 46,
  // 2 Corinthians
  '2corinthians': 47, '2cor': 47, '2co': 47, 'iicorinthians': 47,
  // Galatians
  'galatians': 48, 'gal': 48, 'ga': 48,
  // Ephesians
  'ephesians': 49, 'eph': 49,
  // Philippians
  'philippians': 50, 'php': 50, 'phil': 50, 'phili': 50,
  // Colossians
  'colossians': 51, 'col': 51,
  // 1 Thessalonians
  '1thessalonians': 52, '1thess': 52, '1thes': 52, '1th': 52, 'ithessalonians': 52,
  // 2 Thessalonians
  '2thessalonians': 53, '2thess': 53, '2thes': 53, '2th': 53, 'iithessalonians': 53,
  // 1 Timothy
  '1timothy': 54, '1tim': 54, '1ti': 54, 'itimothy': 54,
  // 2 Timothy
  '2timothy': 55, '2tim': 55, '2ti': 55, 'iitimothy': 55,
  // Titus
  'titus': 56, 'tit': 56,
  // Philemon
  'philemon': 57, 'phm': 57, 'phile': 57, 'philem': 57,
  // Hebrews
  'hebrews': 58, 'heb': 58,
  // James
  'james': 59, 'jas': 59, 'jam': 59,
  // 1 Peter
  '1peter': 60, '1pet': 60, '1pe': 60, 'ipeter': 60,
  // 2 Peter
  '2peter': 61, '2pet': 61, '2pe': 61, 'iipeter': 61,
  // 1 John
  '1john': 62, '1joh': 62, '1jn': 62, '1jo': 62, 'ijohn': 62,
  // 2 John
  '2john': 63, '2joh': 63, '2jn': 63, '2jo': 63, 'iijohn': 63,
  // 3 John
  '3john': 64, '3joh': 64, '3jn': 64, '3jo': 64, 'iiijohn': 64,
  // Jude
  'jude': 65,
  // Revelation
  'revelation': 66, 'rev': 66, 're': 66,
};

// ============================================================================
// Verse reference parser (adapted from convert-topical-index.js)
// ============================================================================

/**
 * Resolve a book name/abbreviation to a book number (1-66).
 * In TSK context, "Isa" = Isaiah (23), "Jud" = Jude (65).
 */
function resolveBookNumber(rawBook) {
  if (!rawBook) return null;
  let normalized = rawBook.replace(/\s+/g, '').toLowerCase();

  // TSK uses "Isa" for Isaiah, not 1 Samuel
  if (normalized === 'isa') return 23;

  // TSK uses "Jud" for Judges (book 7), and "Jude" for Jude (book 65)
  // The BOOK_ABBREV_MAP already maps 'jud' → 7 correctly.

  const num = BOOK_ABBREV_MAP[normalized];
  return num || null;
}

// Single-chapter books (references may omit chapter number)
const SINGLE_CHAPTER_BOOKS = new Set([31, 57, 63, 64, 65]); // Obadiah, Philemon, 2 John, 3 John, Jude

/**
 * Try to extract a book name/abbreviation from the start of a string.
 * Returns { bookNum, endIndex } or null.
 */
function tryExtractBook(str) {
  // Match patterns like "Ge ", "1Ch ", "1 Ch ", "Ex ", etc.
  const match = str.match(/^(\d?\s*[A-Za-z]+)\s+(?=\d)/);
  if (match) {
    const bookNum = resolveBookNumber(match[1]);
    if (bookNum) {
      return { bookNum, endIndex: match[0].length - (match[0].length - match[0].trimEnd().length) };
    }
  }

  // Book name followed by end of string (book-only reference)
  const match2 = str.match(/^(\d?\s*[A-Za-z]+)\s*$/);
  if (match2) {
    const bookNum = resolveBookNumber(match2[1]);
    if (bookNum) {
      return { bookNum, endIndex: match2[0].length };
    }
  }

  return null;
}

/**
 * Parse "chapter:verse" patterns from a string for a given book.
 * Returns array of { targetVerseId, targetVerseEndId } objects (ranges preserved).
 */
function parseChapterVerseRanges(bookNum, str) {
  const results = [];
  // First alternative catches chapter-spanning ranges ("14:1-21:45"). Without
  // it the scanner reads "14:1-21" as a verse range and then treats the
  // orphaned ":45" as a chapter-only reference, inventing Joshua 45:1.
  const refPattern = /(\d+):(\d+)\s*-\s*(\d+):(\d+)|(\d+)(?::(\d+(?:[,-]\d+)*))?/g;
  let match;
  let currentChapter = null;

  while ((match = refPattern.exec(str)) !== null) {
    if (match[1] !== undefined) {
      const startChapter = parseInt(match[1], 10);
      const startVerse = parseInt(match[2], 10);
      const endChapter = parseInt(match[3], 10);
      const endVerse = parseInt(match[4], 10);
      results.push({
        targetVerseId: (bookNum * 1000000) + (startChapter * 1000) + startVerse,
        targetVerseEndId: (bookNum * 1000000) + (endChapter * 1000) + endVerse
      });
      currentChapter = endChapter;
      continue;
    }

    const num1 = parseInt(match[5], 10);
    const versePart = match[6];

    if (versePart) {
      currentChapter = num1;
      parseVerseListRanges(bookNum, currentChapter, versePart, results);
    } else {
      if (currentChapter !== null) {
        const beforeMatch = str.substring(0, match.index);
        if (beforeMatch.match(/,\s*$/)) {
          // Verse continuation within same chapter
          const vid = (bookNum * 1000000) + (currentChapter * 1000) + num1;
          results.push({ targetVerseId: vid, targetVerseEndId: null });
          continue;
        }
      }

      if (SINGLE_CHAPTER_BOOKS.has(bookNum)) {
        // Single-chapter book — number is a verse
        const vid = (bookNum * 1000000) + (1 * 1000) + num1;
        results.push({ targetVerseId: vid, targetVerseEndId: null });
        currentChapter = 1;
      } else {
        // Chapter-only reference — store as chapter:1
        currentChapter = num1;
        const vid = (bookNum * 1000000) + (num1 * 1000) + 1;
        results.push({ targetVerseId: vid, targetVerseEndId: null });
      }
    }
  }

  return results;
}

/**
 * Parse a verse list like "1-15", "2,3", "16-20", "1-15,25"
 * Returns ranges (not expanded).
 */
function parseVerseListRanges(bookNum, chapter, verseStr, results) {
  const parts = verseStr.split(',');
  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) continue;

    const rangeParts = trimmed.split('-');
    if (rangeParts.length === 2) {
      const start = parseInt(rangeParts[0], 10);
      const end = parseInt(rangeParts[1], 10);
      if (!isNaN(start) && !isNaN(end) && end >= start) {
        const vidStart = (bookNum * 1000000) + (chapter * 1000) + start;
        const vidEnd = (bookNum * 1000000) + (chapter * 1000) + end;
        results.push({
          targetVerseId: vidStart,
          targetVerseEndId: start === end ? null : vidEnd
        });
      }
    } else {
      const v = parseInt(trimmed, 10);
      if (!isNaN(v)) {
        const vid = (bookNum * 1000000) + (chapter * 1000) + v;
        results.push({ targetVerseId: vid, targetVerseEndId: null });
      }
    }
  }
}

/**
 * Parse a TSK reference string with same-book fallback.
 *
 * When a semicolon-separated group has no book abbreviation, it falls back to
 * the source verse's book (not the last-seen book from a previous group).
 *
 * Examples with source = Romans 8:28 (book 45, chapter 8):
 *   "35-39; 5:3,4; Ge 50:20" →
 *     Ro 8:35-39, Ro 5:3, Ro 5:4, Ge 50:20
 *
 *   "15; Mt 9:13; 1Ti 1:15,16" with source John 3:16 →
 *     Jn 3:15, Mt 9:13, 1Ti 1:15, 1Ti 1:16
 */
function parseTskReferences(refString, sourceBookNum, sourceChapter) {
  const results = [];
  if (!refString || !refString.trim()) return results;

  const groups = refString.split(';');
  let currentBook = null;

  for (const group of groups) {
    const trimmed = group.trim();
    if (!trimmed) continue;

    const bookMatch = tryExtractBook(trimmed);

    if (bookMatch) {
      currentBook = bookMatch.bookNum;
      const remainder = trimmed.substring(bookMatch.endIndex).trim();
      if (remainder) {
        const refs = parseChapterVerseRanges(currentBook, remainder);
        results.push(...refs);
      }
    } else {
      // No book found — continue with last explicit book if available,
      // otherwise fall back to the source verse's book.
      // e.g., "Ro 1:1; 6:22; 16:18" → all three are Romans references.
      const fallbackBook = currentBook ?? sourceBookNum;
      const fallbackChapter = currentBook ? null : sourceChapter;

      // Check if this looks like "chapter:verse" or just bare numbers
      if (trimmed.match(/^\d+:/)) {
        // Has colon — chapter:verse within the fallback book
        const refs = parseChapterVerseRanges(fallbackBook, trimmed);
        results.push(...refs);
      } else if (trimmed.match(/^\d/)) {
        if (fallbackChapter != null) {
          // Bare number(s) — verse(s) within source chapter (no prior explicit book)
          parseBareVerseRefs(fallbackBook, fallbackChapter, trimmed, results);
        } else {
          // Bare number(s) with a prior explicit book but no chapter context —
          // treat as chapter:1 (chapter-only reference)
          const refs = parseChapterVerseRanges(fallbackBook, trimmed);
          results.push(...refs);
        }
      }
    }
  }

  return results;
}

/**
 * Parse bare verse references (no book, no chapter prefix).
 * These are verses in the same book+chapter as the source verse.
 * Handles: "35-39", "2,3", "15", etc.
 */
function parseBareVerseRefs(bookNum, chapter, str, results) {
  const parts = str.split(',');
  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) continue;

    const rangeParts = trimmed.split('-');
    if (rangeParts.length === 2) {
      const start = parseInt(rangeParts[0], 10);
      const end = parseInt(rangeParts[1], 10);
      if (!isNaN(start) && !isNaN(end) && end >= start) {
        const vidStart = (bookNum * 1000000) + (chapter * 1000) + start;
        const vidEnd = (bookNum * 1000000) + (chapter * 1000) + end;
        results.push({
          targetVerseId: vidStart,
          targetVerseEndId: start === end ? null : vidEnd
        });
      }
    } else {
      const v = parseInt(trimmed, 10);
      if (!isNaN(v)) {
        const vid = (bookNum * 1000000) + (chapter * 1000) + v;
        results.push({ targetVerseId: vid, targetVerseEndId: null });
      }
    }
  }
}

// ============================================================================
// HTML parsing
// ============================================================================

/**
 * Extract the text content from an <a> tag string.
 * Input:  '<a href="...">Lu 2:14; Ro 5:8</a>'
 * Output: 'Lu 2:14; Ro 5:8'
 */
function extractAnchorText(html) {
  const match = html.match(/>([^<]*)<\/a>/);
  return match ? match[1].trim() : '';
}

/**
 * Check if a segment is an <a> tag (starts with '<a')
 */
function isAnchorTag(segment) {
  return segment.trimStart().startsWith('<a ') || segment.trimStart().startsWith('<a>');
}

/**
 * Check if an anchor's text content is just a number (chapter header link).
 */
function isChapterHeaderLink(anchorText) {
  return /^\d+$/.test(anchorText.trim());
}

/**
 * Parse the HTML content of a single TSK commentary entry into phrase groups.
 *
 * Returns: [{ phrase: string|null, refStrings: string[] }]
 * where refStrings are the raw text from <a> tags to be parsed later.
 */
function parseEntryHtml(content) {
  if (!content) return [];

  // Split on <br /> or <br/> (case-insensitive)
  const segments = content.split(/<br\s*\/?>/i);

  const groups = [];
  let currentGroup = null;
  let inChapterHeader = false;

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i].trim();
    if (!seg) continue;

    if (isAnchorTag(seg)) {
      const anchorText = extractAnchorText(seg);
      if (!anchorText) continue;

      // Check for chapter header: <a>number</a> followed by description text
      if (isChapterHeaderLink(anchorText)) {
        // This is a chapter header link — skip it and mark that we're in header mode
        inChapterHeader = true;
        continue;
      }

      inChapterHeader = false;

      if (currentGroup) {
        // Continuation refs or refs for current phrase — add to current group
        currentGroup.refStrings.push(anchorText);
      } else {
        // Refs without a preceding phrase — create a group with null phrase
        currentGroup = { phrase: null, refStrings: [anchorText] };
        groups.push(currentGroup);
      }
    } else {
      // Non-anchor text
      if (inChapterHeader) {
        // This is the description text after a chapter header link — skip
        inChapterHeader = false;
        continue;
      }

      // This is a phrase label — strip any remaining HTML tags
      const phraseText = seg.replace(/<[^>]*>/g, '').trim();
      if (!phraseText) continue;

      // Start a new group with this phrase
      currentGroup = { phrase: phraseText, refStrings: [] };
      groups.push(currentGroup);
    }
  }

  // Filter out groups with no references
  return groups.filter(g => g.refStrings.length > 0);
}

// ============================================================================
// Target database schema
// ============================================================================

/**
 * Cross-reference module schema.
 *
 * TSK ships its cross-references as `<a href="passagestudy.jsp?...">` HTML
 * buried inside `commentary_entry.content`, where nothing can query them. This
 * importer parses those anchors out; the prose stays as content in the source
 * commentary module, and the references land here as data.
 *
 * `cross_reference_group` keeps the phrase grouping (a TSK entry attaches
 * several reference lists to different phrases of the same verse). The targets
 * are rows in the shared `verse_link` table with `link_type = 'cross_reference'`.
 */
const TARGET_SCHEMA = loadSchema('CrossReference.sql');

// ============================================================================
// Main conversion logic
// ============================================================================

async function main() {
  console.log('=== TSK Cross-Reference Import ===');
  console.log(`Source: ${SOURCE_DB_PATH}`);
  console.log(`Target: ${TARGET_DB_PATH}`);

  // Check source exists
  if (!fs.existsSync(SOURCE_DB_PATH)) {
    console.error(`ERROR: Source database not found: ${SOURCE_DB_PATH}`);
    process.exit(1);
  }

  // Check if target already exists
  if (fs.existsSync(TARGET_DB_PATH)) {
    if (!FORCE) {
      console.error(`Target already exists: ${TARGET_DB_PATH}`);
      console.error('Use --force to overwrite.');
      process.exit(1);
    }
    console.log('Removing existing target (--force)...');
    fs.unlinkSync(TARGET_DB_PATH);
  }

  // Open source database
  const srcDb = await openDb(SOURCE_DB_PATH, sqlite3.OPEN_READONLY);
  console.log('Opened source database.');

  // Read all entries
  const entries = await dbAll(srcDb, 'SELECT verse_id_start, verse_id_end, content FROM commentary_entry ORDER BY verse_id_start');
  console.log(`Read ${entries.length} commentary entries.`);

  // Read source module info
  const srcModuleInfo = await dbGet(srcDb, 'SELECT * FROM module_info WHERE info_id = 1');
  await closeDb(srcDb);

  // Parse all entries
  console.log('Parsing HTML content...');
  let totalGroups = 0;
  let totalEntries = 0;
  let warningCount = 0;

  // Collected data: { verseId, verseIdEnd, groups: [{ phrase, entries: [{targetVerseId, targetVerseEndId}] }] }
  const allData = [];

  for (const entry of entries) {
    const verseId = entry.verse_id_start;
    // cross_reference_group.verse_id_end is NOT NULL; TSK source entries
    // are always single-verse, but fall back to verseId defensively.
    const verseIdEnd = entry.verse_id_end ?? verseId;
    const sourceBookNum = Math.floor(verseId / 1000000);
    const sourceChapter = Math.floor((verseId % 1000000) / 1000);

    const phraseGroups = parseEntryHtml(entry.content);

    const verseData = { verseId, verseIdEnd, groups: [] };

    for (const pg of phraseGroups) {
      const groupEntries = [];

      for (const refStr of pg.refStrings) {
        const refs = parseTskReferences(refStr, sourceBookNum, sourceChapter);
        for (const ref of refs) {
          // Basic validation: book number should be 1-66
          const targetBook = Math.floor(ref.targetVerseId / 1000000);
          if (targetBook < 1 || targetBook > 66) {
            warningCount++;
            if (warningCount <= 20) {
              console.warn(`  Warning: Invalid verse ID ${ref.targetVerseId} from ref "${refStr}" (source ${verseId})`);
            }
            continue;
          }
          groupEntries.push(ref);
        }
      }

      if (groupEntries.length > 0) {
        verseData.groups.push({ phrase: pg.phrase, entries: groupEntries });
        totalGroups++;
        totalEntries += groupEntries.length;
      }
    }

    if (verseData.groups.length > 0) {
      allData.push(verseData);
    }
  }

  console.log(`Parsed: ${totalGroups} groups, ${totalEntries} entries from ${allData.length} verses.`);
  if (warningCount > 0) {
    console.log(`Warnings: ${warningCount} invalid references skipped.`);
  }

  // Create target database
  console.log('Creating target database...');
  const targetDb = await openDbCreate(TARGET_DB_PATH);

  // Enable WAL mode and create schema
  await dbExec(targetDb, 'PRAGMA journal_mode = WAL;');
  await dbExec(targetDb, TARGET_SCHEMA);

  // Insert module info (identity + provenance)
  const abbrev = 'TSKxref';
  const languageCode = srcModuleInfo.language_code || 'en';
  const copyright = identity.stripRtfArtifacts(srcModuleInfo.copyright || '') || 'Public Domain';
  const licenseSpdx = srcModuleInfo.license_spdx || identity.licenseToSpdx(copyright) || 'PD';
  const contentVersion = srcModuleInfo.content_version || srcModuleInfo.version || '1.4';

  // Identity that survives content revisions — never fold the version in.
  const moduleUuid = identity.deterministicUuid(
    `cross_reference:${abbrev.toLowerCase()}:${languageCode.toLowerCase()}`
  );

  await dbRun(targetDb,
    `INSERT INTO module_info (
       info_id, module_uuid, module_type, format, format_version,
       abbreviation, full_name, author, year_published, copyright,
       license_spdx, license_url, source_url, description, language_code,
       versification, content_version, metadata
     ) VALUES (1, ?, 'cross_reference', 'cross-reference-module', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      moduleUuid,
      identity.FORMAT_VERSION,
      abbrev,
      srcModuleInfo.full_name || 'Treasury of Scripture Knowledge',
      srcModuleInfo.author || null,
      identity.extractYear(srcModuleInfo.year_published || copyright) || null,
      copyright,
      licenseSpdx,
      identity.licenseUrlForSpdx(licenseSpdx) || null,
      srcModuleInfo.source_url || null,
      identity.stripRtfArtifacts(srcModuleInfo.description || '')
        || 'Treasury of Scripture Knowledge cross-references',
      languageCode,
      identity.VERSIFICATION,
      contentVersion,
      JSON.stringify({ sourceModule: 'commentary_tsk', convertedDate: new Date().toISOString() })
    ]
  );

  // Bulk insert in a transaction
  console.log('Writing groups and entries...');
  await dbExec(targetDb, 'BEGIN TRANSACTION;');

  const groupStmt = 'INSERT INTO cross_reference_group (verse_id_start, verse_id_end, phrase, sort_order) VALUES (?, ?, ?, ?)';

  let insertedGroups = 0;
  let insertedEntries = 0;

  for (const verseData of allData) {
    let groupOrder = 0;

    for (const group of verseData.groups) {
      groupOrder++;
      const result = await dbRun(targetDb, groupStmt, [verseData.verseId, verseData.verseIdEnd, group.phrase, groupOrder]);
      const groupId = result.lastID;
      insertedGroups++;

      let entryOrder = 0;
      for (const entry of group.entries) {
        entryOrder++;
        // The reference target is a verse_link row. `context` keeps the phrase the reference was
        // attached to, so a consumer never has to join back to read it.
        await dbRun(targetDb, identity.VERSE_LINK_INSERT_SQL, [
          'cross_reference_group',
          groupId,
          entry.targetVerseId,
          identity.verseLinkEnd(entry.targetVerseId, entry.targetVerseEndId),
          'cross_reference',
          entryOrder,
          group.phrase || null,
        ]);
        insertedEntries++;
      }
    }
  }

  await dbExec(targetDb, 'COMMIT;');

  console.log(`Inserted: ${insertedGroups} groups, ${insertedEntries} entries.`);

  // Update module_info with counts and the content hash
  // Aliased to avoid collision: `g` carries the source passage range,
  // `vl` carries each cross-reference target's range.
  const hashRows = await dbAll(targetDb, `
    SELECT g.verse_id_start AS g_start, g.verse_id_end AS g_end, g.phrase,
           vl.verse_id_start AS vl_start, vl.verse_id_end AS vl_end
    FROM cross_reference_group g
    LEFT JOIN verse_link vl
      ON vl.source_type = 'cross_reference_group' AND vl.source_id = g.group_id
    ORDER BY g.group_id, vl.sort_order
  `);
  const contentSha256 = identity.sha256Hex(
    hashRows
      .map(r => `${r.g_start}\t${r.g_end}\t${r.phrase ?? ''}\t${r.vl_start ?? ''}\t${r.vl_end ?? ''}`)
      .join('\n')
  );

  await dbRun(targetDb,
    `UPDATE module_info SET metadata = ?, content_sha256 = ? WHERE info_id = 1`,
    [
      JSON.stringify({
        sourceModule: 'commentary_tsk',
        convertedDate: new Date().toISOString(),
        groupCount: insertedGroups,
        entryCount: insertedEntries,
        verseCount: allData.length
      }),
      contentSha256,
    ]
  );

  await closeDb(targetDb);

  console.log('\n=== Done! ===');
  console.log(`Output: ${TARGET_DB_PATH}`);
  console.log(`Groups: ${insertedGroups}`);
  console.log(`Entries: ${insertedEntries}`);
  console.log(`Verses with refs: ${allData.length}`);
}

main().catch(err => {
  console.error('FATAL:', err);
  process.exit(1);
});
