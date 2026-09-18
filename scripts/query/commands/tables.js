const { withDb, dbAll, dbGet } = require('../lib/db');
const { resolveModule } = require('../lib/modules');

module.exports = {
  name: 'tables',
  usage: 'tables <module>',
  description: 'List table names and row counts for a database',
  args: 1,
  async run(args) {
    const dbPath = resolveModule(args[0]);
    return withDb(dbPath, async (db) => {
      const tables = await dbAll(db, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name");
      const results = [];
      for (const t of tables) {
        const row = await dbGet(db, `SELECT COUNT(*) as count FROM "${t.name}"`);
        results.push({ table: t.name, rows: row.count });
      }
      return results;
    });
  },
};
