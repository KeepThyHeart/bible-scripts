'use strict';

/**
 * The canonical content digest (design §2.7): a codec-invariant, reproducible
 * SHA-256 over a module's actual content, keyed by `module_info.content_sha256`.
 *
 * This is a from-spec reimplementation, not an import: the design's §2.5
 * table names `packages/core/src/Data/Format/ModuleFormat.ts` (CONTENT_MAP)
 * and `computeContentSha256` in the Bible repo (F1/F3/F9) as the source of
 * truth. bible-scripts cannot import that code (JS in a different repo,
 * scope: this task must not touch the `bible` repo — see task 0035), so this
 * file mirrors the spec's own formula instead. Keep it in step with §2.7 if
 * the design changes; CONTENT_MAP below covers only the five converter types
 * this repo builds (bible, commentary, dictionary, book, devotional) — not
 * topical_index/cross_reference/tag_graph, which this task doesn't convert
 * (0035 spec, "Out of scope").
 *
 * digest = SHA-256 over, for each ContentShape in CONTENT_MAP[type] (declared
 * order), for each row ordered by its rowid column ascending:
 *   u64le(rowid) || for each column in (prose ∪ indexed), declared order:
 *     0x00 if NULL, else 0x01 || u64le(byteLength) || utf8(DECODED value)
 *   then 0x1E as a table separator.
 *
 * `indexed` is a superset of `prose` for every type in the design's table
 * (§2.5), so "prose ∪ indexed, declared order" is exactly `indexed`'s own
 * order — that's what this file iterates.
 */

const crypto = require('crypto');
const codec = require('./codec');

/**
 * @typedef {object} ContentShape
 * @property {string} table
 * @property {string} rowid
 * @property {readonly string[]} prose   compressed when module_info.compression != 'none'
 * @property {readonly string[]} indexed superset of `prose` in every type below
 */

/** @type {Record<string, readonly ContentShape[]>} */
const CONTENT_MAP = {
  bible: [{ table: 'bible_verse', rowid: 'verse_id', prose: [], indexed: ['text'] }],
  commentary: [{ table: 'commentary_entry', rowid: 'entry_id', prose: ['content'], indexed: ['content'] }],
  dictionary: [{
    table: 'dictionary_entry', rowid: 'entry_id',
    prose: ['definition', 'usage_notes'], indexed: ['word', 'definition', 'usage_notes'],
  }],
  book: [{ table: 'book_section', rowid: 'section_id', prose: ['content'], indexed: ['title', 'content'] }],
  devotional: [{ table: 'devotional_entry', rowid: 'entry_id', prose: ['content'], indexed: ['title', 'content'] }],
};

const TABLE_SEPARATOR = Buffer.from([0x1e]);

function u64le(n) {
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64LE(BigInt(n));
  return buf;
}

/**
 * @param {object} db an sqlite3.Database opened on the module file
 * @param {string} moduleType one of CONTENT_MAP's keys
 * @param {object} [options]
 * @param {string} [options.compression] module_info.compression; read from the
 *   db if omitted.
 * @param {Buffer} [options.dictionary] compression_dictionary.dict_blob for
 *   `compression`; read from the db if omitted and compression != 'none'.
 * @returns {Promise<string>} lowercase hex SHA-256, 64 chars
 */
async function computeContentSha256(db, moduleType, options = {}) {
  const shapes = CONTENT_MAP[moduleType];
  if (!shapes) {
    throw new Error(`content-digest: no ContentShape for module type '${moduleType}' (in scope: ${Object.keys(CONTENT_MAP).join(', ')})`);
  }

  const compression = options.compression !== undefined
    ? options.compression
    : await getCompression(db);
  const dictionary = compression === 'none'
    ? null
    : (options.dictionary !== undefined ? options.dictionary : await getDictionary(db, compression));

  const hash = crypto.createHash('sha256');

  for (const shape of shapes) {
    const columns = shape.indexed; // prose ∪ indexed, declared order — see file doc comment
    // CAST(... AS BLOB): every column comes back as a Buffer of its EXACT
    // stored bytes, whatever its storage class — matching
    // content_digest.cpp's decodeColumn(), which never UTF-8-validates
    // (confirmed on Barnes.zip, task 0035: real SWORD source data, verified
    // byte-identical to libsword's own reading, is not valid UTF-8 in a few
    // spots — `Buffer.toString('utf8')`'s silent U+FFFD replacement would
    // otherwise change the hash without the file being wrong). A prose
    // column's typeof() tells apart a 'blob' cell (one codec frame — needs
    // decompressing) from a 'text' one (the per-row keep-only-if-smaller
    // rule, §3.2, can leave it uncompressed even in a compressed module).
    const parts = [`"${shape.rowid}"`];
    for (const col of columns) {
      parts.push(`CAST("${col}" AS BLOB) AS "${col}"`);
      if (shape.prose.includes(col)) parts.push(`typeof("${col}") AS "${col}__type"`);
    }
    const rows = await dbAll(db, `SELECT ${parts.join(', ')} FROM "${shape.table}" ORDER BY "${shape.rowid}" ASC`);

    for (const row of rows) {
      hash.update(u64le(row[shape.rowid]));
      for (const col of columns) {
        const raw = row[col];
        if (raw === null || raw === undefined) {
          hash.update(Buffer.from([0x00]));
          continue;
        }
        const isProse = shape.prose.includes(col);
        const isCompressedCell = isProse && compression !== 'none' && row[`${col}__type`] === 'blob';
        const bytes = isCompressedCell ? await decodeProseBytes(compression, raw, dictionary) : raw;
        hash.update(Buffer.from([0x01]));
        hash.update(u64le(bytes.length));
        hash.update(bytes);
      }
    }

    hash.update(TABLE_SEPARATOR);
  }

  return hash.digest('hex');
}

/** Byte-exact decompress for one prose cell — see the doc comment above. */
async function decodeProseBytes(compression, frame, dict) {
  if (compression === 'deflate') return codec.deflateDecodeBytes(frame, dict);
  if (compression === 'zstd') return codec.zstdDecodeBytes(frame);
  throw new Error(`content-digest: unknown compression '${compression}'`);
}

async function getCompression(db) {
  const row = await dbGet(db, `SELECT compression FROM module_info WHERE info_id = 1`);
  return (row && row.compression) || 'none';
}

async function getDictionary(db, compression) {
  const row = await dbGet(
    db,
    `SELECT dict_blob FROM compression_dictionary WHERE codec = ?`,
    [compression]
  );
  if (!row) {
    throw new Error(
      `content-digest: module_info.compression = '${compression}' but compression_dictionary has no row for it`
    );
  }
  return row.dict_blob;
}

function dbAll(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.all(sql, params, (err, rows) => (err ? reject(err) : resolve(rows || [])));
  });
}

function dbGet(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.get(sql, params, (err, row) => (err ? reject(err) : resolve(row || null)));
  });
}

module.exports = {
  CONTENT_MAP,
  computeContentSha256,
};
