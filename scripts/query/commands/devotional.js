const { withDb, dbGet } = require('../lib/db');
const { resolveModule } = require('../lib/modules');

module.exports = {
  name: 'devotional',
  usage: 'devotional <module> <day-number-or-date-label>',
  description: 'Get a devotional entry by day number or date label (e.g., "1", "January 1")',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0], 'devotional_');
    const identifier = args[1];
    return withDb(dbPath, async (db) => {
      const dayNum = parseInt(identifier, 10);
      let entry;
      if (!isNaN(dayNum) && String(dayNum) === identifier) {
        entry = await dbGet(db, 'SELECT * FROM devotional_entry WHERE day_number = ?', [dayNum]);
      }
      if (!entry) {
        entry = await dbGet(db, 'SELECT * FROM devotional_entry WHERE date_label = ? COLLATE NOCASE', [identifier]);
      }
      if (!entry) {
        entry = await dbGet(db, 'SELECT * FROM devotional_entry WHERE date_label LIKE ? COLLATE NOCASE LIMIT 1', [`%${identifier}%`]);
      }
      if (!entry) return { error: `Devotional entry not found: "${identifier}"` };
      if (entry.metadata) try { entry.metadata = JSON.parse(entry.metadata); } catch (_e) { /* keep */ }
      return entry;
    });
  },
};
