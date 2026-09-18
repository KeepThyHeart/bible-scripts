const { withDb, dbAll } = require('../lib/db');
const { resolveModule, detectModuleType } = require('../lib/modules');
const { parseReference, formatVerseRange } = require('../lib/verse');

module.exports = {
  name: 'xref',
  usage: 'xref <module> <reference> [--limit=N]',
  description: 'Get cross-references for a verse',
  args: 2,
  async run(args, opts) {
    const dbPath = resolveModule(args[0], 'xref_');
    const ref = parseReference(args[1]);
    const limit = parseInt(opts.limit || '50', 10);
    return withDb(dbPath, async (db) => {
      if (await detectModuleType(db) !== 'xref') {
        throw new Error(`${args[0]} has no cross_reference_group table — not a cross-reference module.`);
      }

      // A source passage is a `cross_reference_group` row with an inclusive
      // range, and its targets live in the generic `verse_link` table.
      // Overlap, not containment: the reference may itself be a range.
      const rows = await dbAll(db,
        `SELECT g.verse_id_start AS from_start, g.verse_id_end AS from_end, g.phrase,
                vl.verse_id_start AS to_start, vl.verse_id_end AS to_end, vl.context
         FROM cross_reference_group g
         JOIN verse_link vl
           ON vl.source_id = g.group_id AND vl.source_type = 'cross_reference_group'
         WHERE g.verse_id_start <= ? AND g.verse_id_end >= ?
         ORDER BY g.verse_id_start, g.sort_order, vl.sort_order
         LIMIT ?`,
        [ref.end, ref.start, limit]
      );

      return rows.map(r => ({
        from: formatVerseRange(r.from_start, r.from_end),
        phrase: r.phrase || undefined,
        to: formatVerseRange(r.to_start, r.to_end),
        note: r.context || undefined,
      }));
    });
  },
};
