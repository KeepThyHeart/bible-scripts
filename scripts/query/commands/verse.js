const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { parseReference, formatVerseId } = require('../lib/verse');

module.exports = {
  name: 'verse',
  usage: 'verse <module> <reference>',
  description: 'Get verse(s) by reference (e.g., "John 3:16", "Gen 1:1-5", "Ps 23")',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0], 'bible_');
    const ref = parseReference(args[1]);
    return withDb(dbPath, async (db) => {
      const rows = await dbAll(db,
        'SELECT verse_id, text FROM bible_verse WHERE verse_id BETWEEN ? AND ? ORDER BY verse_id',
        [ref.start, ref.end]
      );
      return rows.map(r => ({
        verse_id: r.verse_id,
        ref: formatVerseId(r.verse_id),
        text: r.text,
      }));
    });
  },
};
