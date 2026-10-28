#!/usr/bin/env python3
"""Self-test for nave-to-topical.py: python3 -I test_nave_to_topical.py (no SWORD tools or real module needed)."""
import json, os, sqlite3, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'nave-to-topical.py')

IMP = """$$$JESUS, THE CHRIST
<entryFree n="JESUS, THE CHRIST"> <def> <lb/>→ HISTORY OF <list> <item>Genealogy of <ref osisRef="Matt.1.1-Matt.1.17">Mt 1:1-17</ref></item> <item>Facts before the birth of</item> <item>The angel appears <ref osisRef="Luke.1.26-Luke.1.38">Lu 1:26-38</ref></item> <item>Mary visits <ref osisRef="Luke.1.39-Luke.1.56">Lu 1:39-56</ref></item> <item>Magnificat <ref osisRef="Luke.1.46-Luke.1.55">Lu 1:46-55</ref></item> <item>Joseph <ref osisRef="Matt.1.18-Matt.1.25">Mt 1:18-25</ref></item> <item>Birth of <ref osisRef="Luke.2.1-Luke.2.7">Lu 2:1-7</ref></item> </list> <lb/>.Appears to the disciples <ref osisRef="Luke.24.36">Lu 24:36</ref> <lb/>→ ATTRIBUTES OF <lb/>→ (See each one in its alphabetical order, below) <lb/>→ BIRTH OF <list> <item>See JESUS, INCARNATION OF</item> </list> </def> </entryFree>
$$$JERICHO
<entryFree n="JERICHO"> <def> <lb/>1. A city <ref osisRef="Num.22.1">Nu 22:1</ref> <list> <item>Besieged <ref osisRef="Josh.6">Jos 6</ref></item> </list> <lb/>2. Plain of <ref osisRef="Jude.1.1">Jude 1</ref>:3,20-23 </def> </entryFree>
$$$ABARIM
<entryFree n="ABARIM"> <def> <lb/>→ See <ref target="Nave:NEBO">NEBO</ref></def> </entryFree>
"""


def make_template(path):
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, full_name TEXT, description TEXT, topic_count INTEGER,
      verse_count INTEGER, content_sha256 TEXT, metadata TEXT);
    INSERT INTO module_info VALUES (1, 'Naveߴs Topical Bible', 'x', 0, 0, NULL, NULL);
    CREATE TABLE topic (topic_id INTEGER PRIMARY KEY AUTOINCREMENT, parent_topic_id INTEGER, name TEXT NOT NULL,
      description TEXT, sort_order INTEGER NOT NULL DEFAULT 0, metadata TEXT);
    CREATE VIRTUAL TABLE topic_fts USING fts5(name, content='topic', content_rowid='topic_id');
    CREATE TRIGGER topic_fts_insert AFTER INSERT ON topic BEGIN INSERT INTO topic_fts(rowid, name) VALUES (new.topic_id, new.name); END;
    CREATE TABLE schema_version (version_id INTEGER PRIMARY KEY AUTOINCREMENT, version_number TEXT, applied_date TEXT, notes TEXT, metadata TEXT);
    CREATE TABLE verse_link (link_id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL, source_id INTEGER NOT NULL,
      verse_id_start INTEGER NOT NULL, verse_id_end INTEGER NOT NULL, link_type TEXT NOT NULL DEFAULT 'reference',
      sort_order INTEGER NOT NULL DEFAULT 0, context TEXT, metadata TEXT);
    INSERT INTO topic(name) VALUES ('Jesus, The Christ');
    """)
    c.commit(); c.close()


class NaveBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        imp = os.path.join(cls.tmp, 'n.imp'); tpl = os.path.join(cls.tmp, 't.db'); out = os.path.join(cls.tmp, 'o.db')
        with open(imp, 'w', encoding='utf-8') as f:
            f.write(IMP)
        make_template(tpl)
        subprocess.run([sys.executable, '-I', SCRIPT, '--imp', imp, '--template', tpl, '--output', out], check=True,
                       stdout=subprocess.DEVNULL)
        cls.db = sqlite3.connect(out)

    def q(self, sql, *a):
        return self.db.execute(sql, a).fetchall()

    def kids(self, name):
        return [r[0] for r in self.q("SELECT c.name FROM topic c JOIN topic p ON c.parent_topic_id=p.topic_id WHERE p.name=? ORDER BY c.sort_order", name)]

    def links(self, name):
        return self.q("SELECT verse_id_start, verse_id_end FROM verse_link WHERE source_id=(SELECT topic_id FROM topic WHERE name=?) ORDER BY sort_order", name)

    def test_refless_item_adopts_following_items(self):
        self.assertEqual(self.kids('Facts before the birth of'), ['The angel appears', 'Mary visits', 'Magnificat', 'Joseph'])  # GROUP_LIMITS caps it at 4
        self.assertIn('Birth of', self.kids('HISTORY OF'))

    def test_dot_marker_is_one_level_deeper_and_removed(self):
        self.assertEqual(self.q("SELECT count(*) FROM topic WHERE name LIKE '.%'")[0][0], 0)
        self.assertIn('Appears to the disciples', self.kids('HISTORY OF'))

    def test_chapter_only_reference_covers_chapter(self):
        self.assertEqual(self.links('Besieged'), [(6006001, 6006027)])

    def test_single_chapter_book_verse_from_text(self):
        self.assertEqual(self.links('Plain of'), [(65001003, 65001003), (65001020, 65001023)])

    def test_see_becomes_description(self):
        self.assertEqual(self.q("SELECT description FROM topic WHERE name='BIRTH OF'")[0][0], 'See JESUS, INCARNATION OF')
        self.assertTrue(self.q("SELECT description FROM topic WHERE name='ATTRIBUTES OF'")[0][0].startswith('(See each one'))
        self.assertEqual(self.q("SELECT description FROM topic WHERE name='Abarim'")[0][0], 'See NEBO')

    def test_apostrophe_repaired_and_counts(self):
        self.assertEqual(self.q('SELECT full_name FROM module_info')[0][0], 'Nave’s Topical Bible')
        self.assertEqual(self.q('SELECT topic_count FROM module_info')[0][0], self.q('SELECT count(*) FROM topic')[0][0])


if __name__ == '__main__':
    unittest.main()
