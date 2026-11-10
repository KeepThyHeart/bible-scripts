import json
import os
import sqlite3
import tempfile
import unittest
import zipfile

from door43lib import fetch, schema, tq

SRC = {'owner': 'o', 'repo': 'xx_tq', 'tag': 'v2', 'lang': 'xx', 'door43_lang': 'xx'}
MANIFEST = "dublin_core:\n  title: Questions\n  creator: C\n  issued: '2024-01-01'\n  version: '2'\n" \
           "  language:\n    direction: ltr\n    identifier: xx\n"
HDR = 'Reference\tID\tTags\tQuote\tOccurrence\tQuestion\tResponse\n'
TSV = HDR + '\n'.join([
    '1:1\tr3ao\tName, Love\t\t\tWho writes?\tThe elder.',
    '1:1\tr3ao\t\t\t\tDuplicate id?\tYes.',
    '1:3-5\tbbbb\t\t\t\tMulti verse?\tLine one\\nline two.',
    '1:3,5\tcccc\t\t\t\tPieces?\tTwo pieces.',
    '1:15\tdddd\t\t\t\tMerged?\tInto 14.',
    ':\teeee\t\t\t\tBad ref?\tx',
    '1:intro\tffff\t\t\t\tIntro?\tx',
    '1:2\tgggg\t\t\t\t\tx',
    '1:2\thhhh\t\t\t\tNo answer?\t',
]) + '\n'


def make_zip(path, members):
    with zipfile.ZipFile(path, 'w') as z:
        for k, v in members.items():
            z.writestr('repo/' + k, v)


@unittest.skipUnless(schema.schema_available('quiz'), 'schema dir missing')
class TqTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def run_build(self, members, src=None):
        z = os.path.join(self.dir, 'a.zip')
        make_zip(z, dict({'manifest.yaml': MANIFEST, 'LICENSE.md': 'Copyright © 2024 by Ex\n'}, **members))
        with fetch.Archive(z) as a:
            rep = tq.build(a, src or SRC, os.path.join(self.dir, 'out'))
        return rep, sqlite3.connect(rep['path'])

    def test_tsv(self):
        rep, c = self.run_build({'tq_3JN.tsv': TSV})
        self.assertTrue(rep['path'].endswith('quiz_TQ-XX.db'))
        self.assertEqual(rep['skipped'], {'malformed_reference': 1, 'non_verse_reference': 1, 'empty_question': 1})
        q = c.execute('SELECT question_key, kind, answer_mode, prompt, answer, tags, source_id, review_status,'
                      ' sort_order FROM quiz_question ORDER BY question_id').fetchall()
        self.assertEqual([r[0] for r in q], ['3JN-r3ao', '3JN-r3ao-2', '3JN-bbbb', '3JN-cccc', '3JN-dddd', '3JN-hhhh'])
        self.assertEqual(q[0][1:3], ('comprehension', 'free_response'))
        self.assertEqual(json.loads(q[0][5]), ['name', 'love'])
        self.assertEqual(q[0][6:8], ('door43-xx_tq', 'unreviewed'))
        self.assertEqual([r[8] for r in q], [1, 2, 3, 4, 5, 6])
        self.assertEqual(q[2][4], 'Line one\nline two.')
        self.assertIsNone(q[5][4])
        self.assertEqual(q[5][2], 'reflection')

    def test_primary_passage_links(self):
        rep, c = self.run_build({'tq_3JN.tsv': TSV})
        def links(key):
            return c.execute('SELECT l.verse_id_start, l.verse_id_end, l.link_type FROM verse_link l JOIN quiz_question q'
                             ' ON q.question_id = l.source_id WHERE l.source_type = "quiz_question" AND q.question_key = ?'
                             ' ORDER BY l.sort_order', (key,)).fetchall()
        self.assertEqual(links('3JN-r3ao'), [(64001001, 64001001, 'primary_passage')])
        self.assertEqual(links('3JN-bbbb'), [(64001003, 64001005, 'primary_passage')])
        self.assertEqual(links('3JN-cccc'), [(64001003, 64001003, 'primary_passage'),
                                             (64001005, 64001005, 'primary_passage')])
        self.assertEqual(links('3JN-dddd'), [(64001014, 64001014, 'primary_passage')])

    def test_data_source(self):
        rep, c = self.run_build({'tq_3JN.tsv': TSV})
        row = c.execute('SELECT id, name, licence, url, attribution FROM data_source').fetchall()
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0][0], 'door43-xx_tq')
        self.assertEqual(row[0][2], 'CC BY-SA 4.0')
        self.assertIn('releases/tag/v2', row[0][3])
        self.assertIn('Copyright', row[0][4])
        self.assertEqual(c.execute('SELECT module_type, format FROM module_info').fetchone(), ('quiz', 'quiz-module'))

    def test_legacy_markdown(self):
        members = {
            '3jn/01/01.md': '# Who writes?\n\nThe elder.\n\n# To whom?\n\nGaius,\nthe beloved.',
            '3jn/01/06.md': '# Why?\n\nBecause (veja: x_y) love.',
        }
        rep, c = self.run_build(members, dict(SRC, lang='pt', door43_lang='pt-br'))
        self.assertEqual(rep['layout'], 'markdown')
        q = c.execute('SELECT question_key, prompt, answer FROM quiz_question ORDER BY question_id').fetchall()
        self.assertEqual(q[:2], [('3JN-01-01-1', 'Who writes?', 'The elder.'),
                                 ('3JN-01-01-2', 'To whom?', 'Gaius, the beloved.')])
        self.assertEqual(q[2][0], '3JN-01-06-1')
        self.assertNotIn('veja', q[2][2])
        l = c.execute('SELECT verse_id_start, verse_id_end FROM verse_link ORDER BY link_id').fetchall()
        self.assertEqual(l, [(64001001, 64001005), (64001001, 64001005), (64001006, 64001014)])

    def test_arabic_only_for_ar(self):
        tsv = HDR + '1:1\tqq\t\t\t\tdyی?\tok\n'
        rep, c = self.run_build({'tq_3JN.tsv': tsv}, dict(SRC, lang='ar', door43_lang='ar'))
        self.assertEqual(c.execute('SELECT prompt FROM quiz_question').fetchone()[0], 'dyي?')
        self.assertEqual(rep['persian_replacements'], {'06CC->064A': 1})


if __name__ == '__main__':
    unittest.main()
