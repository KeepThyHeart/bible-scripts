#!/usr/bin/env python3
"""Tests for apply-module-overrides.py. Run: python3 -m unittest test_apply_module_overrides -v"""
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('overrides', os.path.join(HERE, 'apply-module-overrides.py'))
overrides = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overrides)


def make_db(path, abbreviation='OCCB', language='zh', text='plain'):
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.executescript('''
        CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, module_uuid TEXT, abbreviation TEXT, full_name TEXT,
            author TEXT, publisher TEXT, year_published INTEGER, description TEXT, language_code TEXT,
            copyright TEXT, license_spdx TEXT, license_url TEXT, source_url TEXT, metadata TEXT);
        CREATE TABLE bible_verse (verse_id INTEGER PRIMARY KEY, text TEXT);
    ''')
    con.execute('INSERT INTO module_info (info_id, module_uuid, abbreviation, full_name, language_code, metadata) '
                'VALUES (1, ?, ?, ?, ?, ?)',
                (overrides.deterministic_uuid(f'bible:{abbreviation.lower()}:{language}'), abbreviation,
                 'Biblica® Open Contemporary', language, json.dumps({'sword_module': 'x', 'keep': 1})))
    con.execute('INSERT INTO bible_verse VALUES (1001001, ?)', (text,))
    con.commit()
    con.close()


class ApplyOverridesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, 'm.db')

    def tearDown(self):
        self.dir.cleanup()

    def row(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return con.execute('SELECT * FROM module_info').fetchone()
        finally:
            con.close()

    def test_uuid_port_matches_converter(self):
        # Value produced by sword2bible for abbreviation "RV1909", language "es".
        self.assertEqual(overrides.deterministic_uuid('bible:rv1909:es'), '3ff7a528-3459-8c62-ad8e-c16043c46652')

    def test_columns_metadata_and_uuid(self):
        make_db(self.db)
        old_uuid = self.row()['module_uuid']
        problems = overrides.apply_overrides(self.db, {
            'columns': {'abbreviation': 'OCCB-T', 'full_name': 'Open Contemporary', 'license_spdx': 'CC-BY-SA-4.0'},
            'metadata': {'attribution': 'by Biblica, Inc.'}})
        self.assertEqual(problems, [])
        row = self.row()
        self.assertEqual(row['full_name'], 'Open Contemporary')
        self.assertNotEqual(row['module_uuid'], old_uuid)
        self.assertEqual(row['module_uuid'], overrides.deterministic_uuid('bible:occb-t:zh'))
        meta = json.loads(row['metadata'])
        self.assertEqual(meta['keep'], 1)
        self.assertEqual(meta['attribution'], 'by Biblica, Inc.')

    def test_uuid_kept_when_abbreviation_unchanged(self):
        make_db(self.db)
        old_uuid = self.row()['module_uuid']
        overrides.apply_overrides(self.db, {'columns': {'abbreviation': 'OCCB', 'copyright': 'c'}})
        self.assertEqual(self.row()['module_uuid'], old_uuid)

    def test_forbidden_text_is_reported(self):
        make_db(self.db, text='Biblica® in a verse')
        problems = overrides.apply_overrides(self.db, {'columns': {'full_name': 'Clean'}, 'forbid_text': ['®']})
        self.assertEqual(len(problems), 1)
        self.assertIn('verse', problems[0])
        # The module_info side was cleaned, so only the verse is reported.
        make_db(self.db, text='clean')
        self.assertEqual(overrides.apply_overrides(self.db, {'columns': {'full_name': 'Biblica®'}, 'forbid_text': ['®']})[0][:20],
                         'module_info.full_nam')

    def test_unknown_column_rejected(self):
        make_db(self.db)
        with self.assertRaises(ValueError):
            overrides.apply_overrides(self.db, {'columns': {'module_type': 'x'}})

    def test_main_without_entry_leaves_module_alone(self):
        make_db(self.db)
        table = os.path.join(self.dir.name, 'o.json')
        with open(table, 'w') as fh:
            json.dump({'modules': {}}, fh)
        self.assertEqual(overrides.main([self.db, '--module', 'nothing', '--overrides', table]), 0)
        self.assertEqual(self.row()['abbreviation'], 'OCCB')

    def test_shipped_table_is_well_formed(self):
        with open(overrides.DEFAULT_OVERRIDES, encoding='utf-8') as fh:
            table = json.load(fh)['modules']
        seen = {}
        for name, entry in table.items():
            self.assertFalse(set(entry.get('columns', {})) - set(overrides.COLUMNS), name)
            abbr = entry['columns'].get('abbreviation')
            if abbr:
                self.assertNotIn(abbr, seen, f'{name} reuses abbreviation of {seen.get(abbr)}')
                seen[abbr] = name
            if entry['columns'].get('license_spdx', '').startswith('CC-BY-SA'):
                self.assertTrue(entry['metadata'].get('attribution'), name)


if __name__ == '__main__':
    unittest.main()
