const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { formatVerseId } = require('../lib/verse');

module.exports = {
  name: 'verse-lookup',
  usage: 'verse-lookup <bible-module> <verse-id> [verse-id2 ...]',
  description: 'Look up verse text by numeric verse_id(s). Useful for resolving IDs from other queries.',
  args: 2, // minimum 2, but accepts more
  async run(args) {
    const dbPath = resolveModule(args[0], 'bible_');
    const ids = args.slice(1).map(id => parseInt(id, 10)).filter(id => !isNaN(id));
    if (ids.length === 0) throw new Error('Provide at least one numeric verse_id');
    return withDb(dbPath, async (db) => {
      const placeholders = ids.map(() => '?').join(',');
      const rows = await dbAll(db,
        `SELECT verse_id, text FROM bible_verse WHERE verse_id IN (${placeholders}) ORDER BY verse_id`,
        ids
      );
      return rows.map(r => ({
        verse_id: r.verse_id,
        ref: formatVerseId(r.verse_id),
        text: r.text,
      }));
    });
  },
};
