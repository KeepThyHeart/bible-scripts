#!/usr/bin/env python3
"""Apply curated identity, licence and attribution fields to a converted Bible module.

sword2bible copies what the SWORD .conf says. For some modules the .conf is
wrong or incomplete for our purposes: a trademark that must be dropped from the
name (Biblica CC BY-SA texts), a licence that covers only part of the module
(CUV text is public domain, its Strong's tags are GNU FDL), a mis-stated
licence, or an abbreviation shared by two modules. Those corrections live in
../data/module-overrides.json, keyed by the SWORD module name, and this script
applies them to a finished .db.

Usage:
    apply-module-overrides.py MODULE.db [--module NAME] [--overrides FILE]

NAME defaults to metadata.sword_module of the database. A module without an
entry is left untouched (exit status 0), so the script is safe to run on every
conversion, and it never touches content (`content_sha256` is unchanged).

Entry fields:
    columns     module_info columns to set: abbreviation, full_name, author,
                publisher, year_published, description, copyright,
                license_spdx, license_url, source_url
    metadata    keys merged into the module_info.metadata JSON object
                (e.g. attribution, notices, modifications)
    forbid_text strings that must not appear in any module_info text field or
                in bible_verse.text after the overrides (a trademark guard);
                the script exits 1 if one does

When `abbreviation` changes, `module_uuid` is recomputed with the converter's
own rule (sha256 of "urn:bible-module:v0.1:bible:<abbreviation>:<language>"),
so two modules that shared a SWORD abbreviation get distinct identities.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys

DEFAULT_OVERRIDES = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'module-overrides.json')

COLUMNS = ('abbreviation', 'full_name', 'author', 'publisher', 'year_published', 'description',
           'copyright', 'license_spdx', 'license_url', 'source_url')
TEXT_FIELDS = ('abbreviation', 'full_name', 'author', 'publisher', 'description', 'copyright',
               'license_spdx', 'license_url', 'source_url', 'metadata')


def deterministic_uuid(identity_key):
    """Port of SwordCommon::deterministicUuid (RFC 9562 v8, hash based)."""
    digest = hashlib.sha256(('urn:bible-module:v0.1:' + identity_key).encode('utf-8')).hexdigest()
    u = list(digest[:32])
    u[12] = '8'
    u[16] = '89ab'[int(u[16], 16) & 3]
    u = ''.join(u)
    return f'{u[0:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:32]}'


# (table, text column, label) checked by forbid_text, per module type
CONTENT_TEXT = [
    ('bible_verse', 'text', 'verse'),
    ('commentary_entry', 'content', 'commentary entry'),
    ('dictionary_entry', 'definition', 'dictionary entry'),
    ('book_section', 'content', 'book section'),
    ('quiz_question', 'prompt', 'quiz prompt'),
]


def apply_overrides(db_path, entry, module_name=None):
    """Apply one override entry to the module at db_path. Returns the list of problems (empty = ok)."""
    unknown = set(entry.get('columns', {})) - set(COLUMNS)
    if unknown:
        raise ValueError(f'unknown module_info column(s): {sorted(unknown)}')

    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute('SELECT * FROM module_info WHERE info_id = 1').fetchone()
        if row is None:
            raise ValueError('module_info has no row')

        cols = dict(entry.get('columns', {}))
        if cols:
            sets = ', '.join(f'{c} = ?' for c in cols)
            con.execute(f'UPDATE module_info SET {sets} WHERE info_id = 1', list(cols.values()))

        if 'abbreviation' in cols and cols['abbreviation'] != row['abbreviation']:
            new_uuid = deterministic_uuid(f"{(row['module_type'] if 'module_type' in row.keys() else None) or 'bible'}:{cols['abbreviation'].lower()}:{row['language_code'].lower()}")
            con.execute('UPDATE module_info SET module_uuid = ? WHERE info_id = 1', (new_uuid,))

        extra = entry.get('metadata')
        if extra:
            meta = json.loads(row['metadata'] or '{}')
            meta.update(extra)
            con.execute('UPDATE module_info SET metadata = ? WHERE info_id = 1',
                        (json.dumps(meta, ensure_ascii=False, separators=(',', ':')),))
        con.commit()

        problems = []
        forbid = entry.get('forbid_text', [])
        if forbid:
            info = con.execute('SELECT * FROM module_info WHERE info_id = 1').fetchone()
            for field in TEXT_FIELDS:
                value = info[field] or ''
                for bad in forbid:
                    if bad in value:
                        problems.append(f'module_info.{field} contains {bad!r}')
            for table, column, label in CONTENT_TEXT:
                if not con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
                    continue
                for bad in forbid:
                    n = con.execute(f'SELECT COUNT(*) FROM {table} WHERE instr({column}, ?) > 0', (bad,)).fetchone()[0]
                    if n:
                        problems.append(f'{n} {label}(s) contain {bad!r}')
        return problems
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('db')
    ap.add_argument('--module', help='SWORD module name (default: metadata.sword_module)')
    ap.add_argument('--overrides', default=DEFAULT_OVERRIDES)
    args = ap.parse_args(argv)

    module = args.module
    if not module:
        con = sqlite3.connect(args.db)
        try:
            meta = json.loads(con.execute('SELECT metadata FROM module_info WHERE info_id = 1').fetchone()[0] or '{}')
        finally:
            con.close()
        module = meta.get('sword_module', '')

    with open(args.overrides, encoding='utf-8') as fh:
        table = json.load(fh).get('modules', {})
    entry = table.get(module)
    if entry is None:
        print(f'{module}: no override entry; left as converted')
        return 0

    problems = apply_overrides(args.db, entry, module)
    if problems:
        for p in problems:
            print(f'{module}: FORBIDDEN TEXT: {p}', file=sys.stderr)
        return 1
    print(f'{module}: overrides applied')
    return 0


if __name__ == '__main__':
    sys.exit(main())
