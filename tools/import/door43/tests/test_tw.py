import json
import os
import sqlite3
import tempfile
import unittest
import zipfile

from door43lib import fetch, links, schema, tw

MANIFEST = """dublin_core:
  title: 'Palabras de prueba'
  creator: 'Test Org'
  publisher: 'Test Pub'
  issued: '2022-05-01'
  version: '1'
  language:
    identifier: 'xx'
    direction: ltr
"""
GOD = """# Dios

## Definicion

Texto sobre Dios. Ver [sacrificio](../other/sacrifice.md) y [Dios mismo](./god.md) y
[ta](rc://*/ta/man/translate/translate-names).

## Referencias

* [3 Juan 1:15](rc://*/tn/help/3jn/01/15).
* [Exodo 12](rc://*/tn/help/exo/12).

## Datos

* Numeros de Strong: H0430, G2316, G00270, b:H3117
"""
SACRIFICE = """# sacrificio

Ofrenda. Ver [[rc://*/tw/dict/bible/kt/god]] y [roto](../kt/missing.md).

* Strong: G2316
"""
ABRAHAM = "# إبراهيم ی\n\nکتاب ھذا ۱۲\n"


def make_zip(path, articles):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xx_tw/manifest.yaml', MANIFEST)
        z.writestr('xx_tw/LICENSE.md', '# License\n\nCopyright © 2022 by Test Org\n')
        z.writestr('xx_tw/bible/config.yaml', 'ignored: true\n')
        for name, text in articles.items():
            z.writestr('xx_tw/bible/%s.md' % name, text)


@unittest.skipUnless(schema.schema_available('dictionary'), 'app schema not available')
class TwBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_build(self, lang, articles, registry=None):
        z = os.path.join(self.tmp.name, 'tw.zip')
        make_zip(z, articles)
        src = dict(owner='o', repo='%s_tw' % lang, tag='v1', lang=lang, door43_lang=lang)
        with fetch.Archive(z) as a:
            rep = tw.build(a, src, self.tmp.name, registry)
        return rep, sqlite3.connect(rep['module'])

    def test_build(self):
        rep, db = self.run_build('xx', {'kt/god': GOD, 'other/sacrifice': SACRIFICE})
        self.assertEqual(rep['entries'], 2)
        self.assertTrue(rep['module'].endswith('dictionary_TW-XX.db'))
        row = db.execute("SELECT word, related_words, metadata FROM dictionary_entry WHERE entry_key='kt/god'").fetchone()
        self.assertEqual(row[0], 'Dios')
        self.assertEqual(json.loads(row[1]), ['other/sacrifice'])
        meta = json.loads(row[2])
        self.assertEqual(meta['category'], 'kt')
        self.assertEqual(meta['strongs'], ['H0430', 'G2316', 'G0027', 'H3117'])
        idx = json.loads(db.execute("SELECT metadata FROM module_feature WHERE feature_name='strongs_index'").fetchone()[0])
        self.assertEqual(idx['G2316'], ['kt/god', 'other/sacrifice'])
        self.assertEqual(idx['H0430'], ['kt/god'])
        info = db.execute('SELECT module_type, abbreviation, dictionary_type, language_from, language_to, '
                          'content_version, metadata FROM module_info').fetchone()
        self.assertEqual(info[:6], ('dictionary', 'TW-XX', 'bible_dictionary', None, 'xx', 'v1'))
        self.assertEqual(json.loads(info[6])['content_format'], 'html')

    def test_links_and_verse_links(self):
        rep, db = self.run_build('xx', {'kt/god': GOD, 'other/sacrifice': SACRIFICE})
        defn = db.execute("SELECT definition FROM dictionary_entry WHERE entry_key='kt/god'").fetchone()[0]
        self.assertIn('class="dictionary-link"', defn)
        self.assertIn('href="#entry-other/sacrifice"', defn)
        self.assertNotIn('rc://', defn)
        self.assertNotIn('<h1', defn)
        self.assertNotIn('Dios</h', defn.split('Definicion')[0])
        vl = db.execute("SELECT verse_id_start, verse_id_end, source_type, link_type FROM verse_link "
                        "ORDER BY sort_order").fetchall()
        self.assertEqual(vl, [(64001014, 64001014, 'dictionary_entry', 'reference'),
                              (2012001, 2012051, 'dictionary_entry', 'reference')])
        sac = db.execute("SELECT definition FROM dictionary_entry WHERE entry_key='other/sacrifice'").fetchone()[0]
        self.assertIn('>Dios</a>', sac)
        self.assertIn('roto', sac)
        self.assertNotIn('missing', sac)
        self.assertEqual(rep['links']['dictionary']['unresolved'], 1)

    def test_arabic_normalised_only_for_ar(self):
        rep, db = self.run_build('ar', {'kt/abraham': ABRAHAM})
        word, defn = db.execute('SELECT word, definition FROM dictionary_entry').fetchone()
        self.assertNotIn('ی', word + defn)
        self.assertNotIn('ھ', defn)
        self.assertIn('١٢', defn)
        self.assertTrue(rep['arabic_replacements'])
        rep, db = self.run_build('xx', {'kt/abraham': ABRAHAM})
        word, defn = db.execute('SELECT word, definition FROM dictionary_entry').fetchone()
        self.assertIn('ی', word)
        self.assertEqual(rep['arabic_replacements'], {})

    def test_registry_passes_obs(self):
        reg = links.Registry(obs_stories={48}, obs_uuid='ob')
        art = {'kt/god': '# Dios\n\n[x](rc://*/tn/help/obs/48/06) [y](rc://*/tn/help/obs/49/06)\n'}
        rep, db = self.run_build('xx', art, reg)
        defn = db.execute('SELECT definition FROM dictionary_entry').fetchone()[0]
        self.assertIn('href="#section-48"', defn)
        self.assertNotIn('section-49', defn)


if __name__ == '__main__':
    unittest.main()
