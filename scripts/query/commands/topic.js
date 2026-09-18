const { withDb, dbAll, dbGet } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { formatVerseRange } = require('../lib/verse');
const { topicVerseCountSql, TOPIC_VERSES_SQL, resolveTopic } = require('../lib/topical');

module.exports = {
  name: 'topic',
  usage: 'topic <module> <name-or-id>',
  description: 'Get a topic by name or ID, including children and verse references',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0], 'topical_');
    const identifier = args[1];
    return withDb(dbPath, async (db) => {
      const topic = await resolveTopic(db, identifier);
      if (!topic) return { error: `Topic not found: "${identifier}"` };

      // Parse metadata
      if (topic.metadata) try { topic.metadata = JSON.parse(topic.metadata); } catch (_e) { /* keep */ }

      // Get parent chain (breadcrumb)
      const breadcrumb = [];
      let currentId = topic.parent_topic_id;
      while (currentId) {
        const parent = await dbGet(db,
          'SELECT topic_id, name, parent_topic_id FROM topic WHERE topic_id = ?', [currentId]);
        if (!parent) break;
        breadcrumb.unshift({ topic_id: parent.topic_id, name: parent.name });
        currentId = parent.parent_topic_id;
      }

      // Get children
      const children = await dbAll(db,
        `SELECT t.topic_id, t.name, t.sort_order,
                (SELECT COUNT(*) FROM topic c WHERE c.parent_topic_id = t.topic_id) as child_count,
                ${topicVerseCountSql('t.topic_id')} as verse_count
         FROM topic t
         WHERE t.parent_topic_id = ?
         ORDER BY t.sort_order, t.name`,
        [topic.topic_id]
      );

      const verses = await dbAll(db, TOPIC_VERSES_SQL, [topic.topic_id]);

      return {
        topic_id: topic.topic_id,
        name: topic.name,
        description: topic.description,
        metadata: topic.metadata,
        breadcrumb,
        children,
        verses: verses.map(v => ({
          verse_id_start: v.verse_id_start,
          verse_id_end: v.verse_id_end,
          ref: formatVerseRange(v.verse_id_start, v.verse_id_end),
          context: v.context,
        })),
      };
    });
  },
};
