"""Structural check of a built module (design section 8).

``check_module(db_path)`` returns a list of problem strings (empty = fine):
a single ``module_info`` row with its identity columns set, a non-empty content
table, verse ids inside KJV, no dangling ``dictionary-link`` / ``book-link``
anchors that point at a missing entry of the same DB, and no empty entry text.

``run_node_validator(db_path)`` runs ``scripts/modules/validate-module.js`` only
when node can load ``sqlite3``; otherwise it reports ``validator not run``.
"""
import os
import re
import shutil
import sqlite3
import subprocess

from . import versification

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..'))
VALIDATOR = os.path.join(REPO_ROOT, 'scripts', 'modules', 'validate-module.js')
NODE_PATH_CANDIDATES = (
    os.path.join(REPO_ROOT, 'node_modules'),
    '/home/psran/Git/KeepThyHeart/bible/node_modules',
)
IDENTITY_COLUMNS = ('module_uuid', 'module_type', 'abbreviation', 'full_name', 'language_code',
                    'license_spdx', 'content_version', 'source_url')

# module type -> (content table, text table, text column, entry id column)
CONTENT = {
    'bible': ('bible_verse', 'bible_verse', 'text', 'verse_id'),
    'commentary': ('commentary_entry', 'commentary_entry', 'content', 'rowid'),
    'dictionary': ('dictionary_entry', 'dictionary_entry', 'definition', 'rowid'),
    'book': ('book_section', 'book_section', 'content', 'rowid'),
    'quiz': ('quiz_question', 'quiz_question', 'prompt', 'rowid'),
}
_ANCHOR_RE = re.compile(r'<a\b([^>]*)>', re.I)
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')
_kjv = None


def _kjv_counts():
    global _kjv
    if _kjv is None:
        _kjv = versification.load_kjv_counts()
    return _kjv


def _vid_ok(vid):
    """Inside KJV; chapter 0 / verse 0 stand for book and chapter introductions."""
    if vid is None:
        return False
    b, c, v = vid // 1000000, (vid // 1000) % 1000, vid % 1000
    counts = _kjv_counts().get(b)
    if not counts:
        return False
    if c == 0:
        return v == 0
    if c > len(counts):
        return False
    return v <= counts[c - 1]


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def detect_type(tables):
    for t, (table, _, _, _) in CONTENT.items():
        if table in tables:
            return t
    return None


def _anchors(html):
    for m in _ANCHOR_RE.finditer(html or ''):
        yield dict(_ATTR_RE.findall(m.group(1)))


def check_module(db_path):
    problems = []
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % os.path.abspath(db_path), uri=True)
    except sqlite3.Error as e:
        return ['cannot open database: %s' % e]
    try:
        tables = _tables(conn)
        if 'module_info' not in tables:
            return ['no module_info table']
        n = conn.execute('SELECT COUNT(*) FROM module_info').fetchone()[0]
        info = None
        if n != 1:
            problems.append('module_info has %d rows (need exactly 1)' % n)
        if n:
            cur = conn.execute('SELECT * FROM module_info ORDER BY info_id LIMIT 1')
            cols = [d[0] for d in cur.description]
            info = dict(zip(cols, cur.fetchone()))
            for c in IDENTITY_COLUMNS:
                if c in cols and info.get(c) in (None, ''):
                    problems.append('module_info.%s is empty' % c)
        mtype = detect_type(tables)
        if not mtype:
            problems.append('no recognised content table')
            return problems
        if info and info.get('module_type') and info['module_type'] != mtype:
            problems.append('module_info.module_type %r does not match content tables (%s)' %
                            (info['module_type'], mtype))
        table, _, col, idcol = CONTENT[mtype]
        count = conn.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
        if count == 0:
            problems.append('content table %s is empty' % table)
        if mtype != 'bible':
            empty = conn.execute("SELECT COUNT(*) FROM %s WHERE %s IS NULL OR TRIM(%s) = ''" %
                                 (table, col, col)).fetchone()[0]
            if empty:
                problems.append('%d empty entry text row(s) in %s.%s' % (empty, table, col))
        _check_verse_ids(conn, tables, mtype, problems)
        _check_anchors(conn, mtype, info or {}, problems)
    except sqlite3.Error as e:
        problems.append('sqlite error: %s' % e)
    finally:
        conn.close()
    return problems


