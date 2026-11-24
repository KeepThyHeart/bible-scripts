"""Damage checks for converted commentary modules (task 0225).

The published Gill commentary lost its scripture references (800 paragraphs
reduced to ':-' stubs, 4 whole entries) and gained chapter-navigation text
glued onto chapter-final entries ("2 Chronicles2Ch 27"). Pure functions, no
database or SWORD access, so they are unit-testable with small fixtures:
see test_commentary_checks.py. swordcheck.verify_generic runs them on every
commentary.
"""
import re

# A paragraph that is only the punctuation left once a reference was removed.
STUB_RE = re.compile(r'<p>\s*:-[,.;:]?\s*</p>')
WHOLE_RE = re.compile(r'^:-[,.;:]?$')
# Book name glued to an abbreviation: "2 Chronicles2Ch 27", "asPs 69:4".
GLUED_RE = re.compile(r'[a-z]\d?[A-Z][a-z]{1,3} \d+')
TAG_RE = re.compile(r'<[^>]+>')
# A control character or U+FFFD after the last real text: the source index overstated the
# entry length and a junk character from the next record was kept.
JUNK_END_RE = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f\ufffd]\\s*$')


def plain(html):
    return re.sub(r'\s+', ' ', TAG_RE.sub(' ', html)).strip()


def commentary_flags(html):
    """Return the damage flags ('stub', 'whole', 'glued', 'junk_end') for one entry."""
    flags = []
    text = plain(html)
    if STUB_RE.search(html):
        flags.append('stub')
    if WHOLE_RE.match(text):
        flags.append('whole')
    if GLUED_RE.search(text):
        flags.append('glued')
    if JUNK_END_RE.search(text):
        flags.append('junk_end')
    return flags


def scan_commentary(rows):
    """rows: iterable of (key, html). Returns {'counts': {...}, 'examples': {...}}."""
    counts = {'stub': 0, 'whole': 0, 'glued': 0, 'junk_end': 0}
    examples = {k: [] for k in counts}
    for key, html in rows:
        for flag in commentary_flags(html or ''):
            counts[flag] += 1
            if len(examples[flag]) < 5:
                examples[flag].append({'key': key, 'excerpt': plain(html)[:160]})
    return {'counts': counts, 'examples': examples}
