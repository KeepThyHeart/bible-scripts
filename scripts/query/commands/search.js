const { withDb, dbAll } = require('../lib/db');
const { resolveModule, detectModuleType } = require('../lib/modules');
const { formatVerseId } = require('../lib/verse');

/**
 * Column index of an FTS5 column, for `snippet()`/`highlight()`.
 *
 * Every FTS table in these modules starts with an UNINDEXED id column, so a
 * hardcoded `0` makes snippet() return the row id ("30304") instead of
 * matching text.
 */
async function ftsColumnIndex(db, ftsTable, ...names) {
  const cols = await dbAll(db, `PRAGMA table_info(${JSON.stringify(ftsTable)})`).catch(() => []);
  for (const name of names) {
    const idx = cols.findIndex(c => c.name === name);
    if (idx >= 0) return idx;
  }
  return 1;
}

module.exports = {
  name: 'search',
  usage: 'search <module> <query> [--limit=N]',
  description: 'Full-text search Bible verses (FTS5)',
  args: 2,
  async run(args, opts) {
    const dbPath = resolveModule(args[0]);
    const query = args[1];
    const limit = parseInt(opts.limit || '25', 10);
    return withDb(dbPath, async (db) => {
      const type = await detectModuleType(db);
      let rows;
      if (type === 'bible') {
        rows = await dbAll(db,
          `SELECT v.verse_id, v.text AS text, rank
           FROM bible_verse_fts fts
           JOIN bible_verse v ON v.verse_id = fts.rowid
           WHERE bible_verse_fts MATCH ?
           ORDER BY rank
           LIMIT ?`,
          [query, limit]
        );
        return rows.map(r => ({
          verse_id: r.verse_id,
          ref: formatVerseId(r.verse_id),
          text: r.text,
          rank: r.rank,
        }));
      } else if (type === 'commentary') {
        const col = await ftsColumnIndex(db, 'commentary_entry_fts', 'content');
        rows = await dbAll(db,
          `SELECT e.entry_id, e.verse_id_start, e.verse_id_end, e.entry_level,
                  snippet(commentary_entry_fts, ${col}, '>>>', '<<<', '...', 40) as snippet, rank
           FROM commentary_entry_fts fts
           JOIN commentary_entry e ON e.entry_id = fts.rowid
           WHERE commentary_entry_fts MATCH ?
           ORDER BY rank
           LIMIT ?`,
          [query, limit]
        );
        return rows.map(r => ({
          entry_id: r.entry_id,
          ref_start: formatVerseId(r.verse_id_start),
          ref_end: formatVerseId(r.verse_id_end),
          level: r.entry_level,
          snippet: r.snippet,
        }));
      } else if (type === 'dictionary') {
        const col = await ftsColumnIndex(db, 'dictionary_entry_fts', 'definition', 'word');
        rows = await dbAll(db,
          `SELECT e.entry_id, e.entry_key, e.word,
                  snippet(dictionary_entry_fts, ${col}, '>>>', '<<<', '...', 40) as snippet, rank
           FROM dictionary_entry_fts fts
           JOIN dictionary_entry e ON e.entry_id = fts.rowid
           WHERE dictionary_entry_fts MATCH ?
           ORDER BY rank
           LIMIT ?`,
          [query, limit]
        );
        return rows;
      } else if (type === 'topical') {
        rows = await dbAll(db,
          `SELECT t.topic_id, t.name, t.parent_topic_id,
                  (SELECT p.name FROM topic p WHERE p.topic_id = t.parent_topic_id) AS parent_name,
                  rank
           FROM topic_fts fts
           JOIN topic t ON t.topic_id = fts.rowid
           WHERE topic_fts MATCH ?
           ORDER BY rank
           LIMIT ?`,
          [query, limit]
        );
        return rows;
      } else if (type === 'book') {
        const col = await ftsColumnIndex(db, 'book_section_fts', 'content');
        rows = await dbAll(db,
          `SELECT s.section_id, s.title, s.parent_section_id,
                  snippet(book_section_fts, ${col}, '>>>', '<<<', '...', 40) as snippet, rank
           FROM book_section_fts fts
           JOIN book_section s ON s.section_id = fts.rowid
           WHERE book_section_fts MATCH ?
           ORDER BY rank
           LIMIT ?`,
          [query, limit]
        );
        return rows;
      } else if (type === 'devotional') {
        const col = await ftsColumnIndex(db, 'devotional_entry_fts', 'content');
        rows = await dbAll(db,
          `SELECT e.entry_id, e.day_number, e.date_label, e.title,
                  snippet(devotional_entry_fts, ${col}, '>>>', '<<<', '...', 40) as snippet, rank
           FROM devotional_entry_fts fts
           JOIN devotional_entry e ON e.entry_id = fts.rowid
           WHERE devotional_entry_fts MATCH ?
           ORDER BY rank
           LIMIT ?`,
          [query, limit]
        );
        return rows;
      }
      throw new Error(`Search not supported for module type: ${type}`);
    });
  },
};
