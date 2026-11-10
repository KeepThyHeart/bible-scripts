"""Create empty module databases from the app's own schema files (design section 1).

``-- @include <path>`` lines are expanded relative to the including file, recursively,
with cycle detection, as ``loadSchemaSql`` does. The schema root is
``$BIBLE_REPO/packages/core/sql/schemas/initial`` (default: the ``bible`` checkout
next to this one).
"""
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BIBLE_REPO = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..', '..', 'bible'))

SCHEMA_FILES = {
    'bible': 'BibleTranslation.sql', 'commentary': 'Commentary.sql', 'dictionary': 'Dictionary.sql',
    'book': 'Book.sql', 'quiz': 'Quiz.sql', 'devotional': 'Devotional.sql',
    'cross_reference': 'CrossReference.sql', 'topical_index': 'TopicalIndex.sql',
    'tag_graph': 'TagGraph.sql', 'timeline': 'Timeline.sql',
}
FORMATS = {t: t.replace('_', '-') + '-module' for t in SCHEMA_FILES}
FORMATS['bible'] = 'bible-module'

_INCLUDE_RE = re.compile(r'^\s*--\s*@include\s+(\S+)\s*$')


class SchemaMissing(RuntimeError):
    pass


def bible_repo():
    return os.environ.get('BIBLE_REPO') or DEFAULT_BIBLE_REPO


def schema_dir():
    return os.path.join(bible_repo(), 'packages', 'core', 'sql', 'schemas', 'initial')


def schema_available(module_type='quiz'):
    return os.path.isfile(os.path.join(schema_dir(), SCHEMA_FILES[module_type]))


def expand_includes(path, _stack=()):
    path = os.path.normpath(path)
    if path in _stack:
        raise RuntimeError(f'include cycle: {" -> ".join(_stack + (path,))}')
    if not os.path.isfile(path):
        raise SchemaMissing(f'schema file not found: {path}')
    out = []
    with open(path, encoding='utf-8') as f:
        for line in f.read().split('\n'):
            m = _INCLUDE_RE.match(line)
            if m:
                out.append(expand_includes(os.path.join(os.path.dirname(path), m.group(1)), _stack + (path,)))
            else:
                out.append(line)
    return '\n'.join(out)


def schema_sql(module_type):
    if module_type not in SCHEMA_FILES:
        raise ValueError(f'unknown module type: {module_type!r}')
    return expand_includes(os.path.join(schema_dir(), SCHEMA_FILES[module_type]))


def create_db(path, module_type, overwrite=False):
    """Create an empty database of ``module_type`` at ``path`` (or ':memory:')."""
    sql = schema_sql(module_type)
    if path != ':memory:':
        if os.path.exists(path):
            if not overwrite:
                raise FileExistsError(path)
            os.remove(path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(sql)
    return conn


MODULE_INFO_COLUMNS = (
    'module_uuid', 'module_type', 'abbreviation', 'full_name', 'format', 'format_version', 'content_version',
    'content_sha256', 'compression', 'author', 'publisher', 'year_published', 'description', 'language_code',
    'is_original_language', 'right_to_left', 'copyright', 'license_spdx', 'license_url', 'source_url',
    'versification', 'metadata',
)
_REQUIRED = ('module_uuid', 'module_type', 'abbreviation', 'full_name', 'format', 'language_code')


def insert_module_info(conn, module_type, **fields):
    """Insert the singleton module_info row. ``format`` defaults from the module type,
    ``module_uuid`` from identity.module_uuid, and a dict ``metadata`` is stored as JSON.
    Columns left out take the schema defaults (format_version, compression, versification...)."""
    from . import identity
    fields = {k: v for k, v in fields.items() if v is not None or k in ('description',)}
    unknown = set(fields) - set(MODULE_INFO_COLUMNS)
    if unknown:
        raise ValueError(f'unknown module_info column(s): {sorted(unknown)}')
    fields['module_type'] = module_type
    fields.setdefault('format', FORMATS[module_type])
    if 'module_uuid' not in fields and 'abbreviation' in fields and 'language_code' in fields:
        fields['module_uuid'] = identity.module_uuid(module_type, fields['abbreviation'], fields['language_code'])
    missing = [c for c in _REQUIRED if not fields.get(c)]
    if missing:
        raise ValueError(f'module_info missing required: {missing}')
    if isinstance(fields.get('metadata'), (dict, list)):
        fields['metadata'] = json.dumps(fields['metadata'], ensure_ascii=False, sort_keys=True)
    cols = ', '.join(['info_id'] + list(fields))
    marks = ', '.join('?' * (len(fields) + 1))
    conn.execute(f'INSERT INTO module_info ({cols}) VALUES ({marks})', [1] + list(fields.values()))


def insert_data_source(conn, id, name, licence, url=None, attribution=None, notes=None):
    """Row for the shared data_source table (Quiz, TagGraph, Timeline)."""
    conn.execute('INSERT INTO data_source (id, name, licence, url, attribution, notes) VALUES (?,?,?,?,?,?)',
                 (id, name, licence, url, attribution, notes))


def insert_feature(conn, name, metadata=None, enabled=1):
    conn.execute('INSERT INTO module_feature (feature_name, is_enabled, metadata) VALUES (?,?,?)',
                 (name, enabled, json.dumps(metadata, ensure_ascii=False) if metadata is not None else None))
