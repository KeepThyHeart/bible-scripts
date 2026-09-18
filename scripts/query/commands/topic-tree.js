const { withDb, dbAll, dbGet } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { topicVerseCountSql, resolveTopic } = require('../lib/topical');

/** Indented text rendering of the `topic-tree` result. */
function renderTopicTree(tree) {
  if (tree.error) { console.log(tree.error); return; }
  const suffix = t => (t.verse_count ? `  [${t.verse_count}v]` : '');
  console.log(`${tree.name}  (#${tree.topic_id})${suffix(tree)}`);
  if (tree.description) console.log(`  ${tree.description}`);
  const walk = (nodes, indent) => {
    nodes.forEach((node, i) => {
      console.log(`${indent}${i + 1}. ${node.name}  (#${node.topic_id})${suffix(node)}`);
      walk(node.children || [], `${indent}    `);
    });
  };
  walk(tree.children || [], '  ');
}

module.exports = {
  name: 'topic-tree',
  usage: 'topic-tree <module> <name-or-id> [--depth=N]',
  description: 'Show the full hierarchy tree under a topic',
  args: 2,
  // Rendered as an indented tree for the default --format=pretty; the other
  // formats still emit the nested object. Five levels of nested JSON is not a
  // readable answer to "show me the tree".
  render: renderTopicTree,
  async run(args, opts) {
    const dbPath = resolveModule(args[0], 'topical_');
    const identifier = args[1];
    const maxDepth = parseInt(opts.depth || '3', 10);
    return withDb(dbPath, async (db) => {
      const root = await resolveTopic(db, identifier);
      if (!root) return { error: `Topic not found: "${identifier}"` };

      const countSql = topicVerseCountSql('t.topic_id');
      async function buildTree(topicId, depth) {
        if (depth >= maxDepth) return [];
        const children = await dbAll(db,
          `SELECT t.topic_id, t.name, t.sort_order, ${countSql} as verse_count
           FROM topic t WHERE t.parent_topic_id = ? ORDER BY t.sort_order, t.name`,
          [topicId]
        );
        for (const child of children) {
          child.children = await buildTree(child.topic_id, depth + 1);
        }
        return children;
      }

      const vc = await dbGet(db, `SELECT ${topicVerseCountSql('?')} as c`, [root.topic_id]);
      return {
        topic_id: root.topic_id,
        name: root.name,
        description: root.description,
        verse_count: vc.c,
        children: await buildTree(root.topic_id, 0),
      };
    });
  },
};
