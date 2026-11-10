import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

from door43lib import fetch, schema, tn

SRC = {'owner': 'o', 'repo': 'xx_tn', 'tag': 'v1', 'lang': 'xx', 'door43_lang': 'xx'}
MANIFEST = "dublin_core:\n  title: Notes\n  creator: C\n  publisher: P\n  issued: '2024-01-01'\n  version: '1'\n" \
           "  language:\n    direction: ltr\n    identifier: xx\n"
LICENSE = 'Copyright © 2024 by Example\n'

HDR7 = 'Reference\tID\tTags\tSupportReference\tQuote\tOccurrence\tNote\n'
TSV7 = HDR7 + '\n'.join([
    'front:intro\ta1\t\t\t\t0\t# Intro\\n\\nBook text.',
    '1:intro\ta2\t\t\t\t0\tChapter text.',
    '1:1\ta3\t\trc://*/ta/man/translate/figs-you\tfoo\t1\tFirst **note**.',
    '1:1\ta4\t\t\tbar\t1\tSecond note.',
    ':\ta5\t\t\t\t0\tbad ref',
    '1:2\ta6\t\t\t\t0\t',
    '1:3-5\ta7\t\t\t\t0\tPassage note.',
    '1:3,5\ta8\t\t\t\t0\tList note.',
    '1:15\ta9\t\t\t\t0\tMerged verse.',
    '1:99\tb1\t\t\t\t0\tNowhere.',
]) + '\n'

HDR9 = 'Book\tChapter\tVerse\tID\tSupportReference\tOrigQuote\tOccurrence\tGLQuote\tOccurrenceNote\n'
TSV9 = HDR9 + '\n'.join([
    '3JN\tfront\tintro\tz1\t\t\t0\t\tBook intro.',
    '3JN\t1\t4\tz2\t\tword\t1\tgl\t"Quoted ""cell"" here"',
    '3JN\t1\t4\tz3\t\t\t1\t\tAnother.',
])


def make_zip(path, members):
    with zipfile.ZipFile(path, 'w') as z:
        for k, v in members.items():
            z.writestr('repo/' + k, v)


def fake_links():
    calls = []

    def resolve_links(html, registry, book=None, chapter=None):
        calls.append((html, book, chapter))
        return SimpleNamespace(html=html.replace('First', 'Resolved'), verse_links=[(64001001, 64001001)],
                               stats={'resolved': 1})
    return SimpleNamespace(Registry=lambda **kw: object(), resolve_links=resolve_links), calls


