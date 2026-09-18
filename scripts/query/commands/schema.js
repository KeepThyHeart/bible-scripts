const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');

module.exports = {
  name: 'schema',
  usage: 'schema <module>',
  description: 'Show all tables and their columns for a database',
  args: 1,
  async run(args) {
    const dbPath = resolveModule(args[0]);
    return withDb(dbPath, async (db) => {
      const tables = await dbAll(db, "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name");
      const result = {};
      for (const t of tables) {
        const cols = await dbAll(db, `PRAGMA table_info('${t.name}')`);
        result[t.name] = {
          columns: cols.map(c => ({
            name: c.name,
            type: c.type,
            notnull: c.notnull === 1,
            pk: c.pk === 1,
            default: c.dflt_value,
          })),
          sql: t.sql,
        };
      }
      return result;
    });
  },
};
