const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { parseReference } = require('../lib/verse');

module.exports = {
  name: 'verse-topics',
  usage: 'verse-topics <topical-module> <reference>',
  description: 'Find all topics associated with a verse reference',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0], 'topical_');
    const ref = parseReference(args[1]);
    return withDb(dbPath, async (db) => {
      // Overlap, not containment: the reference may itself be a range.
      return await dbAll(db,
        `SELECT DISTINCT t.topic_id, t.name, t.parent_topic_id, p.name as parent_name
         FROM verse_link vl
         JOIN topic t ON t.topic_id = vl.source_id AND vl.source_type = 'topic'
         LEFT JOIN topic p ON t.parent_topic_id = p.topic_id
         WHERE vl.verse_id_start <= ? AND COALESCE(vl.verse_id_end, vl.verse_id_start) >= ?
         ORDER BY t.name`,
        [ref.end, ref.start]
      );
    });
  },
};
