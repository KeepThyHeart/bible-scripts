import importlib.util
import json
import os
import sqlite3
import unittest

from door43lib import identity, schema

HERE = os.path.dirname(os.path.abspath(__file__))
OVERRIDES = os.path.join(HERE, '..', '..', 'sword', 'bible', 'apply-module-overrides.py')


class IdentityTest(unittest.TestCase):
    def test_uuid_parity(self):
        spec = importlib.util.spec_from_file_location('amo', OVERRIDES)
        amo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(amo)
        for key in ('dictionary:tw-es:es', 'quiz:tq-hi:hi', 'bible:avd:ar'):
            self.assertEqual(identity.deterministic_uuid(key), amo.deterministic_uuid(key))
        self.assertEqual(identity.module_uuid('dictionary', 'TW-ES', 'es'), amo.deterministic_uuid('dictionary:tw-es:es'))

    def test_names(self):
        self.assertEqual(identity.abbreviation('tw', 'es-419'), 'TW-ES')
        self.assertEqual(identity.file_name('tn', 'ar'), 'commentary_TN-AR.db')
        self.assertEqual(identity.file_name('tq', 'hi'), 'quiz_TQ-HI.db')
        self.assertEqual(identity.file_name('obs', 'bn'), 'book_OBS-BN.db')
        self.assertEqual(identity.file_name('tw', 'es'), 'dictionary_TW-ES.db')

    def test_copyright_line(self):
        self.assertEqual(identity.copyright_line('# License\n\nCopyright © 2022 by Example Org\n\nmore'),
                         'Copyright © 2022 by Example Org')
        self.assertEqual(identity.copyright_line('**Copyright © 2020 X**'), 'Copyright © 2020 X')
        self.assertIsNone(identity.copyright_line('nothing'))

    def test_build_module_info(self):
        manifest = {'dublin_core': {'title': 'Palabras', 'creator': 'Cr', 'publisher': 'Pub', 'issued': '2022-05-01',
                                    'version': '37', 'language': {'direction': 'ltr', 'identifier': 'es-419'},
                                    'source': [{'identifier': 'tw'}]}}
        src = {'owner': 'o', 'repo': 'es-419_tw', 'tag': 'v37', 'lang': 'es', 'door43_lang': 'es-419'}
        c = identity.build_module_info(manifest, 'tw', src, 'Copyright © 2022 by Example', zip_sha256='ab')
        self.assertEqual(c['abbreviation'], 'TW-ES')
        self.assertEqual(c['module_type'], 'dictionary')
        self.assertEqual(c['language_code'], 'es')
        self.assertEqual(c['full_name'], 'Palabras (translationWords)')
        self.assertEqual(c['year_published'], 2022)
        self.assertEqual(c['right_to_left'], 0)
        self.assertEqual(c['source_url'], 'https://git.door43.org/o/es-419_tw/releases/tag/v37')
        self.assertEqual(c['metadata']['door43_language'], 'es-419')
        self.assertEqual(c['module_uuid'], identity.deterministic_uuid('dictionary:tw-es:es'))
        rtl = identity.build_module_info({'dublin_core': {'language': {'direction': 'rtl'}}}, 'tq',
                                         dict(src, lang='ar', door43_lang='ar', repo='ar_tq'))
        self.assertEqual(rtl['right_to_left'], 1)


@unittest.skipUnless(schema.schema_available('quiz'), 'bible schema dir not found (set BIBLE_REPO)')
class SchemaTest(unittest.TestCase):
    def test_includes_expanded(self):
        for t in ('commentary', 'dictionary', 'book', 'quiz'):
            with self.subTest(t=t):
                sql = schema.schema_sql(t)
                self.assertNotIn('@include', sql)
                self.assertIn('CREATE TABLE module_info', sql)

    def test_create_and_insert(self):
        for t, fmt in (('commentary', 'commentary-module'), ('dictionary', 'dictionary-module'),
                       ('book', 'book-module'), ('quiz', 'quiz-module')):
            with self.subTest(t=t):
                conn = schema.create_db(':memory:', t)
                schema.insert_module_info(conn, t, abbreviation='TX-AA', full_name='Test', language_code='aa',
                                          metadata={'k': 1}, license_spdx='CC-BY-SA-4.0')
                schema.insert_feature(conn, 'strongs_index', {'a': 1})
                row = conn.execute('SELECT module_type, format, versification, metadata, module_uuid '
                                   'FROM module_info').fetchone()
                self.assertEqual(row[:3], (t, fmt, 'kjv-english'))
                self.assertEqual(json.loads(row[3]), {'k': 1})
                self.assertEqual(row[4], identity.module_uuid(t, 'TX-AA', 'aa'))
                with self.assertRaises(sqlite3.IntegrityError):
                    schema.insert_module_info(conn, t, abbreviation='TX-AA', full_name='T', language_code='aa')
                conn.close()

    def test_data_source_quiz(self):
        conn = schema.create_db(':memory:', 'quiz')
        schema.insert_data_source(conn, 'door43-xx_tq', 'xx tQ', 'CC BY-SA 4.0')
        self.assertEqual(conn.execute('SELECT count(*) FROM data_source').fetchone()[0], 1)

    def test_file_and_missing_required(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'x', 'dictionary_TX.db')
            conn = schema.create_db(p, 'dictionary')
            with self.assertRaises(ValueError):
                schema.insert_module_info(conn, 'dictionary', abbreviation='TX')
            conn.close()
            with self.assertRaises(FileExistsError):
                schema.create_db(p, 'dictionary')
            schema.create_db(p, 'dictionary', overwrite=True).close()

    def test_unknown_type(self):
        with self.assertRaises(ValueError):
            schema.schema_sql('nonsense')


class SchemaIncludeTest(unittest.TestCase):
    def test_cycle_and_missing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, 'a.sql'), os.path.join(d, 'b.sql')
            with open(a, 'w') as f:
                f.write('-- @include b.sql\n')
            with open(b, 'w') as f:
                f.write('-- @include a.sql\n')
            with self.assertRaises(RuntimeError):
                schema.expand_includes(a)
            with open(a, 'w') as f:
                f.write('SELECT 1;\n-- @include nope.sql\n')
            with self.assertRaises(schema.SchemaMissing):
                schema.expand_includes(a)
            with open(b, 'w') as f:
                f.write('X;')
            with open(a, 'w') as f:
                f.write('A;\n-- @include b.sql\nC;')
            self.assertEqual(schema.expand_includes(a), 'A;\nX;\nC;')

    def test_env_override(self):
        old = os.environ.get('BIBLE_REPO')
        os.environ['BIBLE_REPO'] = '/nonexistent'
        try:
            self.assertFalse(schema.schema_available('quiz'))
        finally:
            if old is None:
                os.environ.pop('BIBLE_REPO')
            else:
                os.environ['BIBLE_REPO'] = old


if __name__ == '__main__':
    unittest.main()
