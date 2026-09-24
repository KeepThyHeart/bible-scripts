#!/usr/bin/env node

/**
 * validate-module.js — Public conformance checker for Bible app module databases.
 *
 * Validates a module `.db` file against the module format contract:
 *   • `module_info` exists and carries the single `info_id = 1` row.
 *   • A recognised content table for the module type is present.
 *   • Bible modules stay inside the 66-book Protestant canon and the standard
 *     English (KJV) versification: every (book, chapter, verse) triple present in
 *     the module must exist in the canonical reference space, and per-book chapter
 *     counts must not EXCEED the canonical chapter count.
 *   • `module_info` declares the identity block (uuid, format, versioning,
 *     versification, licensing) that a generic consumer reads without knowing the type.
 *   • FTS5 tables put the rowid key in column 0, because callers address the text
 *     column positionally (`highlight(fts, 1, ...)`).
 *   • The primary text column actually contains TEXT. A converter bug once
 *     produced modules where 99% of entries were a few bytes of binary noise and
 *     every structural check still passed — the shape was perfect and the content
 *     was gone.
 *
 * A canonical *subset* passes (an OT-only Bible, a Pentateuch-only Bible). Only
 * *shifted* or *out-of-canon* numbering fails — that is what silently collides in
 * the shared verse-id space.
 *
 * Canon source: `main.db` (`chapter_info` + `bible_book`) when it is populated,
 * otherwise the checked-in `scripts/data/kjv-versification.json`.
 *
 * Uses the async `sqlite3` driver (NOT better-sqlite3) so it runs under system
 * Node.js. All module databases are opened READ-ONLY — this script never writes.
 *
 * Usage:
 *   node scripts/modules/validate-module.js <module> [options]
 *   node scripts/modules/validate-module.js --all [options]
 *
 * Arguments:
 *   <module>            DB filename ("bible_kjv"), abbreviation ("kjv"), or full path.
 *
 * Options:
 *   --all               Validate every .db module in the modules directory.
 *   --type=<type>       With --all, restrict to one type (bible, commentary,
 *                       dictionary, lexicon, book, devotional, topical, xref,
 *                       tag_graph).
 *   --json              Emit machine-readable JSON instead of a text report.
 *   --quiet             Only print failures (text mode).
 *   --modules-dir=PATH  Override the modules directory.
 *   --main-db=PATH      Override the main.db used as the canon source.
 *
 * Exit code: 0 if everything validated passes, 1 if any module fails,
 *            2 on a usage/setup error.
 */

'use strict';

const path = require('path');
const fs = require('fs');
const sqlite3 = require('sqlite3');

// ============================================================================
// Constants
// ============================================================================

const {
  MODULES_DIR: DEFAULT_MODULES_DIR,
  MAIN_DB_PATH: DEFAULT_MAIN_DB,
  KJV_VERSIFICATION_JSON: VERSIFICATION_PATH,
} = require('../lib/paths');
const codec = require('../lib/codec');
const { CONTENT_MAP: DIGEST_CONTENT_MAP, computeContentSha256 } = require('../lib/content-digest');

const MAX_BOOK_NUMBER = 66;

/**
 * Content tables that identify a module type. A module needs at least one.
 *
 * Topical index content is `topic` (singular), cross-reference content is
 * `cross_reference_group`, and `tag_graph` is keyed on its entity/association
 * tables. Per-type schemas: the Bible repo's `packages/core/sql/schemas/initial/`.
 */
const CONTENT_TABLES = {
  bible: ['bible_verse'],
  commentary: ['commentary_entry'],
  dictionary: ['dictionary_entry'],
  lexicon: ['dictionary_entry'],   // a lexicon is a dictionary-shaped module
  book: ['book_section'],
  devotional: ['devotional_entry'],
  topical: ['topic'],
  xref: ['cross_reference_group'],
  tag_graph: ['tag_association', 'person', 'place', 'object', 'theme'],
};

/**
 * Tables every conforming module MUST carry, whatever its type.
 * `verse_link` is the one uniform content->verse linking shape (may be empty);
 * `schema_version` records the schema revision the file was built at.
 */
const REQUIRED_TABLES = ['verse_link', 'schema_version'];

/**
 * The only versification in use. The canon (66-book Protestant) is fixed and not
 * recorded in `module_info`.
 */
const REQUIRED_VERSIFICATION = 'kjv-english';

/** RFC 4122 UUID (any version/variant). `module_uuid` MUST match. */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Non-conforming range column spellings. A module MUST NOT declare these in ANY
 * table; ranges are `verse_id_start` / `verse_id_end`.
 */
const FORBIDDEN_RANGE_COLUMNS = ['start_verse_id', 'end_verse_id'];

/**
 * `module_info` columns the format requires of every module type.
 * A consumer reads these without knowing the module type, so a module missing
 * one is unusable generically even if its own content is fine.
 */
const REQUIRED_INFO_COLUMNS = [
  'module_uuid', 'abbreviation', 'full_name',
  'format', 'format_version', 'content_version', 'content_sha256',
  'versification',
  'license_spdx', 'source_url',
  'compression', // design §2.1 — v0.2 adds this; the column existing is required of
                 // every module this validator sees, only its VALUE is version-gated below.
];

/** The exact format_version string this validator applies the v0.2 rules to. */
const V2_FORMAT_VERSION = '0.2';

/** Known codec vocabulary. Open set by design (§2.1: "no CHECK, expected to grow") — an
 * unrecognised value is a warning here, never an error, so this validator doesn't have
 * to be updated in lockstep with every future codec. */
const KNOWN_CODECS = new Set(['none', 'deflate', 'zstd']);

/**
 * FTS5 shape contract: for each content table, column 0 of its `_fts` table must
 * be the rowid key, declared UNINDEXED.
 *
 * This is load-bearing rather than cosmetic. Callers address FTS columns
 * positionally — `highlight(bible_verse_fts, 1, ...)` in
 * BibleRepository.searchVersesWithHighlighting means "the text column". Drop the
 * leading key column and index 1 silently points at the wrong column, or at none.
 */
const FTS_SHAPE = {
  bible_verse: { fts: 'bible_verse_fts', key: 'verse_id' },
  commentary_entry: { fts: 'commentary_entry_fts', key: 'entry_id' },
  dictionary_entry: { fts: 'dictionary_entry_fts', key: 'entry_id' },
  devotional_entry: { fts: 'devotional_entry_fts', key: 'entry_id' },
  book_section: { fts: 'book_section_fts', key: 'section_id' },
};

/**
 * Content sanity: the primary text column of each module type.
 *
 * A module can be structurally perfect and still hold garbage. A converter bug
 * (the SWBuf use-after-free) produced modules where 99% of entries were a few
 * bytes of binary noise, and this validator passed all of them — it was checking
 * the shape of the container and never looking inside. These thresholds are
 * deliberately loose: they are meant to catch wholesale corruption, not to judge
 * editorial quality.
 */
