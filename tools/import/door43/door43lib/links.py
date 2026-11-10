"""Link resolution for Door43 helps (design section 4).

``resolve_links(html, registry, book, chapter)`` rewrites the ``<a>`` anchors that
``markdown_html`` emits (hrefs verbatim) plus bare ``[[...]]`` and bare ``rc://`` text:

* scripture rc / relative tN links -> ``<a href="#verse-ID" class="scripture-link">``
  plus a ``verse_link`` range (ufw -> KJV through ``registry.versification``)
* tW links -> ``<a href="#entry-kt/god" class="dictionary-link" data-module=UUID>``
* OBS links -> ``<a href="#section-N" class="book-link" data-module=UUID>``
* translationAcademy links -> label kept (anchor) or removed (bare), an enclosing
  ``(See: )`` left empty is removed
* ``http(s)://`` kept; anything unresolvable becomes plain label text and is counted.

Stats: ``{kind: {'resolved': n, 'unresolved': n, 'stripped': n, 'samples': [..<=20]}}``
with kinds ``scripture``, ``dictionary``, ``obs``, ``ta``, ``http``, ``other``.
"""
import html as _html
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import books, refs, versification as _vers

MAX_SAMPLES = 20
CATEGORIES = ('kt', 'names', 'other')

_KJV_COUNTS = None


def _kjv_counts():
    global _KJV_COUNTS
    if _KJV_COUNTS is None:
        try:
            _KJV_COUNTS = _vers.load_kjv_counts()
        except (OSError, ValueError, KeyError):
            _KJV_COUNTS = {}
    return _KJV_COUNTS


class Registry:
    """What exists: tW keys (``kt/god``), the tW module uuid, OBS story numbers, the OBS module
    uuid, and the versification used for ufw -> KJV. ``tw_words`` (optional ``{key: word}``)
    gives bare ``[[...]]`` links their label."""

    def __init__(self, tw_keys=(), tw_uuid=None, obs_stories=(), obs_uuid=None, versification=None,
                 tw_words=None):
        self.tw_keys = set(tw_keys or ())
        self.tw_uuid = tw_uuid
        self.obs_stories = {int(s) for s in (obs_stories or ())}
        self.obs_uuid = obs_uuid
        self.versification = versification
        self.tw_words = dict(tw_words or {})


@dataclass
class LinkResult:
    html: str
    verse_links: List[Tuple[int, int]] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def empty_stats():
    return {}


def _bucket(stats, kind):
    return stats.setdefault(kind, {'resolved': 0, 'unresolved': 0, 'stripped': 0, 'samples': []})


def _count(stats, kind, outcome, sample=None):
    b = _bucket(stats, kind)
    b[outcome] += 1
    if outcome == 'unresolved' and sample is not None and len(b['samples']) < MAX_SAMPLES:
        b['samples'].append(sample)


def merge_stats(into, other):
    """Add ``other`` stats into ``into`` (in place) and return it."""
    for kind, b in (other or {}).items():
        t = _bucket(into, kind)
        for k in ('resolved', 'unresolved', 'stripped'):
            t[k] += b.get(k, 0)
        for s in b.get('samples', []):
            if len(t['samples']) < MAX_SAMPLES:
                t['samples'].append(s)
    return into


class _Skip(Exception):
    """Internal: link not resolvable; ``reason`` explains."""

    def __init__(self, kind, reason):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


# ---------------------------------------------------------------- scripture

def _map_one(reg, book, ch, v):
    vers = reg.versification
    if vers is not None:
        try:
            ch, v = vers.map_verse(book, ch, v)
        except _vers.UnmappedVerse as e:
            raise _Skip('scripture', 'no KJV verse: %s' % e)
    counts = _kjv_counts().get(book)
    if not counts or not (1 <= ch <= len(counts)) or not (1 <= v <= counts[ch - 1]):
        raise _Skip('scripture', 'no KJV verse %s %s:%s' % (book, ch, v))
    return books.verse_id(book, ch, v)


