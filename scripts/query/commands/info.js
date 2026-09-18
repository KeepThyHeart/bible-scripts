const path = require('path');
const { withDb, dbGet } = require('../lib/db');
const { resolveModule } = require('../lib/modules');

module.exports = {
  name: 'info',
  usage: 'info <module>',
  description: 'Show module_info metadata for a database',
  args: 1,
  async run(args) {
    const dbPath = resolveModule(args[0]);
    return withDb(dbPath, async (db) => {
      const info = await dbGet(db, 'SELECT * FROM module_info LIMIT 1').catch(() => null);
      if (!info) {
        return { error: 'No module_info table found', file: dbPath };
      }
      // Parse JSON metadata if present
      if (info.metadata) {
        try { info.metadata = JSON.parse(info.metadata); } catch (_e) { /* keep as string */ }
      }
      info._file = path.basename(dbPath);
      return info;
    });
  },
};