const CONTENT_COLUMNS = {
  bible: { table: 'bible_verse', column: 'text', minChars: 8 },
  commentary: { table: 'commentary_entry', column: 'content', minChars: 12 },
  dictionary: { table: 'dictionary_entry', column: 'definition', minChars: 8 },
  devotional: { table: 'devotional_entry', column: 'content', minChars: 12 },
  book: { table: 'book_section', column: 'content', minChars: 12 },
};

/**
 * Two thresholds, because "short" and "not text" are very different signals.
 *
 * U+FFFD means bytes that were never valid UTF-8 — nothing legitimate produces
 * those, so a low bar is right. Shortness alone is unreliable: `bdbglosses` is a
 * Strong's gloss list where 3,461 of 8,674 entries (39%) are legitimately just a
 * key with no gloss. Only near-total shortness
 * indicates corruption.
 *
 * Calibrated against observed real data:
 *   corrupt  barnes 99.3% short / 69.3% U+FFFD  · mhc 100% / 96.6%
 *            easton 98.9% / 98.9%  · dbd 100% / 0%  · ylt 100% / 100%
 *            lo 100% / 32.3%
 *   healthy  bdbglosses 39% / 0%   · kjv 0.01% / 0%  · concord 0% / 0%
 *            webster1913 0% / 13.9%  · jochrist 0% / 25.9%
 *
 * SHORTNESS is the reliable discriminator: every corrupt module ran >= 98%
 * short, every healthy one <= 39%. U+FFFD on its own is NOT — `webster1913`
 * (13.9%) and `jochrist` (25.9%) carry replacement characters in identical
 * numbers, because the SWORD sources lost Latin ligatures (`ædificari`,
 * `præparans`) long before we touched them. Failing on that would reject data we
 * cannot improve.
 */
const CONTENT_NONTEXT_RATIO = 0.60;   // only overwhelming non-text fails on its own
const CONTENT_SHORT_RATIO = 0.80;

