/**
 * Module resolution and type detection.
 */

const path = require('path');
const fs = require('fs');
const { MODULES_DIR } = require('../../lib/paths');
const { dbAll } = require('./db');

/**
 * Resolve a module identifier to a full .db file path.
 * Accepts: full path, filename, or abbreviation/prefix.
 *
 * `preferPrefix` is the module-type prefix the calling command expects, tried
 * ahead of the generic list. Without it, bare abbreviations resolve to the
 * wrong module whenever two types share a name: `nave` and `torrey` are both a
 * dictionary and a topical index, and `tsk` is both a commentary and the
 * cross-reference set, so `xref tsk "John 3:16"` opened commentary_tsk.db and
 * reported "not a cross-reference module".
 */
function resolveModule(identifier, preferPrefix) {
  // Full path
  if (path.isAbsolute(identifier) || identifier.includes('/') || identifier.includes('\\')) {
    if (!fs.existsSync(identifier)) {
      throw new Error(`Database file not found: ${identifier}`);
    }
    return identifier;
  }

  // Add .db extension if missing
  const withExt = identifier.endsWith('.db') ? identifier : `${identifier}.db`;

  // Direct filename match in modules dir
  const directPath = path.join(MODULES_DIR, withExt);
  if (fs.existsSync(directPath)) return directPath;

  // Try prefixed matches (e.g., "kjv" → "bible_kjv.db"), the command's own
  // module type first.
  const prefixes = ['bible_', 'commentary_', 'dictionary_', 'topical_', 'book_', 'devotional_', 'xref_'];
  if (preferPrefix) prefixes.unshift(preferPrefix);
  for (const prefix of prefixes) {
    const prefixed = path.join(MODULES_DIR, `${prefix}${withExt}`);
    if (fs.existsSync(prefixed)) return prefixed;
  }

  // Fuzzy: scan all files for substring match
  if (fs.existsSync(MODULES_DIR)) {
    const files = fs.readdirSync(MODULES_DIR).filter(f => f.endsWith('.db'));
    const lower = identifier.toLowerCase();
    const match = files.find(f => f.toLowerCase().includes(lower));
    if (match) return path.join(MODULES_DIR, match);
  }

  throw new Error(`Cannot resolve module: "${identifier}". Provide a filename, abbreviation, or full path.`);
}

/**
 * Detect module type from the database file (checks tables present).
 */
async function detectModuleType(db) {
  const tables = await dbAll(db, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name");
  const tableNames = new Set(tables.map(t => t.name));

  if (tableNames.has('topic')) return 'topical';
  if (tableNames.has('bible_verse')) return 'bible';
  if (tableNames.has('commentary_entry')) return 'commentary';
  if (tableNames.has('dictionary_entry')) return 'dictionary';
  if (tableNames.has('book_section')) return 'book';
  if (tableNames.has('devotional_entry')) return 'devotional';
  if (tableNames.has('cross_reference_group')) return 'xref';
  if (tableNames.has('module_metadata')) return 'main';
  return 'unknown';
}

module.exports = { resolveModule, detectModuleType };
