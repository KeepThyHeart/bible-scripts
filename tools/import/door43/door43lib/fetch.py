"""Pinned Door43 release download and safe zip access (design section 2).

``fetch`` streams ``https://git.door43.org/<owner>/<repo>/archive/<tag>.zip`` to
``<work>/cache/<owner>__<repo>__<tag>.zip``. The first fetch writes the sha256 into
sources.json; later fetches enforce it. ``Archive`` reads members straight from the
zip and rejects any member whose path contains ``..`` or starts with ``/``.
"""
import hashlib
import json
import os
import urllib.request
import zipfile

from . import manifest_lite

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(HERE, '..', 'data', 'sources.json')
BASE_URL = 'https://git.door43.org'


class UnsafeMember(ValueError):
    """Zip member path escapes the archive (zip-slip)."""


class Sha256Mismatch(RuntimeError):
    pass


def load_sources(path=None):
    with open(path or SOURCES_PATH, encoding='utf-8') as f:
        data = json.load(f)
    return data['sources'] if isinstance(data, dict) else data


def get_source(source_id, path=None):
    for s in load_sources(path):
        if s['id'] == source_id:
            return s
    raise KeyError(f'unknown source id: {source_id}')


def source_url(src):
    return f"{BASE_URL}/{src['owner']}/{src['repo']}/archive/{src['tag']}.zip"


def release_url(src):
    return f"{BASE_URL}/{src['owner']}/{src['repo']}/releases/tag/{src['tag']}"


def work_dir(arg=None):
    d = arg or os.environ.get('DOOR43_WORK')
    if not d:
        raise RuntimeError('no work dir: pass --work-dir or set DOOR43_WORK')
    return d


def cache_path(work, src):
    return os.path.join(work, 'cache', f"{src['owner']}__{src['repo']}__{src['tag']}.zip")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _pin(sources_path, source_id, digest):
    p = sources_path or SOURCES_PATH
    with open(p, encoding='utf-8') as f:
        data = json.load(f)
    rows = data['sources'] if isinstance(data, dict) else data
    for r in rows:
        if r['id'] == source_id:
            r['sha256'] = digest
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, p)


def fetch(source_id, work, sources_path=None, opener=None, force=False):
    """Download (or reuse the cached) zip and return ``(zip_path, sha256)``.

    ``opener(url)`` returns a binary file-like object (default: urllib). A pinned
    sha256 that does not match raises ``Sha256Mismatch`` and the bad file is removed.
    """
    src = get_source(source_id, sources_path)
    dest = cache_path(work, src)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if force or not os.path.exists(dest):
        part = dest + '.part'
        resp = (opener or urllib.request.urlopen)(source_url(src))
        try:
            with open(part, 'wb') as out:
                for chunk in iter(lambda: resp.read(1 << 20), b''):
                    out.write(chunk)
        finally:
            close = getattr(resp, 'close', None)
            if close:
                close()
        os.replace(part, dest)
    digest = sha256_file(dest)
    pinned = src.get('sha256')
    if pinned and pinned != digest:
        os.remove(dest)
        raise Sha256Mismatch(f'{source_id}: expected {pinned}, got {digest}')
    if not pinned:
        _pin(sources_path, source_id, digest)
    return dest, digest


def check_member_name(name):
    n = name.replace('\\', '/')
    if n.startswith('/') or (len(n) > 1 and n[1] == ':') or '..' in n.split('/'):
        raise UnsafeMember(f'unsafe zip member path: {name!r}')
    return n


class Archive:
    """Read-only view of a Door43 zip. Gitea zips hold a single top folder
    (``repo/``); ``root`` is that prefix and member names are given without it."""

    def __init__(self, path):
        self.path = path
        self._zip = zipfile.ZipFile(path)
        infos = [i for i in self._zip.infolist()]
        for i in infos:
            check_member_name(i.filename)
        files = [i.filename for i in infos if not i.filename.endswith('/')]
        tops = {f.split('/', 1)[0] for f in files if '/' in f}
        self.root = (tops.pop() + '/') if len(tops) == 1 and all('/' in f for f in files) else ''
        self._names = [f[len(self.root):] for f in files]

    def close(self):
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def names(self, prefix=''):
        return [n for n in self._names if n.startswith(prefix)]

    def exists(self, name):
        return name in self._names

    def read(self, name):
        n = check_member_name(name)
        return self._zip.read(self.root + n)

    def read_text(self, name, encoding='utf-8'):
        return self.read(name).decode(encoding)

    def manifest(self, name='manifest.yaml'):
        return manifest_lite.load_manifest(self.read_text(name))


def open_source(source_id, work, **kw):
    """Fetch if needed, then open: ``(Archive, source_row)``."""
    path, _ = fetch(source_id, work, **kw)
    return Archive(path), get_source(source_id, kw.get('sources_path'))