const colors = {
  reset: '\x1b[0m',
  bright: '\x1b[1m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  cyan: '\x1b[36m',
  dim: '\x1b[2m',
};

// ============================================================================
// SQLite helpers (read-only)
// ============================================================================

function openReadOnly(dbPath) {
  return new Promise((resolve, reject) => {
    const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
      if (err) reject(new Error(`Cannot open database: ${dbPath} (${err.message})`));
      else resolve(db);
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

function dbGet(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.get(sql, params, (err, row) => {
      if (err) reject(err);
      else resolve(row || null);
    });
  });
}

function dbClose(db) {
  return new Promise((resolve) => {
    if (!db) { resolve(); return; }
    db.close(() => resolve());
  });
}

// ============================================================================
// Module resolution (mirrors scripts/query/db-cli.js)
// ============================================================================

/**
 * Resolve a module identifier to a full .db file path.
 * Accepts: full path, filename, or abbreviation/prefix.
 */
function resolveModule(identifier, modulesDir = DEFAULT_MODULES_DIR) {
  if (path.isAbsolute(identifier) || identifier.includes('/') || identifier.includes('\\')) {
    if (!fs.existsSync(identifier)) {
      throw new Error(`Database file not found: ${identifier}`);
    }
    return identifier;
  }

  const withExt = identifier.endsWith('.db') ? identifier : `${identifier}.db`;

  const directPath = path.join(modulesDir, withExt);
  if (fs.existsSync(directPath)) return directPath;

  const prefixes = ['bible_', 'commentary_', 'dictionary_', 'lexicon_', 'topical_', 'book_', 'devotional_', 'xref_', 'tag_graph'];
  for (const prefix of prefixes) {
    const prefixed = path.join(modulesDir, `${prefix}${withExt}`);
    if (fs.existsSync(prefixed)) return prefixed;
  }

  if (fs.existsSync(modulesDir)) {
    const files = fs.readdirSync(modulesDir).filter(f => f.endsWith('.db'));
    const lower = identifier.toLowerCase();
    const match = files.find(f => f.toLowerCase().includes(lower));
    if (match) return path.join(modulesDir, match);
  }

  throw new Error(`Cannot resolve module: "${identifier}". Provide a filename, abbreviation, or full path.`);
}

async function listTables(db) {
  const rows = await dbAll(db, "SELECT name FROM sqlite_master WHERE type IN ('table','view')");
  return new Set(rows.map(r => r.name));
}

function detectModuleType(tableNames) {
  if (tableNames.has('bible_verse')) return 'bible';
  if (tableNames.has('commentary_entry')) return 'commentary';
  if (tableNames.has('dictionary_entry')) return 'dictionary';
  if (tableNames.has('book_section')) return 'book';
  if (tableNames.has('devotional_entry')) return 'devotional';
  if (tableNames.has('topic')) return 'topical';
  if (tableNames.has('cross_reference_group')) return 'xref';
  if (tableNames.has('tag_association') || tableNames.has('entity_verse_link')
      || (tableNames.has('person') && tableNames.has('place'))) return 'tag_graph';
  return 'unknown';
}

/** Module type implied by the filename prefix (used when detection fails). */
function typeFromFilename(file) {
  const base = path.basename(file);
  if (base.startsWith('xref_')) return 'xref';
  if (base.startsWith('tag_graph')) return 'tag_graph';
  const prefix = base.split('_')[0];
  return Object.prototype.hasOwnProperty.call(CONTENT_TABLES, prefix) ? prefix : null;
}

// ============================================================================
// Canonical reference space
// ============================================================================

/**
 * Load the canonical versification.
 *
 * Returns { source, chapters: Map<bookNumber, number[]>, bookNames: Map<number,string> }
 * where chapters.get(n)[i] is the verse count of chapter i+1 of book n.
 *
 * Prefers the materialised reference space in main.db (chapter_info); falls back
 * to the checked-in canonical data file when main.db is absent or unpopulated.
 */
async function loadCanon(mainDbPath = DEFAULT_MAIN_DB) {
  const chapters = new Map();
  const bookNames = new Map();

  if (fs.existsSync(mainDbPath)) {
    let db = null;
    try {
      db = await openReadOnly(mainDbPath);
      const rows = await dbAll(db, `
        SELECT b.book_number AS book_number, b.book_name AS book_name,
               ci.chapter AS chapter, ci.verse_count AS verse_count
        FROM chapter_info ci
        JOIN bible_book b ON b.book_id = ci.book_id
        ORDER BY b.book_number, ci.chapter
      `);
      if (rows.length > 0) {
        for (const row of rows) {
          if (!chapters.has(row.book_number)) chapters.set(row.book_number, []);
          chapters.get(row.book_number)[row.chapter - 1] = row.verse_count;
          bookNames.set(row.book_number, row.book_name);
        }
        return { source: `main.db (${mainDbPath})`, chapters, bookNames };
      }
    } catch {
      // main.db missing the reference tables — fall through to the data file.
    } finally {
      await dbClose(db);
    }
  }

  if (!fs.existsSync(VERSIFICATION_PATH)) {
    throw new Error(
      `No canon source available: main.db reference space is empty and ${VERSIFICATION_PATH} is missing.`
    );
  }
  const doc = JSON.parse(fs.readFileSync(VERSIFICATION_PATH, 'utf8'));
  for (const book of doc.books) {
    chapters.set(book.book_number, book.verse_counts.slice());
    bookNames.set(book.book_number, book.name);
  }
  return { source: `data file (${VERSIFICATION_PATH})`, chapters, bookNames };
}

// ============================================================================
// Checks
// ============================================================================

function err(code, message, extra) {
  return Object.assign({ code, message }, extra || {});
}

async function checkModuleInfo(db, tableNames, result) {
  const infoTable = 'module_info';
  if (!tableNames.has(infoTable)) {
    result.errors.push(err('missing_module_info', 'No `module_info` table found.'));
    return;
  }

  const row = await dbGet(db, `SELECT * FROM ${infoTable} WHERE info_id = 1`);
  if (!row) {
    // Distinguish "table empty" from "row uses a different id".
    const any = await dbGet(db, `SELECT COUNT(*) AS cnt FROM ${infoTable}`);
    const cnt = any ? any.cnt : 0;
    result.errors.push(err(
      'missing_module_info_row',
      cnt === 0
        ? `\`${infoTable}\` is empty; the format requires a single row with info_id = 1.`
        : `\`${infoTable}\` has ${cnt} row(s) but none with info_id = 1.`
    ));
    return;
  }

  result.info = {
    abbreviation: row.abbreviation ?? null,
    name: row.full_name ?? row.title ?? row.name ?? null,
    version: row.content_version ?? row.version ?? null,
    language_code: row.language_code ?? null,
    module_uuid: row.module_uuid ?? null,
    versification: row.versification ?? null,
    format_version: row.format_version ?? null,
  };

  // `module_uuid` MUST be present and a valid UUID — it, not
  // `abbreviation`, is the cross-database join key.
  if (row.module_uuid === undefined || row.module_uuid === null || row.module_uuid === '') {
    result.errors.push(err('missing_module_uuid',
      '`module_info.module_uuid` is empty; a module MUST carry a stable RFC 4122 UUID.'));
  } else if (!UUID_RE.test(String(row.module_uuid))) {
    result.errors.push(err('invalid_module_uuid',
      `\`module_info.module_uuid\` = ${JSON.stringify(row.module_uuid)} is not a valid UUID.`));
  }

  // The versification is a declaration with one accepted value. The column
  // may exist (checked below) but hold a shifted value, which is precisely what
  // silently collides in the shared verse-id space, so the VALUE is checked,
  // not just the column.
  if (row.versification !== undefined && row.versification !== REQUIRED_VERSIFICATION) {
    result.errors.push(err('wrong_versification',
      `\`module_info.versification\` = ${JSON.stringify(row.versification)}; the format accepts only '${REQUIRED_VERSIFICATION}'.`));
  }

  // Required identity columns. Checked against the declared schema rather than
  // the row, because a NULL value is a data gap while a missing column breaks any
  // generic consumer's query outright.
  const declared = new Set(
    (await dbAll(db, `SELECT name FROM pragma_table_info('${infoTable}')`)).map(c => c.name)
  );
  const missing = REQUIRED_INFO_COLUMNS.filter(c => !declared.has(c));
  if (missing.length) {
    result.errors.push(err(
      'missing_info_columns',
      `\`${infoTable}\` is missing required column(s): ${missing.join(', ')}.`
    ));
  }

  const total = await dbGet(db, `SELECT COUNT(*) AS cnt FROM ${infoTable}`);
  if (total && total.cnt > 1) {
    result.warnings.push(err(
      'multiple_module_info_rows',
      `\`${infoTable}\` has ${total.cnt} rows; exactly one (info_id = 1) is expected.`
    ));
  }
}

/**
 * Verify the FTS5 tables have the declared column order (pre-v0.2), or that
 * NONE exist at all (v0.2: design §2.3 deletes every FTS5 table and trigger
 * — the sidecar index replaces it, built at install, never shipped in the
 * module file).
 *
 * Only tables that actually exist are checked (pre-v0.2 branch): a module
 * legitimately ships FTS only for the content it has. `content=`/
 * `content_rowid=` options do not appear in pragma_table_info, so the check
 * is on the visible column list.
 */
async function checkFtsShape(db, tableNames, result, isV2) {
  if (isV2) {
    const present = Object.values(FTS_SHAPE).map(s => s.fts).filter(fts => tableNames.has(fts));
    if (present.length) {
      result.errors.push(err(
        'fts5_table_present',
        `v0.2 modules MUST carry no FTS5 table (design §2.3 — the sidecar index replaces it, ` +
        `built at install): found ${present.join(', ')}.`
      ));
    }
    // A v0.2 file with an fts5 table under some OTHER name would slip past the
    // check above; sqlite_master's own module column catches that generically.
    let virtualTables;
    try {
      virtualTables = await dbAll(db,
        "SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE '%VIRTUAL TABLE%fts5%'");
    } catch { virtualTables = []; }
    const unexpected = virtualTables.map(r => r.name).filter(n => !present.includes(n));
    if (unexpected.length) {
      result.errors.push(err(
        'fts5_table_present',
        `v0.2 modules MUST carry no FTS5 table (design §2.3): found ${unexpected.join(', ')}.`
      ));
    }
    return;
  }

  for (const [contentTable, { fts, key }] of Object.entries(FTS_SHAPE)) {
    if (!tableNames.has(contentTable) || !tableNames.has(fts)) continue;

    let cols;
    try {
      cols = await dbAll(db, `SELECT name FROM pragma_table_info('${fts}')`);
    } catch {
      continue; // unreadable virtual table: not this check's business
    }
    if (!cols.length) continue;

    if (cols[0].name !== key) {
      result.errors.push(err(
        'fts_column_order',
        `\`${fts}\` column 0 is \`${cols[0].name}\`, expected \`${key}\` (UNINDEXED). ` +
        `Positional highlight()/snippet() calls address the text column by index and ` +
        `will read the wrong column.`
      ));
    }
  }
}

/**
 * v0.2 compression + digest checks (design §2.8, task 0035 requirement 7):
 *   - compression_dictionary row present iff module_info.compression != 'none'.
 *   - content_sha256 recomputed from the finished file equals the stored value
 *     (the canonical, codec-invariant digest — design §2.7).
 *
 * Returns `{ compression, dictionary, proseColumns }` — or null when there is
 * nothing to check — so checkContentSanity() can reuse the same compression
 * info instead of re-querying it.
 */
async function checkCompressionAndDigest(db, tableNames, moduleType, result, isV2) {
  if (!tableNames.has('module_info')) return null;

  const info = await dbGet(db, 'SELECT compression, content_sha256 FROM module_info WHERE info_id = 1');
  if (!info) return null;

  const compression = info.compression || 'none';

  if (isV2 && compression !== 'none' && !KNOWN_CODECS.has(compression)) {
    result.warnings.push(err('unknown_codec',
      `\`module_info.compression\` = ${JSON.stringify(compression)} is not one this validator ` +
      `recognises (${[...KNOWN_CODECS].join('/')}). The codec vocabulary is open by design (§2.1); ` +
      `this is a warning, not an error, so a new codec doesn't need this file changed first.`));
  }

  let dictionary = null;
  if (tableNames.has('compression_dictionary')) {
    const dictRow = await dbGet(db, 'SELECT dict_blob FROM compression_dictionary WHERE codec = ?', [compression]);
    const dictCount = await dbGet(db, 'SELECT COUNT(*) AS cnt FROM compression_dictionary');

    if (isV2) {
      if (compression === 'none' && dictCount && dictCount.cnt > 0) {
        result.errors.push(err('unexpected_compression_dictionary',
          `\`compression_dictionary\` has ${dictCount.cnt} row(s) but \`module_info.compression\` ` +
          `is 'none'; a row exists if and only if the frames were encoded against a dictionary (§2.2).`));
      } else if (compression !== 'none' && !dictRow) {
        result.errors.push(err('missing_compression_dictionary',
          `\`module_info.compression\` = '${compression}' but \`compression_dictionary\` has no row ` +
          `for it; a dictionary row is required whenever compression != 'none' (§2.8).`));
      }
    }
    dictionary = dictRow ? dictRow.dict_blob : null;
  } else if (isV2 && compression !== 'none') {
    result.errors.push(err('missing_compression_dictionary',
      `\`module_info.compression\` = '${compression}' but this module has no \`compression_dictionary\` ` +
      `table at all (§2.2, §2.8).`));
  }

  const shapes = DIGEST_CONTENT_MAP[moduleType];
  if (shapes && info.content_sha256) {
    let recomputed;
    try {
      recomputed = await computeContentSha256(db, moduleType, { compression, dictionary });
    } catch (e) {
      result.errors.push(err('digest_recompute_failed',
        `Could not recompute content_sha256: ${e.message}`));
      recomputed = null;
    }
    if (recomputed !== null && recomputed !== info.content_sha256) {
      result.errors.push(err('content_sha256_mismatch',
        `\`module_info.content_sha256\` = ${info.content_sha256} but the recomputed digest is ` +
        `${recomputed}; the stored value no longer matches the file's own content (design §2.7).`));
    }
  }

  const proseColumns = new Set();
  if (shapes) {
    for (const shape of shapes) for (const c of shape.prose) proseColumns.add(c);
  }
  return { compression, dictionary, proseColumns };
}

/**
 * Look inside the module: is the primary text column actually text?
 *
 * Two independent signals, because either alone gives false positives:
 *   - implausibly short entries (a real commentary entry is not 6 bytes)
 *   - U+FFFD replacement characters, which mean bytes that were never valid UTF-8
 *
 * Reported as an ERROR past the threshold and a WARNING below it, so a module
 * with a handful of genuinely terse entries is not failed outright.
 */
async function checkContentSanity(db, tableNames, moduleType, result, compressionInfo) {
  const spec = CONTENT_COLUMNS[moduleType];
  if (!spec || !tableNames.has(spec.table)) return;

  const { table, column, minChars } = spec;
  const compression = compressionInfo && compressionInfo.compression;
  const isProse = compressionInfo && compressionInfo.proseColumns && compressionInfo.proseColumns.has(column);

  let stats;
  if (compression && compression !== 'none' && isProse) {
    // Decode-aware path: the column may hold a compressed BLOB (§3.2), so a
    // plain SQL LIKE/length() over the raw bytes would either mis-measure
    // (byte length, not char length) or simply never match — silently
    // turning this whole check into a no-op for every compressed module,
    // which is the "content scan no longer reports content_corrupt" bug
    // task 0035's acceptance criteria call out by name.
    let rows;
    try {
      rows = await dbAll(db, `SELECT "${column}" AS value FROM "${table}"`);
    } catch {
      return;
    }
    if (!rows.length) return;

    let short = 0;
    let nontext = 0;
    for (const row of rows) {
      let text;
      try {
        text = await codec.decodeAsync(compression, row.value, compressionInfo.dictionary);
      } catch (e) {
        // A cell that fails to decode is exactly the corruption this check
        // exists to catch — count it, don't let the decode error hide it.
        nontext++;
        continue;
      }
      if (text === null || text === undefined || text.trim().length < minChars) short++;
      if (typeof text === 'string' && text.includes('�')) nontext++;
    }
    stats = { total: rows.length, short, nontext };
  } else {
    try {
      stats = await dbGet(db, `
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN ${column} IS NULL OR length(trim(${column})) < ${minChars} THEN 1 ELSE 0 END) AS short,
               SUM(CASE WHEN ${column} LIKE '%' || char(65533) || '%' THEN 1 ELSE 0 END) AS nontext
          FROM ${table}
      `);
    } catch {
      return; // column absent: not this check's business
    }
  }
  if (!stats || !stats.total) return;

  const shortRatio = (stats.short || 0) / stats.total;
  const nonTextRatio = (stats.nontext || 0) / stats.total;
  const pct = n => `${(n * 100).toFixed(1)}%`;

  if (nonTextRatio >= CONTENT_NONTEXT_RATIO || shortRatio >= CONTENT_SHORT_RATIO) {
    result.errors.push(err(
      'content_corrupt',
      `\`${table}.${column}\` does not look like text: ` +
      `${stats.short}/${stats.total} rows (${pct(shortRatio)}) shorter than ${minChars} chars, ` +
      `${stats.nontext}/${stats.total} (${pct(nonTextRatio)}) contain U+FFFD. ` +
      `This is the signature of a converter reading freed or mis-decoded memory.`
    ));
    return;
  }

  if (nonTextRatio >= 0.05 || shortRatio >= 0.20) {
    result.warnings.push(err(
      'content_thin',
      `${stats.short} very short (${pct(shortRatio)}) and ${stats.nontext} non-UTF8 ` +
      `(${pct(nonTextRatio)}) row(s) in \`${table}.${column}\`. Below the failure ` +
      `thresholds (${pct(CONTENT_SHORT_RATIO)} short / ${pct(CONTENT_NONTEXT_RATIO)} non-UTF8). ` +
      `Often inherited from the source module rather than introduced by conversion — ` +
      `compare against the previous version before treating it as a defect.`
    ));
  }
}

function checkContentTable(tableNames, moduleType, result) {
  const expected = CONTENT_TABLES[moduleType];
  if (!expected) {
    result.errors.push(err('unknown_module_type', `Cannot determine module type — no recognised content table found.`));
    return;
  }
  if (!expected.some(t => tableNames.has(t))) {
    result.errors.push(err(
      'missing_content_table',
      `Missing content table (expected one of: ${expected.join(', ')}).`
    ));
  }
}

/**
 * Canon + versification check for bible modules.
 *
 * Aggregates the module's verse ids per (book, chapter) and compares against the
 * canonical space. Because the canon is contiguous — every book has chapters
 * 1..N and every chapter has verses 1..M — bounding each chapter's min/max verse
 * inside [1, M] is exactly equivalent to asserting every individual
 * (book, chapter, verse) triple exists in `bible_verse_ref`, at ~1200 rows read
 * instead of ~31000.
 */
async function checkBibleCanon(db, canon, result) {
  const rows = await dbAll(db, `
    SELECT verse_id / 1000000                     AS book_number,
           (verse_id % 1000000) / 1000            AS chapter,
           MIN(verse_id % 1000)                   AS min_verse,
           MAX(verse_id % 1000)                   AS max_verse,
           COUNT(*)                               AS verse_rows
    FROM bible_verse
    GROUP BY book_number, chapter
    ORDER BY book_number, chapter
  `);

  if (rows.length === 0) {
    result.errors.push(err('empty_bible', '`bible_verse` contains no rows.'));
    return;
  }

  const perBook = new Map(); // book_number -> { chapters:[], maxChapter, verses }
  for (const row of rows) {
    if (!perBook.has(row.book_number)) {
      perBook.set(row.book_number, { chapters: [], maxChapter: 0, verses: 0 });
    }
    const entry = perBook.get(row.book_number);
    entry.chapters.push(row);
    entry.maxChapter = Math.max(entry.maxChapter, row.chapter);
    entry.verses += row.verse_rows;
  }

  const bookNumbers = [...perBook.keys()].sort((a, b) => a - b);
  result.stats = {
    books: bookNumbers.length,
    chapters: rows.length,
    verses: rows.reduce((s, r) => s + r.verse_rows, 0),
    first_book: bookNumbers[0],
    last_book: bookNumbers[bookNumbers.length - 1],
  };

  for (const bookNumber of bookNumbers) {
    const entry = perBook.get(bookNumber);
    const canonChapters = canon.chapters.get(bookNumber);

    if (bookNumber < 1 || bookNumber > MAX_BOOK_NUMBER || !canonChapters) {
      result.errors.push(err('book_out_of_canon',
        `Book ${bookNumber} is outside the 66-book Protestant canon ` +
        `(${entry.chapters.length} chapters, ${entry.verses} verses present).`,
        { book_number: bookNumber, actual_chapters: entry.chapters.length }
      ));
      continue;
    }

    const expectedChapters = canonChapters.length;
    if (entry.maxChapter > expectedChapters) {
      result.errors.push(err('chapter_count_exceeds_canon',
        `${canon.bookNames.get(bookNumber) || `Book ${bookNumber}`} (book ${bookNumber}): ` +
        `${entry.chapters.length} chapters present, highest chapter ${entry.maxChapter}, ` +
        `canonical chapter count ${expectedChapters}.`,
        {
          book_number: bookNumber,
          book_name: canon.bookNames.get(bookNumber) || null,
          expected_chapters: expectedChapters,
          actual_chapters: entry.chapters.length,
          highest_chapter: entry.maxChapter,
        }
      ));
    }

    for (const row of entry.chapters) {
      if (row.chapter < 1) {
        result.errors.push(err('invalid_chapter',
          `Book ${bookNumber} contains chapter ${row.chapter} (chapters start at 1).`,
          { book_number: bookNumber, chapter: row.chapter }
        ));
        continue;
      }
      if (row.chapter > expectedChapters) continue; // already reported at book level

      const expectedVerses = canonChapters[row.chapter - 1];
      if (row.min_verse < 1) {
        result.errors.push(err('invalid_verse',
          `Book ${bookNumber} chapter ${row.chapter} contains verse ${row.min_verse} (verses start at 1).`,
          { book_number: bookNumber, chapter: row.chapter, verse: row.min_verse }
        ));
      }
      if (row.max_verse > expectedVerses) {
        result.errors.push(err('verse_out_of_canon',
          `${canon.bookNames.get(bookNumber) || `Book ${bookNumber}`} ${row.chapter}: ` +
          `highest verse ${row.max_verse}, canonical verse count ${expectedVerses}.`,
          {
            book_number: bookNumber,
            chapter: row.chapter,
            expected_verses: expectedVerses,
            highest_verse: row.max_verse,
          }
        ));
      }
    }
  }
}

/**
 * Every module MUST carry `verse_link` and `schema_version`, and
 * `schema_version` MUST have at least one row.
 */
async function checkRequiredTables(db, tableNames, result) {
  for (const t of REQUIRED_TABLES) {
    if (!tableNames.has(t)) {
      result.errors.push(err('missing_required_table',
        `Required table \`${t}\` is missing (every module MUST carry it).`));
    }
  }
  if (tableNames.has('schema_version')) {
    let row;
    try { row = await dbGet(db, 'SELECT COUNT(*) AS cnt FROM schema_version'); }
    catch { row = null; }
    if (!row || !row.cnt) {
      result.errors.push(err('empty_schema_version',
        '`schema_version` exists but has no rows; at least one row is required.'));
    }
  }
}

/**
 * `verse_link` MUST carry all four indexes, and its
 * `verse_id_end` MUST be non-NULL on every row — a single verse is spelled
 * `verse_id_end = verse_id_start`, never NULL. This is what makes the
 * containment probe `verse_id_start <= :v AND verse_id_end >= :v` correct
 * without a `COALESCE`/`OR IS NULL` branch, and what makes
 * `idx_verse_link_covering` a valid covering index in the first place.
 */
const REQUIRED_VERSE_LINK_INDEXES = [
  'idx_verse_link_source',
  'idx_verse_link_start',
  'idx_verse_link_range',
  'idx_verse_link_covering',
];

/**
 * v0.2 drops `idx_verse_link_start` (design §2.3: it is a strict prefix of
 * `idx_verse_link_range` and therefore redundant once the covering pair
 * exists). "F11 must change with it" — this is that change, gated on
 * format_version so a v0.1 file already in the wild isn't failed for
 * carrying the index the format used to require.
 */
const V2_REQUIRED_VERSE_LINK_INDEXES = [
  'idx_verse_link_source',
  'idx_verse_link_range',
  'idx_verse_link_covering',
];

async function checkVerseLinkShape(db, tableNames, result, isV2) {
  if (!tableNames.has('verse_link')) return; // reported separately by checkRequiredTables

  let indexRows;
  try {
    indexRows = await dbAll(db,
      "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'verse_link'");
  } catch { indexRows = []; }
  const indexNames = new Set(indexRows.map(r => r.name));
  const required = isV2 ? V2_REQUIRED_VERSE_LINK_INDEXES : REQUIRED_VERSE_LINK_INDEXES;
  const missingIndexes = required.filter(i => !indexNames.has(i));
  if (missingIndexes.length) {
    result.errors.push(err('missing_verse_link_index',
      `\`verse_link\` is missing required index(es): ${missingIndexes.join(', ')} ` +
      `(${required.length} are part of the ${isV2 ? 'v0.2' : 'pre-v0.2'} contract).`));
  }

  let nullEnd;
  try {
    nullEnd = await dbGet(db, 'SELECT COUNT(*) AS cnt FROM verse_link WHERE verse_id_end IS NULL');
  } catch { nullEnd = null; }
  if (nullEnd && nullEnd.cnt > 0) {
    result.errors.push(err('null_verse_link_end',
      `\`verse_link\` has ${nullEnd.cnt} row(s) with \`verse_id_end\` NULL; a single verse ` +
      `MUST be spelled \`verse_id_end = verse_id_start\`, never NULL.`));
  }
}

/**
 * The reversed range spellings `start_verse_id` / `end_verse_id` are
 * non-conforming and MUST NOT appear as a column in ANY table.
 */
async function checkForbiddenSpellings(db, tableNames, result) {
  for (const table of tableNames) {
    if (table.startsWith('sqlite_')) continue;
    let cols;
    try { cols = await dbAll(db, `SELECT name FROM pragma_table_info('${table}')`); }
    catch { continue; }
    const names = new Set(cols.map(c => c.name));
    for (const bad of FORBIDDEN_RANGE_COLUMNS) {
      if (names.has(bad)) {
        result.errors.push(err('forbidden_range_column',
          `Table \`${table}\` declares \`${bad}\`; the format spells ranges ` +
          `\`verse_id_start\` / \`verse_id_end\`. The reversed spelling is non-conforming.`));
      }
    }
  }
}

/**
 * Tables whose verse anchor is itself optional (a `'book'`/`'chapter'`-
 * level commentary entry, a non-scripture user note, a non-verse pinned item)
 * may leave BOTH range columns NULL together. Every other table that carries
 * `verse_id_start`/`verse_id_end` — `verse_link`, `cross_reference_group` — has
 * a mandatory range: both columns MUST be populated on every row.
 */
const ANCHOR_OPTIONAL_TABLES = new Set(['commentary_entry', 'user_note', 'pinned_item']);

/**
 * No range may have `verse_id_end < verse_id_start`, and in tables whose
 * range is mandatory (not in ANCHOR_OPTIONAL_TABLES — e.g. `verse_link`,
 * `cross_reference_group`), `verse_id_end` MUST NOT be NULL: a single verse is
 * `verse_id_end = verse_id_start`, never NULL. Anchor-optional tables may leave
 * both columns NULL together, but never one without the other.
 */
async function checkRangeSanity(db, tableNames, result) {
  for (const table of tableNames) {
    if (table.startsWith('sqlite_') || table.endsWith('_fts')) continue;
    let cols;
    try { cols = await dbAll(db, `SELECT name FROM pragma_table_info('${table}')`); }
    catch { continue; }
    const names = new Set(cols.map(c => c.name));
    if (!names.has('verse_id_start') || !names.has('verse_id_end')) continue;

    if (ANCHOR_OPTIONAL_TABLES.has(table)) {
      let half;
      try {
        half = await dbGet(db, `
          SELECT COUNT(*) AS cnt FROM ${table}
           WHERE (verse_id_start IS NULL) <> (verse_id_end IS NULL)
        `);
      } catch { half = null; }
      if (half && half.cnt > 0) {
        result.errors.push(err('half_populated_anchor',
          `\`${table}\` has ${half.cnt} row(s) with only one of verse_id_start/verse_id_end ` +
          `set; an anchor-optional table's range MUST be either both NULL or both populated.`));
      }
    } else {
      let nullEnd;
      try {
        nullEnd = await dbGet(db, `
          SELECT COUNT(*) AS cnt FROM ${table}
           WHERE verse_id_start IS NOT NULL AND verse_id_end IS NULL
        `);
      } catch { nullEnd = null; }
      if (nullEnd && nullEnd.cnt > 0) {
        result.errors.push(err('null_mandatory_range_end',
          `\`${table}\` has ${nullEnd.cnt} row(s) with \`verse_id_end\` NULL; this table's range ` +
          `is mandatory — a single verse MUST be \`verse_id_end = verse_id_start\`, never NULL.`));
      }
    }

    let row;
    try {
      row = await dbGet(db, `
        SELECT COUNT(*) AS cnt FROM ${table}
         WHERE verse_id_start IS NOT NULL AND verse_id_end IS NOT NULL
           AND verse_id_end < verse_id_start
      `);
    } catch { continue; }
    if (row && row.cnt > 0) {
      result.errors.push(err('inverted_range',
        `\`${table}\` has ${row.cnt} row(s) with verse_id_end < verse_id_start.`));
    }
  }
}

/**
 * Formatting spans and blocks the app understands
 * (`VERSE_SPAN_TYPES` and `VerseBlock` in the Bible repo's
 * packages/core/src/Data/Text/VerseFormatting.ts).
 */
const VALID_SPAN_TYPES = new Set([
  'divine_name', 'supplied', 'words_of_christ', 'emphasis', 'quotation',
  'transliteration', 'musical_direction',
]);
const VALID_BLOCK_KEYS = new Set(['paragraph_start', 'lines', 'heading', 'heading_kind', 'selah']);
const VALID_FORMATTING_KEYS = new Set(['v', 'block', 'spans', 'source_verses']);

/**
 * Bible `formatting` JSON: every span has a recognised type and 0-based,
 * inclusive word offsets inside the verse (`0 <= start <= end < word count`),
 * and blocks carry only known keys. Interlinear word positions follow the same
 * convention.
 */
async function checkBibleFormatting(db, tableNames, result) {
  if (!tableNames.has('bible_verse')) return;

  let rows;
  try {
    rows = await dbAll(db, 'SELECT verse_id, text, formatting FROM bible_verse WHERE formatting IS NOT NULL');
  } catch { return; }

  const bad = { json: 0, keys: 0, blockKeys: 0, spanType: 0, spanRange: 0 };
  const firstBad = {};
  const note = (kind, verseId) => { bad[kind]++; if (firstBad[kind] === undefined) firstBad[kind] = verseId; };

  for (const row of rows) {
    let fmt;
    try { fmt = JSON.parse(row.formatting); } catch { note('json', row.verse_id); continue; }
    if (!fmt || typeof fmt !== 'object') { note('json', row.verse_id); continue; }

    if (Object.keys(fmt).some(k => !VALID_FORMATTING_KEYS.has(k))) note('keys', row.verse_id);
    if (Object.keys(fmt.block || {}).some(k => !VALID_BLOCK_KEYS.has(k))) note('blockKeys', row.verse_id);

    const wordCount = String(row.text || '').split(/\s+/).filter(Boolean).length;
    for (const span of fmt.spans || []) {
      if (!VALID_SPAN_TYPES.has(span.type)) note('spanType', row.verse_id);
      const { start, end } = span;
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end >= wordCount) {
        note('spanRange', row.verse_id);
      }
    }
  }

  const report = (kind, code, message) => {
    if (bad[kind]) {
      result.errors.push(err(code, `${message} in ${bad[kind]} verse(s) or span(s); first at verse_id ${firstBad[kind]}.`));
    }
  };
  report('json', 'invalid_formatting_json', '`bible_verse.formatting` is not a JSON object');
  report('keys', 'unknown_formatting_key', '`formatting` has keys other than v/block/spans/source_verses');
  report('blockKeys', 'unknown_block_key', '`formatting.block` has an unrecognised key');
  report('spanType', 'unknown_span_type', '`formatting.spans` has an unrecognised span type');
  report('spanRange', 'span_out_of_range',
    '`formatting.spans` offsets are not 0-based inclusive word indexes inside the verse');

  if (tableNames.has('interlinear_word')) {
    const overflow = await dbGet(db, `
      SELECT COUNT(*) AS cnt
        FROM interlinear_word i JOIN bible_verse v ON v.verse_id = i.verse_id
       WHERE i.word_position_start < 0 OR i.word_position_end >= v.word_count
    `).catch(() => null);
    if (overflow && overflow.cnt) {
      result.errors.push(err('interlinear_position_range',
        `${overflow.cnt} \`interlinear_word\` row(s) have a word position that is negative or beyond the verse's \`word_count\`.`));
    }
  }
}

/**
 * Cheap Bible-text conformance: `text` carries no HTML tags, pilcrow,
 * or backslash, and `word_count` matches the whitespace token count of `text`.
 */
async function checkBibleText(db, tableNames, result) {
  if (!tableNames.has('bible_verse')) return;

  let hygiene;
  try {
    hygiene = await dbGet(db, `
      SELECT COUNT(*) AS total,
             SUM(CASE WHEN text LIKE '%<%>%' THEN 1 ELSE 0 END)                 AS html,
             SUM(CASE WHEN text LIKE '%' || char(182) || '%' THEN 1 ELSE 0 END) AS pilcrow,
             SUM(CASE WHEN text LIKE '%' || char(92)  || '%' THEN 1 ELSE 0 END) AS backslash
        FROM bible_verse
    `);
  } catch { return; }
  if (!hygiene || !hygiene.total) return;

  if (hygiene.html > 0) result.errors.push(err('bible_text_html',
    `\`bible_verse.text\` has ${hygiene.html} row(s) containing HTML/XML tags; ` +
    `text MUST be clean UTF-8 with structure in \`formatting\` instead.`));
  if (hygiene.pilcrow > 0) result.errors.push(err('bible_text_pilcrow',
    `\`bible_verse.text\` has ${hygiene.pilcrow} row(s) containing a pilcrow (U+00B6); ` +
    `paragraph structure belongs in \`formatting.block\`.`));
  if (hygiene.backslash > 0) result.errors.push(err('bible_text_backslash',
    `\`bible_verse.text\` has ${hygiene.backslash} row(s) containing a backslash (U+005C), ` +
    `which \`text\` MUST NOT contain.`));

  // word_count == whitespace token count. Conforming `text` is single-spaced with
  // no leading/trailing whitespace, so tokens = (space count) + 1. Only rows with a
  // populated word_count are checked (word_count is nullable).
  let wc;
  try {
    wc = await dbGet(db, `
      SELECT COUNT(*) AS mismatched FROM bible_verse
       WHERE word_count IS NOT NULL
         AND length(trim(text)) > 0
         AND word_count <> length(trim(text)) - length(replace(trim(text), ' ', '')) + 1
    `);
  } catch { wc = null; }
  if (wc && wc.mismatched > 0) {
    result.errors.push(err('word_count_mismatch',
      `\`bible_verse.word_count\` disagrees with the whitespace token count of \`text\` ` +
      `in ${wc.mismatched} row(s). Conforming text is single-spaced with no leading/` +
      `trailing whitespace.`));
  }
}

// ============================================================================
// Public API
// ============================================================================

/**
 * Validate a single module database.
 *
 * @param {string} dbPath        Full path to the module .db file.
 * @param {object} [options]
 * @param {object} [options.canon]      Pre-loaded canon (see loadCanon) — avoids re-reading main.db.
 * @param {string} [options.mainDbPath] main.db to source the canon from when `canon` is not supplied.
 * @returns {Promise<object>} result — `ok` is false when `errors` is non-empty.
 */
async function validateModule(dbPath, options = {}) {
  const result = {
    file: path.basename(dbPath),
    path: dbPath,
    moduleType: null,
    ok: false,
    errors: [],
    warnings: [],
    info: null,
    stats: null,
  };

  if (!fs.existsSync(dbPath)) {
    result.errors.push(err('file_not_found', `File does not exist: ${dbPath}`));
    return result;
  }

  let db = null;
  try {
    db = await openReadOnly(dbPath);
    const tableNames = await listTables(db);

    if (tableNames.size === 0) {
      result.errors.push(err('empty_database', 'Database contains no tables.'));
      return result;
    }

    result.moduleType = detectModuleType(tableNames);
    if (result.moduleType === 'unknown') {
      const guessed = typeFromFilename(dbPath);
      if (guessed) {
        result.moduleType = guessed;
        checkContentTable(tableNames, guessed, result);
      } else {
        result.errors.push(err('unknown_module_type',
          'Cannot determine module type — no recognised content table and no known filename prefix.'));
      }
    } else {
      checkContentTable(tableNames, result.moduleType, result);
    }

    await checkModuleInfo(db, tableNames, result);
    const isV2 = !!(result.info && result.info.format_version === V2_FORMAT_VERSION);

    await checkRequiredTables(db, tableNames, result);
    await checkVerseLinkShape(db, tableNames, result, isV2);
    await checkFtsShape(db, tableNames, result, isV2);
    await checkForbiddenSpellings(db, tableNames, result);
    await checkRangeSanity(db, tableNames, result);
    const compressionInfo = await checkCompressionAndDigest(db, tableNames, result.moduleType, result, isV2);
    await checkContentSanity(db, tableNames, result.moduleType, result, compressionInfo);
    await checkBibleText(db, tableNames, result);
    await checkBibleFormatting(db, tableNames, result);

    if (result.moduleType === 'bible' && tableNames.has('bible_verse')) {
      const canon = options.canon || await loadCanon(options.mainDbPath);
      await checkBibleCanon(db, canon, result);
    }
  } catch (e) {
    // Resilience: any driver/SQL failure becomes a clean reported error.
    result.errors.push(err('validation_error', e && e.message ? e.message : String(e)));
  } finally {
    await dbClose(db);
  }

  result.ok = result.errors.length === 0;
  return result;
}

/** Render a single result as a human-readable report. */
function formatResult(result, { verbose = true, maxDetails = 12 } = {}) {
  const lines = [];
  const status = result.ok
    ? `${colors.green}PASS${colors.reset}`
    : `${colors.red}FAIL${colors.reset}`;
  const type = result.moduleType || 'unknown';
  lines.push(`${status}  ${colors.bright}${result.file}${colors.reset} ${colors.dim}[${type}]${colors.reset}`);

  if (verbose && result.stats) {
    lines.push(`      ${colors.dim}${result.stats.books} books, ${result.stats.chapters} chapters, ${result.stats.verses} verses${colors.reset}`);
  }

  const shown = result.errors.slice(0, maxDetails);
  for (const e of shown) {
    lines.push(`      ${colors.red}✗${colors.reset} [${e.code}] ${e.message}`);
  }
  if (result.errors.length > shown.length) {
    lines.push(`      ${colors.red}✗${colors.reset} ... and ${result.errors.length - shown.length} more error(s)`);
  }
  if (verbose) {
    for (const w of result.warnings.slice(0, maxDetails)) {
      lines.push(`      ${colors.yellow}⚠${colors.reset} [${w.code}] ${w.message}`);
    }
  }
  return lines.join('\n');
}

/** Short one-line failure summary, for embedding in other scripts' output. */
function summarizeErrors(result, limit = 3) {
  if (result.ok) return '';
  const parts = result.errors.slice(0, limit).map(e => e.message);
  if (result.errors.length > limit) parts.push(`(+${result.errors.length - limit} more)`);
  return parts.join(' ');
}

// ============================================================================
// CLI
// ============================================================================

function parseArgs(argv) {
  const opts = {
    all: false, json: false, quiet: false, help: false,
    type: null, modulesDir: DEFAULT_MODULES_DIR, mainDb: DEFAULT_MAIN_DB,
    targets: [],
  };
  for (const arg of argv) {
    if (arg === '--all') opts.all = true;
    else if (arg === '--json') opts.json = true;
    else if (arg === '--quiet') opts.quiet = true;
    else if (arg === '--help' || arg === '-h') opts.help = true;
    else if (arg.startsWith('--type=')) opts.type = arg.slice(7);
    else if (arg.startsWith('--modules-dir=')) opts.modulesDir = arg.slice(14);
    else if (arg.startsWith('--main-db=')) opts.mainDb = arg.slice(10);
    else if (arg.startsWith('--')) throw new Error(`Unknown option: ${arg}`);
    else opts.targets.push(arg);
  }
  return opts;
}

function printHelp() {
  console.log(`
validate-module.js — conformance checker for Bible app module databases

Usage:
  node scripts/modules/validate-module.js <module> [options]
  node scripts/modules/validate-module.js --all [options]

Options:
  --all                Validate every .db in the modules directory
  --type=<type>        With --all: bible | commentary | dictionary | lexicon | book | devotional | topical | xref | tag_graph
  --json               Machine-readable JSON output
  --quiet              Print failures only
  --modules-dir=PATH   Override modules directory (default: packages/desktop/data/modules)
  --main-db=PATH       Override main.db used as the canon source

Exit codes: 0 = all passed, 1 = one or more failed, 2 = usage/setup error
`.trim());
}

async function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (e) {
    console.error(e.message);
    printHelp();
    process.exit(2);
  }

  if (opts.help || (!opts.all && opts.targets.length === 0)) {
    printHelp();
    process.exit(opts.help ? 0 : 2);
  }

  let paths = [];
  try {
    if (opts.all) {
      if (!fs.existsSync(opts.modulesDir)) {
        console.error(`Modules directory not found: ${opts.modulesDir}`);
        process.exit(2);
      }
      paths = fs.readdirSync(opts.modulesDir)
        .filter(f => f.endsWith('.db'))
        .filter(f => !opts.type || typeFromFilename(f) === opts.type)
        .sort()
        .map(f => path.join(opts.modulesDir, f));
    } else {
      paths = opts.targets.map(t => resolveModule(t, opts.modulesDir));
    }
  } catch (e) {
    console.error(e.message);
    process.exit(2);
  }

  if (paths.length === 0) {
    console.error('No modules matched.');
    process.exit(2);
  }

  let canon;
  try {
    canon = await loadCanon(opts.mainDb);
  } catch (e) {
    console.error(e.message);
    process.exit(2);
  }

  const results = [];
  for (const p of paths) {
    results.push(await validateModule(p, { canon }));
  }

  const failed = results.filter(r => !r.ok);

  if (opts.json) {
    console.log(JSON.stringify({
      canon_source: canon.source,
      total: results.length,
      passed: results.length - failed.length,
      failed: failed.length,
      results,
    }, null, 2));
  } else {
    console.log(`${colors.cyan}Canon source:${colors.reset} ${canon.source}`);
    console.log(`${colors.cyan}Validating ${results.length} module(s)${colors.reset}\n`);
    for (const r of results) {
      if (opts.quiet && r.ok) continue;
      console.log(formatResult(r, { verbose: !opts.quiet }));
    }
    console.log('');
    if (failed.length === 0) {
      console.log(`${colors.green}All ${results.length} module(s) passed.${colors.reset}`);
    } else {
      console.log(`${colors.red}${failed.length} of ${results.length} module(s) FAILED:${colors.reset} ${failed.map(r => r.file).join(', ')}`);
    }
  }

  process.exit(failed.length === 0 ? 0 : 1);
}

module.exports = {
  validateModule,
  loadCanon,
  resolveModule,
  formatResult,
  summarizeErrors,
  detectModuleType,
  typeFromFilename,
  VERSIFICATION_PATH,
};

if (require.main === module) {
  main().catch(e => {
    console.error('Fatal error:', e && e.stack ? e.stack : e);
    process.exit(2);
  });
}
