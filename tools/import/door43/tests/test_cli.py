import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import door43  # noqa: E402
from door43lib import check_module, fetch, schema  # noqa: E402

MANIFEST = ("dublin_core:\n  title: Test\n  creator: C\n  publisher: P\n  issued: '2024-01-01'\n  version: '1'\n"
            "  language:\n    direction: ltr\n    identifier: xx\n")
LICENSE = 'Copyright © 2024 by Example\n'
GOD = "# God\n\nAbout God. See [sacrifice](../other/sacrifice.md) and [bad](../kt/nope.md).\n"
SACR = "# sacrifice\n\nAn offering. See [[rc://*/tw/dict/bible/kt/god]].\n"
TN = ('Reference\tID\tTags\tSupportReference\tQuote\tOccurrence\tNote\n'
      '1:1\ta1\t\t\t\t0\tA note, see [God](../../../kt/god.md).\n'
      '1:2\ta2\t\t\t\t0\tSecond note.\n')


def make_zip(path, members):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('repo/manifest.yaml', MANIFEST)
        z.writestr('repo/LICENSE.md', LICENSE)
        for k, v in members.items():
            z.writestr('repo/' + k, v)


@unittest.skipUnless(schema.schema_available('dictionary') and schema.schema_available('commentary'),
                     'app schema not available')
class CliSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.work = os.path.join(t, 'work')
        make_zip(os.path.join(t, 'tw.zip'), {'bible/kt/god.md': GOD, 'bible/other/sacrifice.md': SACR})
        make_zip(os.path.join(t, 'tn.zip'), {'tn_3JN.tsv': TN})
        self.sources = os.path.join(t, 'sources.json')
        rows = [dict(id='tw-xx', kind='tw', owner='o', repo='xx_tw', tag='v1', lang='xx', door43_lang='xx', sha256=None),
                dict(id='tn-xx', kind='tn', owner='o', repo='xx_tn', tag='v1', lang='xx', door43_lang='xx', sha256=None)]
        with open(self.sources, 'w') as f:
            json.dump({'sources': rows}, f)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = door43.main(['--sources', self.sources] + list(argv))
        return rc, buf.getvalue()

    def test_fetch_build_report(self):
        t = self.tmp.name
        rc, out = self.run_cli('fetch', 'all', '--work-dir', self.work,
                               '--local-zip', 'tw-xx=' + os.path.join(t, 'tw.zip'),
                               '--local-zip', 'tn-xx=' + os.path.join(t, 'tn.zip'))
        self.assertEqual(rc, 0, out)
        pinned = fetch.load_sources(self.sources)
        self.assertTrue(all(s['sha256'] for s in pinned))
        rc, out = self.run_cli('build', 'xx', '--work-dir', self.work)
        self.assertIn('TW-XX', out)
        tw_db = os.path.join(self.work, 'out', 'xx', 'dictionary_TW-XX.db')
        tn_db = os.path.join(self.work, 'out', 'xx', 'commentary_TN-XX.db')
        self.assertTrue(os.path.exists(tw_db) and os.path.exists(tn_db), out)
        with open(os.path.join(self.work, 'reports', 'TW-XX.json')) as f:
            rep = json.load(f)
        self.assertEqual(rep['entries'], 2)
        self.assertIn('check_problems', rep)
        # tN got the registry: its tW link resolved to a dictionary-link anchor
        content = sqlite3.connect(tn_db).execute('SELECT content FROM commentary_entry').fetchall()
        self.assertTrue(any('dictionary-link' in c[0] for c in content), content)
        self.assertEqual(check_module.check_module(tw_db), rep['check_problems'])
        self.assertEqual(rep['validator']['status'] in ('ok', 'failed', 'not run'), True)
        rc, out = self.run_cli('report', '--work-dir', self.work)
        self.assertEqual(rc, 0)
        with open(os.path.join(self.work, 'reports', 'SUMMARY.md')) as f:
            summary = f.read()
        self.assertIn('TW-XX', summary)
        self.assertIn('TN-XX', summary)
        self.assertIn('## Link resolution', summary)


class CheckModuleTest(unittest.TestCase):
    def test_flags_problems(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'x.db')
            c = sqlite3.connect(p)
            c.executescript("""
                CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, module_uuid TEXT, module_type TEXT,
                  abbreviation TEXT, full_name TEXT, language_code TEXT, license_spdx TEXT, content_version TEXT,
                  source_url TEXT);
                CREATE TABLE dictionary_entry (entry_id INTEGER PRIMARY KEY, entry_key TEXT, word TEXT, definition TEXT);
                CREATE TABLE verse_link (source_type TEXT, source_id INT, verse_id_start INT, verse_id_end INT);
                INSERT INTO module_info VALUES (1,'u','dictionary','A','A','xx','CC','1','http://x');
                INSERT INTO dictionary_entry VALUES (1,'kt/a','a','<a href="#entry-kt/zzz" class="dictionary-link" data-module="u">x</a>');
                INSERT INTO dictionary_entry VALUES (2,'kt/b','b','  ');
                INSERT INTO verse_link VALUES ('dictionary_entry',1,64001001,64001099);
            """)
            c.commit()
            c.close()
            probs = '\n'.join(check_module.check_module(p))
            self.assertIn('dangling', probs)
            self.assertIn('empty entry', probs)
            self.assertIn('outside KJV', probs)


if __name__ == '__main__':
    unittest.main()