def _check_verse_ids(conn, tables, mtype, problems):
    bad = []
    if mtype == 'bible':
        bad += [r[0] for r in conn.execute('SELECT verse_id FROM bible_verse') if not _vid_ok(r[0])]
    if mtype == 'commentary':
        for a, b in conn.execute('SELECT verse_id_start, verse_id_end FROM commentary_entry'):
            bad += [x for x in (a, b) if x is not None and not _vid_ok(x)]
    if 'verse_link' in tables:
        for a, b in conn.execute('SELECT verse_id_start, verse_id_end FROM verse_link'):
            bad += [x for x in (a, b) if not _vid_ok(x)]
    if bad:
        problems.append('%d verse id(s) outside KJV, e.g. %s' % (len(bad), sorted(set(bad))[:5]))


def _check_anchors(conn, mtype, info, problems):
    if mtype not in ('dictionary', 'commentary', 'book', 'quiz'):
        return
    table, _, col, _ = CONTENT[mtype]
    own = info.get('module_uuid')
    keys = {r[0] for r in conn.execute('SELECT entry_key FROM dictionary_entry')} if mtype == 'dictionary' else set()
    sections = ({str(r[0]) for r in conn.execute('SELECT section_number FROM book_section')}
                if mtype == 'book' else set())
    dangling = []
    for (html,) in conn.execute('SELECT %s FROM %s' % (col, table)):
        for a in _anchors(html):
            cls, href, mod = a.get('class', ''), a.get('href', ''), a.get('data-module')
            if mod and own and mod != own:
                continue          # points into another module; cannot be checked here
            if 'dictionary-link' in cls and href.startswith('#entry-'):
                if mtype == 'dictionary' and href[len('#entry-'):] not in keys:
                    dangling.append(href)
            elif 'book-link' in cls and href.startswith('#section-'):
                if mtype == 'book' and href[len('#section-'):] not in sections:
                    dangling.append(href)
            elif 'dictionary-link' in cls or 'book-link' in cls:
                dangling.append(href or '(no href)')
    if dangling:
        problems.append('%d dangling dictionary-link/book-link anchor(s), e.g. %s' % (len(dangling),
                                                                                  sorted(set(dangling))[:5]))


def node_env():
    """``(node, env)`` when node can load sqlite3 (trying the known node_modules), else ``(None, reason)``."""
    node = shutil.which('node')
    if not node:
        return None, 'node not found'
    for nm in NODE_PATH_CANDIDATES:
        if os.path.isdir(os.path.join(nm, 'sqlite3')):
            env = dict(os.environ, NODE_PATH=nm)
            try:
                r = subprocess.run([node, '-e', "require('sqlite3')"], env=env, capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as e:
                return None, str(e)
            if r.returncode == 0:
                return node, env
    return None, 'sqlite3 is not installed for node'


def run_node_validator(db_path):
    """-> dict(status='ok'|'failed'|'not run', output=str)."""
    if not os.path.isfile(VALIDATOR):
        return {'status': 'not run', 'output': 'validator not run (validate-module.js missing)'}
    node, env = node_env()
    if not node:
        return {'status': 'not run', 'output': 'validator not run (%s)' % env}
    try:
        r = subprocess.run([node, VALIDATOR, db_path], env=env, capture_output=True, text=True, timeout=300,
                           cwd=REPO_ROOT)
    except (OSError, subprocess.SubprocessError) as e:
        return {'status': 'not run', 'output': 'validator not run (%s)' % e}
    return {'status': 'ok' if r.returncode == 0 else 'failed', 'output': (r.stdout + r.stderr)[-4000:]}
