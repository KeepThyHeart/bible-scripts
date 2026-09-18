const path = require('path');
const { withDb, dbAll, dbGet } = require('../lib/db');
const { resolveModule, detectModuleType } = require('../lib/modules');

module.exports = {
  name: 'stats',
  usage: 'stats <module>',
  description: 'Show summary statistics for a module (type-aware)',
  args: 1,
  async run(args) {
    const dbPath = resolveModule(args[0]);
    return withDb(dbPath, async (db) => {
      const type = await detectModuleType(db);
      const info = await dbGet(db, 'SELECT * FROM module_info LIMIT 1').catch(() => null);
      const result = {
        file: path.basename(dbPath),
        type,
        module_name: info?.full_name || info?.module_name || null,
        abbreviation: info?.abbreviation || null,
      };

      switch (type) {
        case 'bible': {
          const vc = await dbGet(db, 'SELECT COUNT(*) as c FROM bible_verse');
          result.verse_count = vc.c;
          const books = await dbAll(db, 'SELECT DISTINCT CAST(verse_id / 1000000 AS INTEGER) as book_num FROM bible_verse ORDER BY book_num');
          result.book_count = books.length;
          break;
        }
        case 'commentary': {
          const ec = await dbGet(db, 'SELECT COUNT(*) as c FROM commentary_entry');
          result.entry_count = ec.c;
          const levels = await dbAll(db, 'SELECT entry_level, COUNT(*) as c FROM commentary_entry GROUP BY entry_level');
          result.entries_by_level = {};
          for (const l of levels) result.entries_by_level[l.entry_level] = l.c;
          break;
        }
        case 'dictionary': {
          const dc = await dbGet(db, 'SELECT COUNT(*) as c FROM dictionary_entry');
          result.entry_count = dc.c;
          break;
        }
        case 'topical': {
          const tc = await dbGet(db, 'SELECT COUNT(*) as c FROM topic');
          const rc = await dbGet(db, 'SELECT COUNT(*) as c FROM topic WHERE parent_topic_id IS NULL');
          const vc = await dbGet(db, `SELECT COUNT(*) as c FROM verse_link WHERE source_type = 'topic'`);
          result.total_topics = tc.c;
          result.root_topics = rc.c;
          result.sub_topics = tc.c - rc.c;
          result.verse_associations = vc.c;
          break;
        }
        case 'xref': {
          const gc = await dbGet(db, 'SELECT COUNT(*) as c FROM cross_reference_group');
          result.group_count = gc.c;
          const rc = await dbGet(db, `SELECT COUNT(*) as c FROM verse_link WHERE source_type = 'cross_reference_group'`);
          result.reference_count = rc.c;
          break;
        }
        case 'book': {
          const sc = await dbGet(db, 'SELECT COUNT(*) as c FROM book_section');
          result.section_count = sc.c;
          break;
        }
        case 'devotional': {
          const dc = await dbGet(db, 'SELECT COUNT(*) as c FROM devotional_entry');
          result.entry_count = dc.c;
          break;
        }
      }
      return result;
    });
  },
};