@unittest.skipUnless(schema.schema_available('commentary'), 'schema dir missing')
class TnTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def run_build(self, members, src=None, links=None):
        z = os.path.join(self.dir, 'a.zip')
        make_zip(z, dict({'manifest.yaml': MANIFEST, 'LICENSE.md': LICENSE}, **members))
        lk, self.calls = links or fake_links()
        with fetch.Archive(z) as a, mock.patch.object(tn, '_load_links', return_value=lk):
            rep = tn.build(a, src or SRC, os.path.join(self.dir, 'out'))
        return rep, sqlite3.connect(rep['path'])

    def test_tsv7(self):
        rep, c = self.run_build({'tn_3JN.tsv': TSV7})
        self.assertTrue(rep['path'].endswith('commentary_TN-XX.db'))
        self.assertEqual(rep['skipped'], {'malformed_reference': 1, 'empty_note': 1, 'unmapped_verse': 1})
        rows = c.execute('SELECT entry_level, verse_id_start, verse_id_end, content, metadata FROM commentary_entry'
                         ' ORDER BY entry_id').fetchall()
        self.assertEqual([r[0] for r in rows], ['book', 'chapter', 'verse', 'passage', 'passage', 'verse'])
        self.assertEqual(rows[0][1:3], (None, None))
        self.assertEqual(rows[2][1:3], (64001001, 64001001))
        self.assertEqual(rows[3][1:3], (64001003, 64001005))
        self.assertEqual(rows[4][1:3], (64001003, 64001005))
        # 1:15 merges into 1:14
        self.assertEqual(rows[5][1:3], (64001014, 64001014))
        # two rows, one reference -> one entry with quote headings and two notes
        self.assertIn('<h4><span lang="grc">foo</span></h4>', rows[2][3])
        self.assertIn('<h4><span lang="grc">bar</span></h4>', rows[2][3])
        self.assertIn('<strong>note</strong>', rows[2][3].replace('Resolved', 'First'))
        meta = json.loads(rows[2][4])
        self.assertEqual([n['id'] for n in meta['notes']], ['a3', 'a4'])
        self.assertEqual(meta['notes'][0]['support_reference'], 'rc://*/ta/man/translate/figs-you')
        self.assertEqual(json.loads(rows[0][4])['book'], '3JN')
        self.assertEqual(c.execute('SELECT count(*) FROM module_info').fetchone()[0], 1)
        self.assertEqual(rep['entries'], 6)

    def test_scope_and_piece_links(self):
        rep, c = self.run_build({'tn_3JN.tsv': TSV7})
        links = c.execute('SELECT e.entry_level, l.verse_id_start, l.verse_id_end, l.link_type FROM verse_link l'
                          ' JOIN commentary_entry e ON e.entry_id = l.source_id AND l.source_type = "commentary_entry"'
                          ' ORDER BY l.link_id').fetchall()
        self.assertIn(('book', 64001001, 64001014, 'scope'), links)
        self.assertIn(('chapter', 64001001, 64001014, 'scope'), links)
        pieces = [l for l in links if l[0] == 'passage' and l[3] == 'reference' and l[1] != l[2]]
        self.assertEqual(pieces, [])
        singles = [(l[1], l[2]) for l in links if l[0] == 'passage' and l[3] == 'reference']
        self.assertIn((64001003, 64001003), singles)
        self.assertIn((64001005, 64001005), singles)
        # resolver links are stored too
        self.assertIn(('verse', 64001001, 64001001, 'reference'), links)
        self.assertEqual(rep['links'].get('resolved'), rep['links']['resolved'])
        self.assertEqual(self.calls[0][1], 64)  # book passed to the resolver

    def test_tsv9_and_quoted_cells(self):
        rep, c = self.run_build({'tn_3JN.tsv': TSV9})
        self.assertEqual(rep['layout'], 'tsv9')
        lv = c.execute('SELECT entry_level, content FROM commentary_entry ORDER BY entry_id').fetchall()
        self.assertEqual([r[0] for r in lv], ['book', 'verse'])
        self.assertIn('Quoted "cell" here', lv[1][1].replace('&quot;', '"'))
        self.assertIn('<span lang="grc">word</span>', lv[1][1])
        self.assertIn('Another.', lv[1][1])

    def test_unquote_and_read_tsv(self):
        self.assertEqual(tn.unquote_cell('"a ""b"" c"'), 'a "b" c')
        self.assertEqual(tn.unquote_cell('plain"'), 'plain"')
        h, rows = tn.read_tsv('A\tB\n"x ""y"""\tz\textra\n\nonly\n')
        self.assertEqual(rows, [['x "y"', 'z\textra'], ['only', '']])

    def test_legacy_markdown_chunks(self):
        members = {
            '3jn/front/intro.md': '# Introduction\n\nAbout.',
            '3jn/01/intro.md': '# Chapter one\n\nText.',
            '3jn/01/01.md': '# elder\n\nThe elder (veja: figs_metonymy) wrote.\n\n# beloved\n\nDear one.',
            '3jn/01/05.md': '# faithful\n\nNote on 5.',
            '3jn/01/12.md': '# witness\n\nLast chunk.',
        }
        src = dict(SRC, lang='pt', door43_lang='pt-br')
        rep, c = self.run_build(members, src)
        self.assertEqual(rep['layout'], 'markdown')
        rows = c.execute('SELECT entry_level, verse_id_start, verse_id_end, content FROM commentary_entry'
                         ' ORDER BY entry_id').fetchall()
        self.assertEqual([r[:3] for r in rows], [
            ('book', None, None), ('chapter', None, None),
            ('passage', 64001001, 64001004), ('passage', 64001005, 64001011), ('passage', 64001012, 64001014)])
        self.assertIn('<h4>elder</h4>', rows[2][3])
        self.assertIn('<h4>beloved</h4>', rows[2][3])
        self.assertNotIn('veja', rows[2][3])
        self.assertTrue(rep['path'].endswith('commentary_TN-PT.db'))

    def test_arabic_normalisation_only_for_ar(self):
        tsv = HDR7 + '1:1\ta\t\t\t\t0\tdyی ۱\n'
        rep, c = self.run_build({'tn_3JN.tsv': tsv}, dict(SRC, lang='ar', door43_lang='ar'))
        text = c.execute('SELECT content FROM commentary_entry').fetchone()[0]
        self.assertNotIn('ی', text)
        self.assertIn('١', text)
        self.assertEqual(rep['persian_replacements'], {'06CC->064A': 1, '06F0-9->0660-9': 1})
        meta = json.loads(c.execute('SELECT metadata FROM module_info').fetchone()[0])
        self.assertIn('06CC->064A', meta['modifications'])
        rep2, c2 = self.run_build({'tn_3JN.tsv': tsv})
        self.assertIn('ی', c2.execute('SELECT content FROM commentary_entry').fetchone()[0])
        self.assertEqual(rep2['persian_replacements'], {})

    def test_without_links_module(self):
        z = os.path.join(self.dir, 'b.zip')
        make_zip(z, {'tn_3JN.tsv': HDR7 + '1:1\ta\t\t\t\t0\tHi\n'})
        with fetch.Archive(z) as a, mock.patch.object(tn, '_load_links', return_value=None):
            rep = tn.build(a, SRC, os.path.join(self.dir, 'o2'))
        self.assertEqual(rep['entries'], 1)
        self.assertEqual(rep['links'], {'links_unavailable': 1})


if __name__ == '__main__':
    unittest.main()
