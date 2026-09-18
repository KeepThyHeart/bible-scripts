const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');

module.exports = {
  name: 'sections',
  usage: 'sections <module> [--parent=<id>]',
  description: 'List book sections (table of contents). Optionally filter by parent section.',
  args: 1,
  async run(args, opts) {
    const dbPath = resolveModule(args[0], 'book_');
    return withDb(dbPath, async (db) => {
      const parentId = opts.parent ? parseInt(opts.parent, 10) : null;
      if (parentId !== null) {
        return await dbAll(db,
          `SELECT section_id, parent_section_id, section_number, title, word_count
           FROM book_section WHERE parent_section_id = ? ORDER BY section_number`,
          [parentId]
        );
      }
      return await dbAll(db,
        `SELECT section_id, parent_section_id, section_number, title, word_count
         FROM book_section WHERE parent_section_id IS NULL ORDER BY section_number`
      );
    });
  },
};
