const { withDb, dbAll } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { formatVerseRange } = require('../lib/verse');
const { TOPIC_VERSES_SQL, resolveTopic } = require('../lib/topical');

module.exports = {
  name: 'topic-verses',
  usage: 'topic-verses <module> <name-or-id> [--recursive]',
  description: 'Get all verse references for a topic (optionally including children)',
  args: 2,
  async run(args, opts) {
    const dbPath = resolveModule(args[0], 'topical_');
    const identifier = args[1];
    const recursive = opts.recursive !== undefined;
    return withDb(dbPath, async (db) => {
      const topic = await resolveTopic(db, identifier);
      if (!topic) return { error: `Topic not found: "${identifier}"` };

      if (recursive) {
        const ids = await dbAll(db,
          `WITH RECURSIVE descendants AS (
             SELECT topic_id FROM topic WHERE topic_id = ?
             UNION ALL
             SELECT t.topic_id FROM topic t
             JOIN descendants d ON t.parent_topic_id = d.topic_id
           )
           SELECT topic_id FROM descendants`,
          [topic.topic_id]
        );
        const topicIds = ids.map(r => r.topic_id);
        const ph = topicIds.map(() => '?').join(',');
        const verses = await dbAll(db,
          `SELECT DISTINCT verse_id_start, verse_id_end FROM verse_link
           WHERE source_type = 'topic' AND source_id IN (${ph}) ORDER BY verse_id_start`,
          topicIds
        );
        return verses.map(v => ({
          verse_id_start: v.verse_id_start,
          verse_id_end: v.verse_id_end,
          ref: formatVerseRange(v.verse_id_start, v.verse_id_end),
        }));
      }

      const verses = await dbAll(db, TOPIC_VERSES_SQL, [topic.topic_id]);
      return verses.map(v => ({
        verse_id_start: v.verse_id_start,
        verse_id_end: v.verse_id_end,
        ref: formatVerseRange(v.verse_id_start, v.verse_id_end),
        context: v.context,
      }));
    });
  },
};
