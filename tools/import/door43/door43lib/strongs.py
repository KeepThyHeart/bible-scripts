"""Strong's number normalisation (design section 5.3).

Target form is the dominant SWORD one: a letter and four zero-padded digits
(``G0027``, ``H0430``). Extra information is returned in a metadata dict:

* ``x_strong``: the raw core when the source had an extension (a fifth digit that
  is not 0 as in UGNT ``G00271``, or a suffix letter as in ``H5921a``)
* ``prefix``: Hebrew prefix letters before a colon (``b:H3117`` -> ``b``)
"""
import re

_CORE = re.compile(r'^([GHgh])(\d{1,5})([a-z]?)$')
TOKEN_RE = re.compile(r'\b[GH]\d{1,5}[a-z]?\b')


def normalize_strong(raw):
    """Return ``(number, meta)``; ``number`` is None for a prefix-only or unparseable value."""
    meta = {}
    if raw is None:
        return None, meta
    s = str(raw).strip()
    if not s:
        return None, meta
    prefix = None
    if ':' in s:
        prefix, _, s = s.rpartition(':')
        prefix = prefix.strip()
        if prefix:
            meta['prefix'] = prefix
    m = _CORE.match(s.strip())
    if not m:
        return None, meta
    letter, digits, suffix = m.group(1).upper(), m.group(2), m.group(3)
    extended = bool(suffix)
    if len(digits) == 5:
        if digits[4] != '0':
            extended = True
        digits = digits[:4]
    number = letter + digits.zfill(4)
    if extended:
        meta['x_strong'] = s.strip()
    return number, meta


def extract_strongs(text):
    """Distinct normalised Strong's numbers found in free text, in first-seen order."""
    seen = []
    for tok in TOKEN_RE.findall(text or ''):
        n, _ = normalize_strong(tok)
        if n and n not in seen:
            seen.append(n)
    return seen
