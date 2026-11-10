"""Parse Door43 scripture references into ``Ref`` records.

Forms (tN/tQ Reference column): ``front:intro``, ``1:intro``, ``1:5``, ``1:3-5``,
``1:20-2:3``, ``1:3,5``; rc links ``rc://*/tn/help/1jn/01/05`` (also ``rc: //``
which is repaired); tN relative links ``./05.md``, ``../01/05.md``,
``../../1jn/01/05.md``.

``Ref.level`` is one of ``book`` (front:intro or a book-only rc), ``chapter``,
``verse`` or ``passage``. End fields are inclusive and always filled for verse and
passage levels (a verse has ch2=ch, v2=v1). A chapter has v1=v2=None; a book has
ch=ch2=None. ``pieces`` holds every (ch, v1, ch2, v2) of a ``1:3,5`` list.
"""
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from . import books


class RefError(ValueError):
    """Malformed reference."""


@dataclass(frozen=True)
class Ref:
    book: Optional[int]
    ch: Optional[int]
    v1: Optional[int]
    ch2: Optional[int]
    v2: Optional[int]
    level: str
    pieces: Tuple = ()


def _int(s, what):
    if not re.fullmatch(r'\d{1,3}', s):
        raise RefError(f'bad {what}: {s!r}')
    return int(s)


def _cv(s):
    """'5' or '1:5' pieces handled by caller; here a 'c:v' string -> (c, v)."""
    c, _, v = s.partition(':')
    return _int(c.strip(), 'chapter'), _int(v.strip(), 'verse')


def parse_reference(s, book=None, chapter=None):
    """Parse any supported form. ``book`` (number or code) and ``chapter`` give the
    context for tN rows and relative links; rc paths carry their own book."""
    if s is None:
        raise RefError('empty reference')
    text = s.strip()
    if not text:
        raise RefError('empty reference')
    if isinstance(book, str):
        book = books.book_number(book)
    low = text.lower()
    if low.startswith('rc:'):
        return parse_rc(text)
    if low.startswith('.'):
        return parse_relative(text, book, chapter)
    return _parse_plain(text, book)


def _parse_plain(text, book):
    t = text.strip()
    ch_s, sep, rest = t.partition(':')
    if not sep:
        raise RefError(f'no ":" in reference {text!r}')
    ch_s, rest = ch_s.strip(), rest.strip()
    if not ch_s or not rest:
        raise RefError(f'malformed reference {text!r}')
    if ch_s.lower() == 'front':
        if rest.lower() != 'intro':
            raise RefError(f'unsupported front reference {text!r}')
        return Ref(book, None, None, None, None, 'book')
    ch = _int(ch_s, 'chapter')
    if rest.lower() == 'intro':
        return Ref(book, ch, None, ch, None, 'chapter')
    if ',' in rest:
        pieces = []
        for part in rest.split(','):
            part = part.strip()
            if not part:
                raise RefError(f'empty piece in {text!r}')
            pieces.append(_range(ch, part, text))
        lo = min(pieces, key=lambda p: (p[0], p[1]))
        hi = max(pieces, key=lambda p: (p[2], p[3]))
        return Ref(book, lo[0], lo[1], hi[2], hi[3], 'passage', tuple(pieces))
    c1, v1, c2, v2 = _range(ch, rest, text)
    if (c1, v1) == (c2, v2):
        return Ref(book, c1, v1, c2, v2, 'verse')
    return Ref(book, c1, v1, c2, v2, 'passage')


def _range(ch, part, whole):
    """'5', '3-5' or '20-2:3' relative to chapter ch -> (c1, v1, c2, v2)."""
    a, dash, b = part.partition('-')
    a, b = a.strip(), b.strip()
    v1 = _int(a, 'verse')
    if not dash:
        return ch, v1, ch, v1
    if ':' in b:
        c2, v2 = _cv(b)
    else:
        c2, v2 = ch, _int(b, 'verse')
    if (c2, v2) < (ch, v1):
        raise RefError(f'descending range in {whole!r}')
    return ch, v1, c2, v2


_RC_RE = re.compile(r'^rc\s*:\s*//\s*[^/\s]*\s*/\s*tn\s*/\s*help\s*/\s*(.+?)\s*/?$', re.I)


def parse_rc(s):
    """``rc://*/tn/help/1jn/01/05`` -> verse; ``.../exo/12`` -> chapter; ``.../exo`` -> book."""
    m = _RC_RE.match(s.strip())
    if not m:
        raise RefError(f'not a tn help rc link: {s!r}')
    segs = [p for p in m.group(1).split('/') if p != '']
    if not segs or len(segs) > 3:
        raise RefError(f'bad rc path: {s!r}')
    bk = books.try_book_number(segs[0])
    if bk is None:
        raise RefError(f'unknown book in {s!r}')
    return _from_segments(bk, segs[1:], None, s)


def parse_relative(s, book, chapter):
    """tN relative link: ``./05.md``, ``../01/05.md``, ``../../1jn/01/05.md`` (also ``intro.md``)."""
    t = re.sub(r'\.?md$', '', re.sub(r'\s+', '', s), flags=re.I).rstrip('/')
    segs = [p for p in t.split('/') if p not in ('', '.', '..')]
    if not segs or len(segs) > 3:
        raise RefError(f'bad relative link: {s!r}')
    if len(segs) == 3:
        bk = books.try_book_number(segs[0])
        if bk is None:
            raise RefError(f'unknown book in {s!r}')
        segs = segs[1:]
    else:
        bk = book
    return _from_segments(bk, segs, chapter, s)


def _from_segments(bk, segs, ctx_chapter, whole):
    """segs = [] (book), [cc], [cc, vv] or (with ctx chapter) [vv]."""
    if not segs:
        return Ref(bk, None, None, None, None, 'book')
    if len(segs) == 1:
        seg = segs[0].lower()
        if ctx_chapter is not None:
            if seg == 'intro':
                return Ref(bk, ctx_chapter, None, ctx_chapter, None, 'chapter')
            v = _int(seg, 'verse')
            return Ref(bk, ctx_chapter, v, ctx_chapter, v, 'verse')
        if seg == 'front':
            return Ref(bk, None, None, None, None, 'book')
        c = _int(seg, 'chapter')
        return Ref(bk, c, None, c, None, 'chapter')
    c_s, v_s = segs[0].lower(), segs[1].lower()
    if c_s == 'front':
        return Ref(bk, None, None, None, None, 'book')
    c = _int(c_s, 'chapter')
    if v_s == 'intro':
        return Ref(bk, c, None, c, None, 'chapter')
    v = _int(v_s, 'verse')
    return Ref(bk, c, v, c, v, 'verse')
