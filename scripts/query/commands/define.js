const { withDb, dbAll, dbGet } = require('../lib/db');
const { resolveModule } = require('../lib/modules');

module.exports = {
  name: 'define',
  usage: 'define <module> <entry-key>',
  description: 'Look up a dictionary entry by key (e.g., "G25", "H430", "Love")',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0], 'dictionary_');
    const key = args[1];
    return withDb(dbPath, async (db) => {
      // Try exact match first, then case-insensitive
      let entry = await dbGet(db, 'SELECT * FROM dictionary_entry WHERE entry_key = ?', [key]);
      if (!entry) {
        entry = await dbGet(db, 'SELECT * FROM dictionary_entry WHERE entry_key = ? COLLATE NOCASE', [key]);
      }
      if (!entry) {
        // Try word column
        entry = await dbGet(db, 'SELECT * FROM dictionary_entry WHERE word = ? COLLATE NOCASE', [key]);
      }
      if (!entry) {
        // Fuzzy: LIKE match
        const rows = await dbAll(db,
          'SELECT * FROM dictionary_entry WHERE entry_key LIKE ? OR word LIKE ? LIMIT 5',
          [`%${key}%`, `%${key}%`]
        );
        if (rows.length === 0) return { error: `No entry found for: ${key}` };
        return rows;
      }
      // Parse JSON fields
      if (entry.related_words) try { entry.related_words = JSON.parse(entry.related_words); } catch (_e) { /* keep */ }
      if (entry.example_verses) try { entry.example_verses = JSON.parse(entry.example_verses); } catch (_e) { /* keep */ }
      if (entry.metadata) try { entry.metadata = JSON.parse(entry.metadata); } catch (_e) { /* keep */ }
      return entry;
    });
  },
};
