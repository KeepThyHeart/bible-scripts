"""Arabic normalisation for the ar helps (design section 5.4).

U+06BE -> U+0647, U+06A9 -> U+0643, Persian digits U+06F0..9 -> U+0660..9,
U+06CC -> U+064A. Exception: a word-final U+06CC becomes U+0649 when the
diacritic-stripped word list knows the U+0649 spelling and not the U+064A one.
"""
import unicodedata

HEH_DOACHASHMEE = 'ھ'
KEHEH = 'ک'
FARSI_YEH = 'ی'
YEH = 'ي'
ALEF_MAKSURA = 'ى'

_SIMPLE = {HEH_DOACHASHMEE: 'ه', KEHEH: 'ك'}
_DIGITS = {chr(0x06F0 + i): chr(0x0660 + i) for i in range(10)}


def strip_diacritics(word):
    """Remove combining marks (Mn) and tatweel."""
    return ''.join(c for c in word if unicodedata.category(c) != 'Mn' and c != 'ـ')


def build_word_set(texts):
    """Diacritic-stripped, non-Persian-normalised word set from an iterable of texts."""
    words = set()
    for t in texts:
        for w in str(t).split():
            w = ''.join(c for c in strip_diacritics(w) if c.isalpha())
            if w:
                words.add(w)
    return words


def _is_word_char(c):
    return c == FARSI_YEH or unicodedata.category(c) == 'Mn' or (c.isalpha() and '؀' <= c <= 'ۿ') \
        or c in _SIMPLE


def normalize_arabic(text, words=None):
    """Return ``(text, counts)``. ``counts`` keys: '06BE->0647', '06A9->0643',
    '06F0-9->0660-9', '06CC->064A', '06CC->0649' (only the nonzero ones)."""
    counts = {}

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if not _is_word_char(c):
            if c in _DIGITS:
                out.append(_DIGITS[c])
                bump('06F0-9->0660-9')
            else:
                out.append(c)
            i += 1
            continue
        j = i
        while j < n and _is_word_char(text[j]):
            j += 1
        word = text[i:j]
        i = j
        # last base letter (ignoring marks)
        last = max((k for k, ch in enumerate(word) if unicodedata.category(ch) != 'Mn'), default=None)
        buf = []
        for k, ch in enumerate(word):
            if ch in _SIMPLE:
                buf.append(_SIMPLE[ch])
                bump('06BE->0647' if ch == HEH_DOACHASHMEE else '06A9->0643')
            elif ch == FARSI_YEH:
                target = YEH
                if k == last and words:
                    base = ''.join(YEH if x == FARSI_YEH else _SIMPLE.get(x, x)
                                   for x in strip_diacritics(word))
                    alt = base[:-1] + ALEF_MAKSURA
                    if alt in words and base not in words:
                        target = ALEF_MAKSURA
                buf.append(target)
                bump('06CC->064A' if target == YEH else '06CC->0649')
            else:
                buf.append(ch)
        out.append(''.join(buf))
    return ''.join(out), counts
