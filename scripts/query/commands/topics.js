const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { topicVerseCountSql, resolveTopic } = require('../lib/topical');

module.exports = {
  name: 'topics',
  usage: 'topics <module> [--parent=<name-or-id>] [--limit=N] [--offset=N]',
  description: 'List topics. Optionally filter by parent topic name or ID.',
  args: 1,
  async run(args, opts) {
    const dbPath = resolveModule(args[0], 'topical_');
    const limit = parseInt(opts.limit || '50', 10);
    const offset = parseInt(opts.offset || '0', 10);
    return withDb(dbPath, async (db) => {
      const select =
        `SELECT t.topic_id, t.name, t.sort_order,
                (SELECT COUNT(*) FROM topic c WHERE c.parent_topic_id = t.topic_id) as child_count,
                ${topicVerseCountSql('t.topic_id')} as verse_count
         FROM topic t`;

      if (opts.parent) {
        const parent = await resolveTopic(db, String(opts.parent));
        if (!parent) throw new Error(`Parent topic not found: "${opts.parent}"`);
        return await dbAll(db,
          `${select} WHERE t.parent_topic_id = ? ORDER BY t.sort_order, t.name LIMIT ? OFFSET ?`,
          [parent.topic_id, limit, offset]
        );
      }

      return await dbAll(db,
        `${select} WHERE t.parent_topic_id IS NULL ORDER BY t.sort_order, t.name LIMIT ? OFFSET ?`,
        [limit, offset]
      );
    });
  },
};