def _scripture_range(ref, reg):
    if ref.book is None:
        raise _Skip('scripture', 'no book context')
    counts = _kjv_counts().get(ref.book)
    if not counts:
        raise _Skip('scripture', 'unknown book')
    if ref.level == 'book':
        return (books.verse_id(ref.book, 1, 1), books.verse_id(ref.book, len(counts), counts[-1]))
    if ref.level == 'chapter':
        if not (1 <= ref.ch <= len(counts)):
            raise _Skip('scripture', 'no KJV chapter %s %s' % (ref.book, ref.ch))
        return (books.verse_id(ref.book, ref.ch, 1), books.verse_id(ref.book, ref.ch, counts[ref.ch - 1]))
    a = _map_one(reg, ref.book, ref.ch, ref.v1)
    b = _map_one(reg, ref.book, ref.ch2, ref.v2)
    return (a, b) if a <= b else (b, a)


def _scripture_label(ref):
    if ref.book is None:
        return 'link'
    code = books.usfm_code(ref.book)
    if ref.level == 'book':
        return code
    if ref.level == 'chapter':
        return '%s %d' % (code, ref.ch)
    if ref.level == 'verse':
        return '%s %d:%d' % (code, ref.ch, ref.v1)
    return '%s %d:%d-%d:%d' % (code, ref.ch, ref.v1, ref.ch2, ref.v2)


# ---------------------------------------------------------------- targets

def _classify(href, reg, book, chapter):
    """Return a tuple describing the target:
    ('http', url) | ('ta',) | ('scripture', (start, end), label) | ('dict', key, label) |
    ('obs', n, label). Raises _Skip when unresolvable."""
    h = _html.unescape(href).strip()
    low = h.lower()
    if low.startswith(('http://', 'https://')):
        return ('http', h)
    if re.match(r'rc\s*:\s*/{1,2}', low):
        h = re.sub(r'^rc\s*:\s*/{1,2}\s*', 'rc://', h, flags=re.I)
        parts = [p.strip() for p in h[5:].split('/')]
        segs = [p for p in parts[1:] if p != ''] if len(parts) > 1 else []
        if not segs:
            raise _Skip('other', 'empty rc link')
        res = segs[0].lower()
        if res == 'ta':
            return ('ta',)
        if res == 'tw':
            return _dict_target(segs[1:], reg)
        if res == 'tn':
            rest = segs[1:]
            if rest and rest[0].lower() != 'obs' and books.try_book_number(rest[0]) is None:
                rest = rest[1:]  # "help" or a localized word such as "ayuda"
            if rest and rest[0].lower() == 'obs':
                return _obs_target(rest[1:], reg)
            try:
                ref = refs.parse_rc('rc://*/tn/help/' + '/'.join(rest))
            except refs.RefError as e:
                raise _Skip('scripture', str(e))
            return ('scripture', _scripture_range(ref, reg), _scripture_label(ref))
        if res == 'obs':
            return _obs_target(segs[1:], reg)
        if res == 'help' and len(segs) > 1 and segs[1].lower() == 'obs':
            return _obs_target(segs[2:], reg)
        raise _Skip('other', 'unknown rc resource %r' % res)
    if h.startswith('.'):
        return _relative_target(h, reg, book, chapter)
    raise _Skip('other', 'unsupported href %r' % h[:60])


def _dict_target(segs, reg, slug_only=False):
    segs = [s for s in segs if s not in ('.', '..')]
    key = None
    if len(segs) >= 2 and segs[-2].lower() in CATEGORIES:
        key = '%s/%s' % (segs[-2].lower(), re.sub(r'\.md$', '', segs[-1], flags=re.I))
    elif segs:
        slug = re.sub(r'\.md$', '', segs[-1], flags=re.I)
        hits = [k for k in reg.tw_keys if k.endswith('/' + slug)]
        key = hits[0] if len(hits) == 1 else None
        if key is None:
            raise _Skip('dictionary', 'tW slug %r not unique or unknown' % slug)
    else:
        raise _Skip('dictionary', 'empty tW path')
    if not reg.tw_uuid:
        raise _Skip('dictionary', 'tW module not built')
    if key not in reg.tw_keys:
        raise _Skip('dictionary', 'tW entry missing: %s' % key)
    label = reg.tw_words.get(key) or key.split('/', 1)[1]
    return ('dict', key, label)


def _obs_target(segs, reg):
    n = next((int(s) for s in segs if re.fullmatch(r'\d{1,3}', s)), None)
    if n is None:
        raise _Skip('obs', 'no story number')
    if not reg.obs_uuid:
        raise _Skip('obs', 'OBS module not built')
    if n not in reg.obs_stories:
        raise _Skip('obs', 'OBS story missing: %d' % n)
    return ('obs', n, 'OBS %d' % n)


