const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { parseReference, formatVerseId } = require('../lib/verse');

module.exports = {
  name: 'commentary',
  usage: 'commentary <module> <reference>',
  description: 'Get commentary entries for a verse or range',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0], 'commentary_');
    const ref = parseReference(args[1]);
    return withDb(dbPath, async (db) => {
      const rows = await dbAll(db,
        `SELECT entry_id, verse_id_start, verse_id_end, entry_level, content, word_count
         FROM commentary_entry
         WHERE verse_id_start <= ? AND verse_id_end >= ?
         ORDER BY verse_id_start, entry_level`,
        [ref.end, ref.start]
      );
      return rows.map(r => ({
        entry_id: r.entry_id,
        ref_start: formatVerseId(r.verse_id_start),
        ref_end: formatVerseId(r.verse_id_end),
        level: r.entry_level,
        word_count: r.word_count,
        content: r.content,
      }));
    });
  },
};
