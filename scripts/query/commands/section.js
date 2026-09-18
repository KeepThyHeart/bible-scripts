const { withDb, dbAll, dbGet } = require('../lib/db');
const { resolveModule } = require('../lib/modules');
const { formatVerseRange } = require('../lib/verse');

module.exports = {
  name: 'section',
  usage: 'section <module> <section-id>',
  description: 'Get a book section by ID (includes content)',
  args: 2,
  async run(args) {
    const dbPath = resolveModule(args[0], 'book_');
    const sectionId = parseInt(args[1], 10);
    return withDb(dbPath, async (db) => {
      const section = await dbGet(db, 'SELECT * FROM book_section WHERE section_id = ?', [sectionId]);
      if (!section) return { error: `Section not found: ${sectionId}` };
      if (section.metadata) try { section.metadata = JSON.parse(section.metadata); } catch (_e) { /* keep */ }
      // Scripture references for this section live in the unified `verse_link` table.
      const refs = await dbAll(db,
        `SELECT verse_id_start, verse_id_end, context FROM verse_link
         WHERE source_type = 'book_section' AND source_id = ? ORDER BY sort_order`,
        [sectionId]
      );
      section.scripture_references = refs.map(r => ({
        ref: formatVerseRange(r.verse_id_start, r.verse_id_end),
        context: r.context,
      }));
      return section;
    });
  },
};