def _relative_target(h, reg, book, chapter):
    segs = [p for p in re.sub(r'\.md$', '', h, flags=re.I).split('/') if p not in ('', '.', '..')]
    if not segs:
        raise _Skip('other', 'empty relative link')
    first = segs[0].lower()
    if first in CATEGORIES:
        return _dict_target(segs, reg)
    last = segs[-1].lower()
    if last.isdigit() or last in ('intro', 'front'):
        try:
            ref = refs.parse_relative(h, book, chapter)
        except refs.RefError as e:
            raise _Skip('scripture', str(e))
        return ('scripture', _scripture_range(ref, reg), _scripture_label(ref))
    if len(segs) == 1:
        return _dict_target(segs, reg)
    raise _Skip('other', 'unrecognised relative link %r' % h[:60])


# ---------------------------------------------------------------- rewriting

def _esc_attr(s):
    return s.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')


_A_RE = re.compile(r'<a\b([^>]*)>(.*?)</a>', re.S | re.I)
_HREF_RE = re.compile(r'\bhref\s*=\s*"([^"]*)"', re.I)
_BARE_RE = re.compile(r'\[\[\s*([^\[\]]*?)\s*\]\]')
_RCTEXT_RE = re.compile(r'rc\s*:\s*/{1,2}[^\s<>)\]"]*', re.I)
_EMPTY_PAREN = re.compile(r'\s*\(\s*(?:[^()\x00]*?[:：]\s*)?(?:\x00[\s,;.]*)+\)')
_PRE_CLASS = re.compile(r'class\s*=\s*"[^"]*(scripture-link|dictionary-link|book-link)', re.I)
_GONE = '\x00'


def resolve_links(html, registry=None, book=None, chapter=None):
    reg = registry or Registry()
    if isinstance(book, str):
        book = books.try_book_number(book)
    stats = {}
    vlinks = []

    def build(target, label):
        kind = target[0]
        if kind == 'scripture':
            (a, b), lab = target[1], target[2]
            if (a, b) not in vlinks:
                vlinks.append((a, b))
            return '<a href="#verse-%d" class="scripture-link">%s</a>' % (a, label if label is not None else lab)
        if kind == 'dict':
            lab = label if label is not None else target[2]
            return '<a href="#entry-%s" class="dictionary-link" data-module="%s">%s</a>' % (
                _esc_attr(target[1]), _esc_attr(reg.tw_uuid), lab)
        lab = label if label is not None else target[2]
        return '<a href="#section-%d" class="book-link" data-module="%s">%s</a>' % (
            target[1], _esc_attr(reg.obs_uuid), lab)

    def statkind(target):
        return {'dict': 'dictionary'}.get(target[0], target[0])

    def handle(href, label):
        """Return replacement text for a link with ``label`` (None for a bare link)."""
        try:
            target = _classify(href, reg, book, chapter)
        except _Skip as s:
            _count(stats, s.kind, 'unresolved', {'href': _html.unescape(href)[:120], 'reason': s.reason})
            return label if label is not None else _GONE
        if target[0] == 'http':
            _count(stats, 'http', 'resolved')
            return None  # keep the original text untouched
        if target[0] == 'ta':
            _count(stats, 'ta', 'stripped')
            return label if label is not None else _GONE
        _count(stats, statkind(target), 'resolved')
        return build(target, label)

    def anchor(m):
        attrs, label = m.group(1), m.group(2)
        if _PRE_CLASS.search(attrs):
            return m.group(0)
        hm = _HREF_RE.search(attrs)
        if not hm:
            return label
        href = hm.group(1)
        if href.startswith('#'):
            return m.group(0)
        out = handle(href, label)
        return m.group(0) if out is None else out

    text = _A_RE.sub(anchor, html or '')

    def bare(m):
        out = handle(m.group(1), None)
        return m.group(0) if out is None else out

    text = _BARE_RE.sub(bare, text)

    def rctext(m):
        out = handle(m.group(0), None)
        return m.group(0) if out is None else out

    text = _RCTEXT_RE.sub(rctext, text)
    if _GONE in text:
        text = _EMPTY_PAREN.sub('', text).replace(_GONE, '')
    return LinkResult(text, vlinks, stats)
