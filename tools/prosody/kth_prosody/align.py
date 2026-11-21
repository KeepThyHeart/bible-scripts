"""Match the words of a verse to the words of its phoneme string.

espeak (through Kokoro's phonemizer) writes short pairs as one word: "of the" -> ʌvðə,
"to be" -> təbi, "there were" -> ðɛɹwˌɜː, "to and fro" -> tˌuːəndfɹˈoʊ. So one phoneme word may
cover up to three text words. Every match is checked by its first sound; a verse with no
consistent match gets None and is left alone by the rules.
"""
import re
from functools import lru_cache

WORD = re.compile(r"[A-Za-z']+")
MARK = re.compile(r"⟦([^|⟧]*)\|[^⟧]*⟧")   # pronunciation markers: ⟦word|ipa⟧
PHON_CHAR = re.compile(r"[^\s,.;:!?—–\-\"“”‘’()]")

# Plausible first sounds for each first letter (loose on purpose: the alignment only needs
# to tell neighbouring words apart).
INIT = {'a': 'æɑɐʌəeɔaɚɪɛ', 'b': 'b', 'c': 'kstʃsʃ', 'd': 'dɾʤ', 'e': 'ɛiɪəeᵻɜʌjæ', 'f': 'f',
        'g': 'ɡdʒʒ', 'h': 'hwɑɛaɔoʌ', 'i': 'ɪaɪəiᵻ', 'j': 'dʒjh', 'k': 'kn', 'l': 'l', 'm': 'm',
        'n': 'n', 'o': 'ɔɑoʌəwɚaʊ', 'p': 'pfs', 'q': 'k', 'r': 'ɹ', 's': 'sʃzθ', 't': 'tθðʃɾ',
        'u': 'ʌjuʊəɜ', 'v': 'v', 'w': 'wɹhʊ', 'x': 'zɛ', 'y': 'jɪi', 'z': 'z'}
JOINABLE = {'a', 'be', 'were', 'was', 'is', 'are', 'am', 'fro'}


def text_words(text):
    """Lower-case words of a verse, with pronunciation markers replaced by their word."""
    return [w.lower() for w in WORD.findall(MARK.sub(r"\1", text))]


def phoneme_words(ps, vocab):
    """(first token, last token, phonemes) per word; token 0 is the start-of-text pad."""
    chars = [c for c in ps if c in vocab]
    spans, cur = [], None
    for i, c in enumerate(chars):
        if PHON_CHAR.match(c):
            cur = [i + 1, i + 1] if cur is None else [cur[0], i + 1]
        elif cur:
            spans.append(tuple(cur)); cur = None
    if cur:
        spans.append(tuple(cur))
    return [(a, b, ''.join(chars[a - 1:b])) for a, b in spans]


def sounds_like(word, ph):
    c = ph.lstrip('ˈˌ')[:1]
    return bool(c) and c in INIT.get(word[:1], '')


def align(tw, pw, small_words):
    """Text word index -> phoneme word index, or None."""
    n, m = len(tw), len(pw)
    joinable = set(small_words) | JOINABLE

    @lru_cache(None)
    def go(i, j):
        # best (misses, joins, mapping): fewest first sounds that do not fit, then fewest joins
        if i == n:
            return (0, 0, ()) if j == m else None
        if j == m:
            return None
        miss = 0 if sounds_like(tw[i], pw[j][2]) else 1
        best = None
        r = go(i + 1, j + 1)
        if r:
            best = (r[0] + miss, r[1], (j,) + r[2])
        for k in (2, 3):
            if i + k <= n and all(tw[x] in joinable or len(tw[x]) <= 3 for x in range(i + 1, i + k)):
                r = go(i + k, j + 1)
                if r:
                    c = (r[0] + miss, r[1] + k - 1, (j,) * k + r[2])
                    if best is None or c[:2] < best[:2]:
                        best = c
        return best

    if n > 400:
        return None
    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * n + 100))
    r = go(0, 0)
    return r[2] if r and r[0] <= n // 15 else None
