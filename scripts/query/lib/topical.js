/**
 * Topical index helpers shared by the topic commands.
 *
 * A topical module holds `topic` (a parent/child tree) and `topic_fts`; the
 * verses of a topic live in the unified `verse_link` table under
 * `source_type = 'topic'`.
 */

const { dbGet } = require('./db');

/**
 * SQL fragment counting the verse references attached to a topic.
 * Written once so every command reports the same number.
 */
function topicVerseCountSql(topicIdExpr) {
  return `(SELECT COUNT(*) FROM verse_link vl WHERE vl.source_type = 'topic' AND vl.source_id = ${topicIdExpr})`;
}

/** SELECT for one topic's verse references, in reading order. */
const TOPIC_VERSES_SQL =
  `SELECT verse_id_start, verse_id_end, context, sort_order
   FROM verse_link WHERE source_type = 'topic' AND source_id = ?
   ORDER BY sort_order, verse_id_start`;

/**
 * Find a topic by numeric id, exact name, or substring.
 *
 * Root topics win ties. Nave's has nine topics named "Manasseh" — one root
 * entry with the five numbered senses under it, plus eight same-named leaves
 * scattered under other headings — and a bare `WHERE name = ?` returned
 * whichever had the lowest rowid, so `topic-tree Manasseh` showed an empty leaf.
 */
async function resolveTopic(db, identifier) {
  const asNum = parseInt(identifier, 10);
  if (!isNaN(asNum) && String(asNum) === identifier) {
    const byId = await dbGet(db, 'SELECT * FROM topic WHERE topic_id = ?', [asNum]);
    if (byId) return byId;
  }
  const order = 'ORDER BY (parent_topic_id IS NULL) DESC, sort_order, topic_id LIMIT 1';
  const byName = await dbGet(db,
    `SELECT * FROM topic WHERE name = ? COLLATE NOCASE ${order}`, [identifier]);
  if (byName) return byName;
  return await dbGet(db,
    `SELECT * FROM topic WHERE name LIKE ? COLLATE NOCASE ${order}`, [`%${identifier}%`]);
}

module.exports = { topicVerseCountSql, TOPIC_VERSES_SQL, resolveTopic };
