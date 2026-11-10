"""ufw -> KJV verse mapping (design section 5.1).

``Versification.load()`` reads ``data/versification-ufw-to-kjv.json`` and the KJV
chapter shape from ``scripts/data/kjv-versification.json``. ``map_verse`` returns the
KJV ``(chapter, verse)`` or raises ``UnmappedVerse``; ``merge_info`` says whether a
source verse is folded into a host verse (and whether before or after the host's text).
"""
import json
import os
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, '..', 'data', 'versification-ufw-to-kjv.json')
KJV_SHAPE_PATH = os.path.join(HERE, '..', '..', '..', '..', 'scripts', 'data', 'kjv-versification.json')

Merge = namedtuple('Merge', 'book ch v into_ch into_v prefix note')


class UnmappedVerse(ValueError):
    """A source verse with no rule and no KJV address."""


def load_kjv_counts(path=None):
    """{book_number: [verses in ch1, ch2, ...]}"""
    with open(path or KJV_SHAPE_PATH, encoding='utf-8') as f:
        data = json.load(f)
    return {b['book_number']: list(b['verse_counts']) for b in data['books']}


class Versification:
    def __init__(self, rules, merges, kjv_counts=None, name='ufw'):
        self.name = name
        self.rules = list(rules)
        self.merges = {(m.book, m.ch, m.v): m for m in merges}
        self.kjv_counts = kjv_counts

    @classmethod
    def load(cls, path=None, kjv_path=None):
        with open(path or DEFAULT_PATH, encoding='utf-8') as f:
            data = json.load(f)
        merges = [Merge(m['b'], m['c'], m['v'], m['into_c'], m['into_v'], bool(m.get('prefix')),
                        m.get('note', '')) for m in data.get('merges', [])]
        return cls(data.get('rules', []), merges, load_kjv_counts(kjv_path), data.get('versification', 'ufw'))

    def in_kjv(self, book, ch, v):
        if self.kjv_counts is None:
            return True
        counts = self.kjv_counts.get(book)
        return bool(counts) and 1 <= ch <= len(counts) and 1 <= v <= counts[ch - 1]

    def merge_info(self, book, ch, v):
        """The Merge record when (book, ch, v) is folded into a host verse, else None."""
        return self.merges.get((book, ch, v))

    def map_verse(self, book, ch, v):
        """KJV (chapter, verse) for a source verse; a merged verse maps to its host."""
        m = self.merges.get((book, ch, v))
        if m:
            return m.into_ch, m.into_v
        for r in self.rules:
            if r['b'] == book and r['c'] == ch and r['from'] <= v <= r['to']:
                ch2, v2 = r['dc'], v + r['off']
                break
        else:
            ch2, v2 = ch, v
        if not self.in_kjv(book, ch2, v2):
            raise UnmappedVerse(f'book {book} {ch}:{v} has no KJV address (got {ch2}:{v2})')
        return ch2, v2

    def map_verse_id(self, verse_id):
        book, ch, v = verse_id // 1000000, (verse_id // 1000) % 1000, verse_id % 1000
        c2, v2 = self.map_verse(book, ch, v)
        return book * 1000000 + c2 * 1000 + v2
