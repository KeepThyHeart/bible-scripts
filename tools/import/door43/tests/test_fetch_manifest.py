import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile

from door43lib import fetch, manifest_lite

MANIFEST = """\
dublin_core:
  conformsto: rc0.2
  creator: Example Group
  description: >-
    A synthetic folded
    description.
  identifier: tw
  issued: '2024-01-18'
  language:
    direction: rtl
    identifier: ar
  relation:
    - ar/tn
    - 'hbo/uhb?v=2'
  title: "A \\"quoted\\" title"
  version: '5'
projects:

  - title: Genesis
    identifier: gen
    sort: 1
    categories:
      - bible-ot
  - title: Exodus   # comment
    identifier: exo
    sort: 2
    categories: [bible-ot, extra]
flag: true
none_value:
"""


def make_zip(path, members):
    with zipfile.ZipFile(path, 'w') as z:
        for k, v in members.items():
            z.writestr(k, v)


class ManifestTest(unittest.TestCase):
    def test_lite(self):
        m = manifest_lite.parse_lite(MANIFEST)
        dc = m['dublin_core']
        self.assertEqual(dc['description'], 'A synthetic folded description.')
        self.assertEqual(dc['language'], {'direction': 'rtl', 'identifier': 'ar'})
        self.assertEqual(dc['relation'], ['ar/tn', 'hbo/uhb?v=2'])
        self.assertEqual(dc['title'], 'A "quoted" title')
        self.assertEqual(m['projects'][1]['title'], 'Exodus')
        self.assertEqual(m['projects'][1]['categories'], ['bible-ot', 'extra'])
        self.assertEqual(m['projects'][0]['sort'], 1)
        self.assertIs(m['flag'], True)
        self.assertIsNone(m['none_value'])

    def test_both_readers_agree(self):
        lite = manifest_lite.load_manifest(MANIFEST, prefer_yaml=False)
        self.assertEqual(lite, manifest_lite.parse_lite(MANIFEST))
        if manifest_lite._yaml is None:
            self.skipTest('PyYAML not installed')
        self.assertEqual(lite, manifest_lite.load_manifest(MANIFEST))

    def test_unindented_sequence(self):
        m = manifest_lite.parse_lite('a:\n- x\n- y: 1\n  z: 2\nb: c\n')
        self.assertEqual(m, {'a': ['x', {'y': 1, 'z': 2}], 'b': 'c'})


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_root_stripped_and_read(self):
        p = os.path.join(self.dir, 'a.zip')
        make_zip(p, {'repo/manifest.yaml': MANIFEST, 'repo/bible/kt/god.md': '# God'})
        with fetch.Archive(p) as a:
            self.assertEqual(a.root, 'repo/')
            self.assertEqual(sorted(a.names()), ['bible/kt/god.md', 'manifest.yaml'])
            self.assertEqual(a.read_text('bible/kt/god.md'), '# God')
            self.assertEqual(a.manifest()['dublin_core']['identifier'], 'tw')
            self.assertEqual(a.names('bible/'), ['bible/kt/god.md'])

    def test_zip_slip_rejected(self):
        for name in ('../evil.txt', 'repo/../../evil.txt', '/abs.txt', 'C:/x.txt'):
            with self.subTest(name=name):
                p = os.path.join(self.dir, 'bad.zip')
                make_zip(p, {name: 'x', 'repo/ok.md': 'ok'})
                with self.assertRaises(fetch.UnsafeMember):
                    fetch.Archive(p)

    def test_read_rejects_traversal(self):
        p = os.path.join(self.dir, 'a.zip')
        make_zip(p, {'repo/ok.md': 'ok'})
        with fetch.Archive(p) as a:
            with self.assertRaises(fetch.UnsafeMember):
                a.read('../x')
            with self.assertRaises(fetch.UnsafeMember):
                a.read('/x')


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, 'work')
        self.sources = os.path.join(self.tmp.name, 'sources.json')
        with open(self.sources, 'w') as f:
            json.dump({'sources': [{'id': 'tw-xx', 'kind': 'tw', 'owner': 'Own', 'repo': 'xx_tw', 'tag': 'v1',
                                    'lang': 'xx', 'door43_lang': 'xx', 'sha256': None}]}, f)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('xx_tw/manifest.yaml', 'a: b\n')
        self.payload = buf.getvalue()
        self.urls = []

    def opener(self, url):
        self.urls.append(url)
        return io.BytesIO(self.payload)

    def test_download_pins_then_enforces(self):
        path, digest = fetch.fetch('tw-xx', self.work, self.sources, opener=self.opener)
        self.assertEqual(self.urls, ['https://git.door43.org/Own/xx_tw/archive/v1.zip'])
        self.assertTrue(path.endswith(os.path.join('cache', 'Own__xx_tw__v1.zip')))
        self.assertEqual(digest, hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(fetch.get_source('tw-xx', self.sources)['sha256'], digest)
        # cached: no second download
        fetch.fetch('tw-xx', self.work, self.sources, opener=self.opener)
        self.assertEqual(len(self.urls), 1)

    def test_sha_mismatch(self):
        fetch.fetch('tw-xx', self.work, self.sources, opener=self.opener)
        self.payload = self.payload + b'tampered'
        with self.assertRaises(fetch.Sha256Mismatch):
            fetch.fetch('tw-xx', self.work, self.sources, opener=self.opener, force=True)
        self.assertFalse(os.path.exists(fetch.cache_path(self.work, fetch.get_source('tw-xx', self.sources))))

    def test_open_source(self):
        arch, src = fetch.open_source('tw-xx', self.work, sources_path=self.sources, opener=self.opener)
        with arch:
            self.assertEqual(arch.manifest(), {'a': 'b'})
        self.assertEqual(src['repo'], 'xx_tw')

    def test_work_dir(self):
        self.assertEqual(fetch.work_dir('/x'), '/x')
        old = os.environ.pop('DOOR43_WORK', None)
        try:
            with self.assertRaises(RuntimeError):
                fetch.work_dir()
            os.environ['DOOR43_WORK'] = '/y'
            self.assertEqual(fetch.work_dir(), '/y')
        finally:
            os.environ.pop('DOOR43_WORK', None)
            if old is not None:
                os.environ['DOOR43_WORK'] = old


class SourcesTest(unittest.TestCase):
    def test_shipped_sources(self):
        rows = fetch.load_sources()
        ids = [r['id'] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        for r in rows:
            self.assertIn(r['kind'], ('tw', 'tn', 'tq', 'obs', 'bible'))
            self.assertTrue(r['tag'].startswith('v'))
        self.assertEqual(fetch.get_source('tn-ar')['tag'], 'v6.1')
        self.assertEqual(fetch.source_url(fetch.get_source('obs-ar')),
                         'https://git.door43.org/OBS-Aquifer/ar_obs/archive/v1.zip')


if __name__ == '__main__':
    unittest.main()
