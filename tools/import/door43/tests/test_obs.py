import json
import os
import sqlite3
import tempfile
import unittest
import zipfile

from door43lib import books, fetch, obs, schema

MANIFEST = """dublin_core:
  title: 'Historias de prueba'
  creator: 'Test Org'
  issued: '2022-05-01'
  language:
    identifier: 'xx'
    direction: ltr
"""
STORY1 = """# 1. Creation

![OBS Image](https://cdn.example.org/obs/jpg/360px/obs-en-01-01.jpg)

First frame text with *emphasis*.

![OBS Image](https://cdn.example.org/obs/jpg/360px/obs-en-01-02.jpg)

Second frame <b>raw</b>.

_A Bible story from: Genesis 1:1-2:25_
"""
STORY2 = """# 2. Sin

![OBS Image](https://cdn.example.org/obs/jpg/360px/obs-en-02-01.jpg)

Only frame.

_ref_
"""


def make_zip(path, extra=None):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xx_obs/manifest.yaml', MANIFEST)
        z.writestr('xx_obs/LICENSE.md', 'Copyright © 2022 by Test Org\n')
        z.writestr('xx_obs/content/01.md', STORY1)
        z.writestr('xx_obs/content/02.md', STORY2)
        z.writestr('xx_obs/content/front/title.md', '# Open Bible Stories\n\nIntro text.\n')
        z.writestr('xx_obs/content/back/intro.md', '# The End\n')
        z.writestr('xx_obs/content/readme.txt', 'ignored')
        for n, t in (extra or {}).items():
            z.writestr('xx_obs/content/%s' % n, t)


class ReferencesTest(unittest.TestCase):
    def test_parse_range(self):
        self.assertEqual(obs.parse_range('GEN 1:1-3:24'), (1001001, 1003024))
        self.assertEqual(obs.parse_range('JHN 3:16'), (43003016, 43003016))
        self.assertEqual(obs.parse_range('MAT 14:13-21'), (40014013, 40014021))
        self.assertEqual(obs.parse_range('1SA 10:1-27'), (9010001, 9010027))
        with self.assertRaises(ValueError):
            obs.parse_range('Genesis one')

    def test_data_file_complete_and_inside_kjv(self):
        from door43lib import versification
        counts = versification.load_kjv_counts()
        data = obs.load_references()
        self.assertEqual(sorted(data), list(range(1, 51)))
        with open(obs.REFERENCES_PATH, encoding='utf-8') as f:
            self.assertIn('hand-compiled', json.load(f)['_note'].lower())
        for story, ranges in data.items():
            self.assertTrue(ranges, story)
            for a, b in ranges:
                self.assertLessEqual(a, b, story)
                for vid in (a, b):
                    bk, ch, v = books.split_verse_id(vid)
                    c = counts[bk]
                    self.assertTrue(1 <= ch <= len(c) and 1 <= v <= c[ch - 1], (story, vid))


class ParseStoryTest(unittest.TestCase):
    def test_parse(self):
        p = obs.parse_story(STORY1)
        self.assertEqual(p['title'], '1. Creation')
        self.assertEqual([f[0] for f in p['frames']], ['01', '02'])
        self.assertEqual(p['reference'], 'A Bible story from: Genesis 1:1-2:25')
        self.assertEqual(p['story'], 1)


@unittest.skipUnless(schema.schema_available('book'), 'app schema not available')
class ObsBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def build(self, lang='xx', extra=None):
        z = os.path.join(self.tmp.name, 'obs.zip')
        make_zip(z, extra)
        src = dict(owner='o', repo='%s_obs' % lang, tag='v1', lang=lang, door43_lang=lang)
        with fetch.Archive(z) as a:
            rep = obs.build(a, src, self.tmp.name)
        return rep, sqlite3.connect(rep['module'])

    def test_sections(self):
        rep, db = self.build()
        self.assertTrue(rep['module'].endswith('book_OBS-XX.db'))
        rows = db.execute('SELECT section_number, sort_order, title FROM book_section ORDER BY sort_order').fetchall()
        self.assertEqual(rows, [('0', 0, 'Open Bible Stories'), ('1', 1, '1. Creation'), ('2', 2, '2. Sin'),
                                ('51', 51, 'The End')])
        content, meta = db.execute("SELECT content, metadata FROM book_section WHERE sort_order=1").fetchone()
        self.assertIn('<p data-frame="01">First frame text with <em>emphasis</em>.</p>', content)
        self.assertIn('<p data-frame="02">Second frame &lt;b&gt;raw&lt;/b&gt;.</p>', content)
        self.assertIn('<p class="obs-reference"><em>A Bible story from: Genesis 1:1-2:25</em></p>', content)
        self.assertNotIn('<img', content)
        self.assertNotIn('cdn.example', content)
        self.assertEqual(json.loads(meta)['frames'], ['01', '02'])
        self.assertEqual(rep['frames'], 3)
        self.assertEqual(rep['stories'], 2)

    def test_verse_links(self):
        rep, db = self.build()
        ids = dict(db.execute('SELECT sort_order, section_id FROM book_section'))
        rows = db.execute("SELECT source_id, verse_id_start, verse_id_end, source_type, link_type FROM verse_link "
                          "WHERE source_id=? ORDER BY sort_order", (ids[1],)).fetchall()
        self.assertEqual(rows, [(ids[1], 1001001, 1002025, 'book_section', 'reference')])
        self.assertEqual(db.execute('SELECT COUNT(*) FROM verse_link WHERE source_id=?', (ids[0],)).fetchone()[0], 0)

    def test_info_and_missing(self):
        rep, db = self.build()
        self.assertEqual(len(rep['missing_stories']), 48)
        row = db.execute('SELECT module_type, abbreviation, format, language_code FROM module_info').fetchone()
        self.assertEqual(row, ('book', 'OBS-XX', 'book-module', 'xx'))

    def test_arabic_normalised_only_for_ar(self):
        extra = {'03.md': '# 3. \u06cc\n\n![x](https://c/obs-en-03-01.jpg)\n\n\u06a9\u062a\u0627\u0628 \u06f5\n'}
        rep, db = self.build('ar', extra)
        title, content = db.execute('SELECT title, content FROM book_section WHERE sort_order=3').fetchone()
        self.assertEqual(title, '3. \u064a')
        self.assertIn('\u0643\u062a\u0627\u0628 \u0665', content)
        self.assertTrue(rep['arabic_replacements'])
        rep, db = self.build('xx', extra)
        self.assertEqual(rep['arabic_replacements'], {})


if __name__ == '__main__':
    unittest.main()
