"""Book codes -> book numbers 1..66 (the KJV/Protestant order).

Accepts USFM ids (``3JN``), lowercase rc codes (``3jn``) and Door43 file prefixes
(``65-3JN``, ``65-3JN.usfm``, ``tn_3JN.tsv`` is NOT a prefix form; pass the code).
The numeric prefix of a file name is Door43's USFM file number, which differs from
the book number, so it is ignored and only the code after the hyphen counts.
"""
import os
import re

# USFM 3 ids in canonical order; index + 1 is the book number.
USFM_CODES = (
    'GEN', 'EXO', 'LEV', 'NUM', 'DEU', 'JOS', 'JDG', 'RUT', '1SA', '2SA', '1KI', '2KI', '1CH', '2CH',
    'EZR', 'NEH', 'EST', 'JOB', 'PSA', 'PRO', 'ECC', 'SNG', 'ISA', 'JER', 'LAM', 'EZK', 'DAN', 'HOS',
    'JOL', 'AMO', 'OBA', 'JON', 'MIC', 'NAM', 'HAB', 'ZEP', 'HAG', 'ZEC', 'MAL',
    'MAT', 'MRK', 'LUK', 'JHN', 'ACT', 'ROM', '1CO', '2CO', 'GAL', 'EPH', 'PHP', 'COL', '1TH', '2TH',
    '1TI', '2TI', 'TIT', 'PHM', 'HEB', 'JAS', '1PE', '2PE', '1JN', '2JN', '3JN', 'JUD', 'REV',
)

_ALIASES = {
    'SOS': 'SNG', 'SON': 'SNG', 'CAN': 'SNG', 'PHI': 'PHP', 'PHL': 'PHM', 'JOH': 'JHN', 'MRC': 'MRK',
    'MAR': 'MRK', 'EZE': 'EZK', 'JUDE': 'JUD', 'JUG': 'JDG', 'PS': 'PSA', 'DEUT': 'DEU',
    'DT': 'DEU', 'JL': 'JOL', 'OB': 'OBA', 'ZEPH': 'ZEP', 'ZECH': 'ZEC', 'NAH': 'NAM', 'AM': 'AMO',
    'JNH': 'JON', 'MK': 'MRK', 'LK': 'LUK', 'JN': 'JHN', 'ACTS': 'ACT', 'ROMANS': 'ROM',
}

_NUMBER = {c: i + 1 for i, c in enumerate(USFM_CODES)}
_NUMBER.update({a: _NUMBER[c] for a, c in _ALIASES.items()})


class UnknownBook(ValueError):
    pass


def _clean(code):
    s = os.path.basename(str(code).strip())
    s = re.sub(r'\.(usfm|sfm|tsv|md|txt)$', '', s, flags=re.I)
    m = re.match(r'^\d{1,3}[-_](.+)$', s)
    # "65-3JN" -> "3JN" ; but a bare "1JN" has no hyphen and is left alone
    if m:
        s = m.group(1)
    return s.upper()


def book_number(code):
    """Return the book number 1..66 for a USFM/rc code or file prefix, else raise UnknownBook."""
    key = _clean(code)
    try:
        return _NUMBER[key]
    except KeyError:
        raise UnknownBook(f'unknown book code: {code!r}') from None


def try_book_number(code):
    """Like book_number but returns None for an unknown code."""
    try:
        return book_number(code)
    except UnknownBook:
        return None


def usfm_code(number):
    """Inverse of book_number: 64 -> '3JN'."""
    if not 1 <= int(number) <= 66:
        raise UnknownBook(f'book number out of range: {number!r}')
    return USFM_CODES[int(number) - 1]


def verse_id(book, chapter, verse):
    """book*1000000 + chapter*1000 + verse."""
    return int(book) * 1000000 + int(chapter) * 1000 + int(verse)


def split_verse_id(vid):
    return vid // 1000000, (vid // 1000) % 1000, vid % 1000
