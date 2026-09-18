const path = require('path');
const fs = require('fs');
const { MODULES_DIR } = require('../../lib/paths');

module.exports = {
  name: 'modules',
  usage: 'modules [--type=<type>]',
  description: 'List all module database files in the modules directory',
  args: 0,
  async run(_args, opts) {
    const files = fs.readdirSync(MODULES_DIR)
      .filter(f => f.endsWith('.db') && !f.endsWith('-shm') && !f.endsWith('-wal'))
      .sort();
    const typeFilter = opts.type;

    const results = [];
    for (const file of files) {
      const dbPath = path.join(MODULES_DIR, file);
      const stats = fs.statSync(dbPath);
      const sizeMB = (stats.size / (1024 * 1024)).toFixed(1);

      // Infer type from filename prefix
      const prefix = file.split('_')[0];
      const type = prefix === 'xref' ? 'cross_reference' : prefix;

      if (typeFilter && type !== typeFilter) continue;

      results.push({
        file,
        type,
        size_mb: parseFloat(sizeMB),
      });
    }
    return results;
  },
};
