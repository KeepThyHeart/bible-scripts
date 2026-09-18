const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { formatVerseId, findBookNumber } = require('../lib/verse');

module.exports = {
  name: 'chapters',
  usage: 'chapters <module> <book-num-or-name>',
  description: 'List chapters and verse counts for a book in a Bible module',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0], 'bible_');
    const bookArg = args[1];

    // Resolve book number
    let bookNum = parseInt(bookArg, 10);
    if (isNaN(bookNum)) {
      bookNum = findBookNumber(bookArg);
      if (!bookNum) throw new Error(`Unknown book: "${bookArg}"`);
    }

    return withDb(dbPath, async (db) => {
      const rows = await dbAll(db,
        `SELECT CAST((verse_id % 1000000) / 1000 AS INTEGER) as chapter,
                COUNT(*) as verse_count,
                MIN(verse_id) as first_verse_id,
                MAX(verse_id) as last_verse_id
         FROM bible_verse
         WHERE CAST(verse_id / 1000000 AS INTEGER) = ?
         GROUP BY chapter
         ORDER BY chapter`,
        [bookNum]
      );
      return rows.map(r => ({
        chapter: r.chapter,
        verse_count: r.verse_count,
        first: formatVerseId(r.first_verse_id),
        last: formatVerseId(r.last_verse_id),
      }));
    });
  },
};
