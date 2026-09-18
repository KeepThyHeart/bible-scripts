const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');

module.exports = {
  name: 'sql',
  usage: 'sql <module> <query>',
  description: 'Execute a raw SQL SELECT query against a module database',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0]);
    const query = args[1];
    if (!/^\s*SELECT\b/i.test(query) && !/^\s*PRAGMA\b/i.test(query) && !/^\s*WITH\b/i.test(query)) {
      throw new Error('Only SELECT, PRAGMA, and WITH (CTE) queries are allowed. This is a read-only tool.');
    }
    return withDb(dbPath, db => dbAll(db, query));
  },
};
