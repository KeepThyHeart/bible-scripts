'use strict';

/**
 * Loads a module schema from the Bible repo, so converters build databases from
 * the same SQL the app uses instead of keeping their own copy of the DDL.
 *
 * Schema files pull in shared fragments with `-- @include <relative path>`;
 * those are expanded here. PRAGMA lines are dropped: a module is an immutable
 * artifact, so no WAL journal or connection-level settings belong in the file.
 */

const fs = require('fs');
const path = require('path');
const { BIBLE_REPO } = require('./paths');

const SCHEMAS_DIR = path.join(BIBLE_REPO, 'packages', 'core', 'sql', 'schemas');
const INCLUDE_RE = /^\s*--\s*@include\s+(\S+)\s*$/;
const PRAGMA_RE = /^\s*PRAGMA\s/i;

function expand(file, seen) {
  if (seen.includes(file)) throw new Error(`Circular @include of ${file}`);
  if (!fs.existsSync(file)) throw new Error(`Schema file not found: ${file} (is the Bible repo at ${BIBLE_REPO}? set BIBLE_REPO)`);
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  const out = [];
  for (const line of lines) {
    const include = INCLUDE_RE.exec(line);
    if (include) {
      out.push(expand(path.resolve(path.dirname(file), include[1]), [...seen, file]));
    } else if (!PRAGMA_RE.test(line)) {
      out.push(line);
    }
  }
  return out.join('\n');
}

/**
 * @param {string} name file name under `initial/`, e.g. 'CrossReference.sql'
 * @returns {string} the schema with all includes expanded
 */
function loadSchema(name) {
  return expand(path.join(SCHEMAS_DIR, 'initial', name), []);
}

module.exports = { loadSchema };
