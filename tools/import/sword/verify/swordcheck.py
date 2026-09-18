#!/usr/bin/env python3
"""
swordcheck - convert SWORD modules in bulk and verify that they came over correctly.

The tool does the bulk of the checking; a person or an AI agent reads its
reports. Every check compares the converted database against an INDEPENDENT
reading of the SWORD module (tools/import/sword/probe/sword-probe, which uses
libsword's own strip filters and HTML renderer, not the converters' parser).

Subcommands
  verify   one module:  --zip MODULE.zip --db bible_x.db [--log convert.log]
  batch    many modules from the catalog CSV: download, convert, verify, summarise
  summary  rebuild summary.md / summary.csv from existing reports
  probe    look at one module side by side: SWORD raw / SWORD plain / DB row
  select   print which catalog rows a batch filter would pick (dry run)

Python 3 standard library only. Needs the converters and sword-probe built
(`make` in tools/import/sword/{common,bible,...,probe}).
See tools/import/sword/verify/README.md.
"""

import argparse
import concurrent.futures
import csv
import difflib
import hashlib
import html
import json
import math
import os
import random
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
SWORD_DIR = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(SWORD_DIR, '..', '..', '..'))
PROBE = os.path.join(SWORD_DIR, 'probe', 'sword-probe')
CONVERTERS = {
    'bible': os.path.join(SWORD_DIR, 'bible', 'sword2bible'),
    'commentary': os.path.join(SWORD_DIR, 'commentary', 'sword2commentary'),
    'dictionary': os.path.join(SWORD_DIR, 'dictionary', 'sword2dictionary'),
    'book': os.path.join(SWORD_DIR, 'book', 'sword2book'),
    'devotional': os.path.join(SWORD_DIR, 'devotional', 'sword2devotional'),
}
VERSIFICATION_JSON = os.path.join(REPO, 'scripts', 'data', 'kjv-versification.json')
MT_MAP_JSON = os.path.join(SWORD_DIR, 'data', 'versification-mt-to-kjv.json')
VALIDATOR = os.path.join(REPO, 'scripts', 'modules', 'validate-module.js')
OVERRIDES_JSON = os.path.join(SWORD_DIR, 'data', 'versification-overrides.json')


def load_overrides():
    """module id (lower case) -> versification to convert with, from evidence-backed overrides."""
    try:
        with open(OVERRIDES_JSON, encoding='utf-8') as f:
            data = json.load(f)
    except OSError:
        return {}
    return {k.lower(): v['versification'] for k, v in data.get('overrides', {}).items()}

# ---------------------------------------------------------------------------
# Licence policy
#
# The catalog's `conversion_license_verdict` decides what may be converted.
# NEEDS PERMISSION and MANUAL REVIEW are never selected: no flag unlocks them.
# The tiers only widen within what the catalog already calls usable, and the
# two wider tiers depend on decisions a person has to make (is the app
# non-commercial? is a versification remap an "adaptation" under CC ND?).
# ---------------------------------------------------------------------------
LICENSE_TIERS = OrderedDict([
    ('open', {'OK', 'OK (attribution)', 'OK (attribution; share-alike)', 'OK (copyleft)'}),
    ('verbatim', {'OK (verbatim; attribution)', 'LIKELY OK (verbatim; verify)'}),
    ('noncommercial', {'OK IF NON-COMMERCIAL', 'OK IF NON-COMMERCIAL (verbatim)',
                       'OK IF NON-COMMERCIAL (verify terms)'}),
])
NEVER_VERDICTS = {'NEEDS PERMISSION', 'MANUAL REVIEW', ''}


def allowed_verdicts(tier):
    out = set()
    for name, verdicts in LICENSE_TIERS.items():
        out |= verdicts
        if name == tier:
            return out
    raise SystemExit(f'unknown licence tier {tier!r}; use one of {", ".join(LICENSE_TIERS)}')


# Preference when one module id is published by several repositories.
REPO_STATUS_RANK = {
    'main': 0, 'main (Wycliffe partner)': 1, 'publisher': 2, 'third-party': 3,
    'third-party (FTP only)': 4, 'beta (unreviewed)': 5, 'attic (retired/superseded)': 6,
}

# ---------------------------------------------------------------------------
# Canon and versification (kept in step with sword_common.cpp)
# ---------------------------------------------------------------------------
OSIS66 = ['Gen', 'Exod', 'Lev', 'Num', 'Deut', 'Josh', 'Judg', 'Ruth', '1Sam', '2Sam',
          '1Kgs', '2Kgs', '1Chr', '2Chr', 'Ezra', 'Neh', 'Esth', 'Job', 'Ps', 'Prov',
          'Eccl', 'Song', 'Isa', 'Jer', 'Lam', 'Ezek', 'Dan', 'Hos', 'Joel', 'Amos',
          'Obad', 'Jonah', 'Mic', 'Nah', 'Hab', 'Zeph', 'Hag', 'Zech', 'Mal',
          'Matt', 'Mark', 'Luke', 'John', 'Acts', 'Rom', '1Cor', '2Cor', 'Gal', 'Eph',
          'Phil', 'Col', '1Thess', '2Thess', '1Tim', '2Tim', 'Titus', 'Phlm', 'Heb', 'Jas',
          '1Pet', '2Pet', '1John', '2John', '3John', 'Jude', 'Rev']

# Spelling variants accepted by canonicalBookNumberFromOsis() in sword_common.cpp.
_CANON_VARIANTS = {
    'genesis': 1, 'exo': 2, 'exodus': 2, 'leviticus': 3, 'numbers': 4, 'deu': 5,
    'deuteronomy': 5, 'joshua': 6, 'judges': 7, '1samuel': 9, '2samuel': 10,
    '1kings': 11, '2kings': 12, '1chronicles': 13, '2chronicles': 14, 'nehemiah': 16,
    'esther': 17, 'psa': 19, 'psalm': 19, 'psalms': 19, 'proverbs': 20,
    'ecclesiastes': 21, 'qoh': 21, 'sos': 22, 'songofsongs': 22, 'cant': 22,
    'isaiah': 23, 'jeremiah': 24, 'lamentations': 25, 'ezekiel': 26, 'daniel': 27,
    'hosea': 28, 'obadiah': 31, 'jon': 32, 'micah': 33, 'nahum': 34, 'habakkuk': 35,
    'zephaniah': 36, 'haggai': 37, 'zechariah': 38, 'malachi': 39, 'mat': 40,
    'matthew': 40, 'romans': 45, '1corinthians': 46, '2corinthians': 47,
    'galatians': 48, 'ephesians': 49, 'philippians': 50, 'colossians': 51,
    '1thessalonians': 52, '2thessalonians': 53, '1timothy': 54, '2timothy': 55,
    'tit': 56, 'phlmn': 57, 'philemon': 57, 'hebrews': 58, 'james': 59, '1peter': 60,
    '2peter': 61, '1jn': 62, '2jn': 63, '3jn': 64, 'revelation': 66, 'revofjohn': 66,
}
CANON_LOOKUP = {name.lower(): i + 1 for i, name in enumerate(OSIS66)}
CANON_LOOKUP.update(_CANON_VARIANTS)


def canon_book(osis_book):
    if not osis_book:
        return 0
    key = re.sub(r'[ _.\-]', '', osis_book).lower()
    return CANON_LOOKUP.get(key, 0)


def load_versification():
    with open(VERSIFICATION_JSON, encoding='utf-8') as f:
        data = json.load(f)
    counts = {}
    names = {}
    for b in data['books']:
        counts[b['book_number']] = b['verse_counts']
        names[b['book_number']] = (b['name'], b['abbreviation'])
    return counts, names


KJV_COUNTS, BOOK_NAMES = load_versification()


def vid(b, c, v):
    return b * 1000000 + c * 1000 + v


def split_vid(verse_id):
    return verse_id // 1000000, (verse_id // 1000) % 1000, verse_id % 1000


def ref_name(verse_id):
    b, c, v = split_vid(verse_id)
    name = BOOK_NAMES.get(b, (f'Book{b}', ''))[0]
    return f'{name} {c}:{v}'


def in_canon(verse_id):
    b, c, v = split_vid(verse_id)
    counts = KJV_COUNTS.get(b)
    return bool(counts) and 1 <= c <= len(counts) and 1 <= v <= counts[c - 1]


def parse_ref(text):
    """'John 3:16', 'Jn 3:16', 'John.3.16', '43003016' -> verse_id (KJV numbering)."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    m = re.match(r'^\s*(\d?\s*[A-Za-z][A-Za-z .]*?)[\s.]*(\d+)[:.](\d+)\s*$', text)
    if not m:
        raise ValueError(f'cannot parse reference {text!r}')
    book_text = re.sub(r'[\s.]', '', m.group(1)).lower()
    book = canon_book(book_text)
    if not book:
        for num, (name, abbr) in BOOK_NAMES.items():
            if book_text in (name.replace(' ', '').lower(), abbr.lower()):
                book = num
                break
    if not book:
        candidates = [num for num, (name, _) in BOOK_NAMES.items()
                      if name.replace(' ', '').lower().startswith(book_text)]
        if len(candidates) == 1:
            book = candidates[0]
    if not book:
        raise ValueError(f'unknown book in {text!r}')
    return vid(book, int(m.group(2)), int(m.group(3)))


# Systems the converter translates (sword2bible.cpp mappableVersifications()).
LIBSWORD_MAPPED = {'nrsv', 'vulg', 'synodal', 'nrsva', 'synodalprot'}   # + aliases (sword2bible versificationAlias)
MT_MAPPED = {'mt', 'leningrad', 'german', 'luther'}   # external maps: see EXTERNAL_MAPS
# Systems the converter accepts as KJV-numbered.
KJV_COMPATIBLE = {'', 'kjv', 'kjva'}


def load_map_rules(name):
    """Rules of data/versification-<name>-to-kjv.json, then those of the map it
    "extends": the converter's order, where the first matching rule wins."""
    path = os.path.join(SWORD_DIR, 'data', f'versification-{name}-to-kjv.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except OSError:
        return []
    rules = list(data.get('rules', []))
    base = (data.get('extends') or '').lower()
    if base and base != name:
        rules += load_map_rules(base)
    return rules


# Systems the converter translates with an external map (after its aliases).
EXTERNAL_MAPS = {'mt': 'mt', 'leningrad': 'mt', 'german': 'german', 'luther': 'german'}
_MAP_CACHE = {}


def external_rules(vers):
    name = EXTERNAL_MAPS[vers]
    if name not in _MAP_CACHE:
        by_book = defaultdict(list)
        for r in load_map_rules(name):
            by_book[r['b']].append(r)
        _MAP_CACHE[name] = by_book
    return _MAP_CACHE[name]


MT_RULES = external_rules('mt')

# VARIANT_VERSE_MAP in sword2bible.cpp: (book, src ch, src v) -> (host ch, host v)
VARIANT_VERSE_MAP = {(66, 12, 18): (13, 1), (64, 1, 15): (1, 14)}

# Verses the critical text omits; a translation lacking them is not a defect.
KNOWN_OMISSIONS = {
    vid(40, 12, 47), vid(40, 17, 21), vid(40, 18, 11), vid(40, 23, 14),
    vid(41, 7, 16), vid(41, 9, 44), vid(41, 9, 46), vid(41, 11, 26), vid(41, 15, 28),
    vid(42, 17, 36), vid(42, 23, 17), vid(43, 5, 4), vid(44, 8, 37), vid(44, 15, 34),
    vid(44, 24, 7), vid(44, 28, 29), vid(45, 16, 24),
}
KNOWN_OMISSIONS |= {vid(41, 16, v) for v in range(9, 21)}      # longer ending of Mark
KNOWN_OMISSIONS |= {vid(43, 7, 53)} | {vid(43, 8, v) for v in range(1, 12)}  # pericope adulterae

# Where red letter is expected at all.
RED_LETTER_BOOKS = {40, 41, 42, 43, 44, 46, 47, 66}

# Well-known verses printed in every report, for a reviewer to eyeball.
KEY_VERSES = [
    'Gen 1:1', 'Gen 2:4', 'Gen 31:55', 'Exod 20:2', 'Ps 3:1', 'Ps 23:1', 'Ps 51:1',
    'Isa 9:6', 'Dan 3:30', 'Esth 10:3', 'Joel 2:28', 'Mal 4:5', 'Matt 5:3',
    'Matt 17:21', 'Mark 16:9', 'Luke 2:14', 'John 1:1', 'John 3:16', 'John 8:1',
    'John 11:35', 'Acts 8:37', 'Rom 8:28', '1 Cor 11:24', '1 John 5:7', '3 John 1:14',
    'Rev 12:17', 'Rev 13:1', 'Rev 22:21',
]

SPAN_TYPES = {'divine_name', 'supplied', 'words_of_christ', 'emphasis', 'quotation',
              'transliteration'}
BLOCK_KEYS = {'paragraph_start', 'poetry_level', 'heading', 'heading_kind', 'selah'}

# ---------------------------------------------------------------------------
# Text hygiene: patterns that must not appear in clean canonical text.
# severity: error = violates the format's clean-text rule for bible_verse.text; warn = suspicious; info = count only
# ---------------------------------------------------------------------------
BAD_PATTERNS = [
    ('html_tag', 'error', re.compile(r'<\s*/?\s*[A-Za-z!][^<>]{0,200}>')),
    ('angle_bracket', 'warn', re.compile(r'[<>]')),
    ('entity', 'error', re.compile(r'&(?:[A-Za-z]{2,8}|#\d{2,7}|#[xX][0-9A-Fa-f]{2,6});')),
    ('pilcrow', 'error', re.compile('\u00b6')),
    ('backslash', 'error', re.compile(r'\\')),
    ('usfm_attribute', 'error', re.compile(r'\|\s*[a-z-]+\s*=\s*"')),
    ('strongs_tag', 'error', re.compile(r'\b(?:strong|lemma|morph|robinson|strongMorph):\S')),
    ('strongs_number', 'warn', re.compile(r'(?<![A-Za-z0-9])[GH]\d{3,5}(?![0-9])')),
    ('braces', 'warn', re.compile(r'[{}]')),
    ('control_char', 'error', re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]')),
    ('replacement_char', 'error', re.compile('\ufffd')),
    ('mojibake', 'error', re.compile('Ã[\u0080-\u00bf]|â€|Â[\u00a0-\u00bf]|Ð[\u0080-\u00bf]')),
    ('whitespace', 'error', re.compile(r'^\s|\s$|\s\s|[\t\n\r]')),
    ('odd_space', 'warn', re.compile('[\u00a0\u2000-\u200a\u202f\u205f\u3000]')),
    ('zero_width', 'info', re.compile('[\u200b\u2060\ufeff]')),
    ('private_use', 'warn', re.compile('[\ue000-\uf8ff]')),
    ('note_marker', 'info', re.compile('[*\u2020\u2021]')),
    ('double_bracket', 'info', re.compile(r'\[\[|\]\]')),
]

# Judgement thresholds. Tune here, not in the checks.
THRESH = {
    'missing_ratio_fail': 0.002,      # source text for an id that the DB lacks
    'missing_abs_fail': 3,
    'diff_ratio_warn': 0.01,          # verses whose text differs materially
    'diff_ratio_fail': 0.05,
    'similar_ratio': 0.90,            # below this a verse "differs materially"
    'word_delta_warn': 0.01,          # total word count, source vs DB (matched ids)
    'word_delta_fail': 0.03,
    'red_agree_warn': 0.97,
    'red_agree_fail': 0.85,
    'overlong_factor': 3.0,           # vs the reference translation, after scaling
    'overlong_min_extra_chars': 80,
    'shift_gain': 0.30,               # correlation gain at +/-1 that signals a shift
    'shift_max_r0': 0.60,
    'shift_min_best': 0.70,           # the shifted alignment must itself be convincing
}


TAG_RE = re.compile(r'<\s*/?\s*[A-Za-z][^<>]{0,300}>')


def similarity(a, b):
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < 0.5:
        return sm.real_quick_ratio()
    if sm.quick_ratio() < 0.5:
        return sm.quick_ratio()
    return sm.ratio()


def count_words(s):
    """Whitespace tokens that carry a letter or digit: libsword's plain text wraps
    supplied words in standalone asterisks ("* это *"), which are not words."""
    return sum(1 for t in s.split() if any(ch.isalnum() for ch in t))


def norm_text(s):
    """Letters, digits and marks only, case-folded: what fidelity is judged on."""
    s = unicodedata.normalize('NFC', s).casefold()
    return ''.join(ch for ch in s if unicodedata.category(ch)[0] in 'LNM')


def dominant_script(texts, limit=3000):
    counts = Counter()
    for t in texts[:limit]:
        for ch in t:
            if ch.isalpha():
                name = unicodedata.name(ch, '')
                counts[name.split(' ')[0] if name else '?'] += 1
    total = sum(counts.values()) or 1
    return [(k, round(v / total, 3)) for k, v in counts.most_common(3)]


EXPECTED_SCRIPT = {}
for _l in ('en de fr es it pt nl sv da no nb nn fi et lv lt pl cs sk sl hr hu ro sq tr id ms '
           'tl vi sw la eo ga cy is mt af eu ca gl').split():
    EXPECTED_SCRIPT[_l] = 'LATIN'
for _l in 'ru uk bg be mk kk ky tg mn cu'.split():
    EXPECTED_SCRIPT[_l] = 'CYRILLIC'
EXPECTED_SCRIPT.update({'el': 'GREEK', 'grc': 'GREEK', 'he': 'HEBREW', 'hbo': 'HEBREW',
                        'ar': 'ARABIC', 'fa': 'ARABIC', 'ur': 'ARABIC', 'zh': 'CJK',
                        'ja': 'CJK', 'ko': 'HANGUL', 'hy': 'ARMENIAN', 'ka': 'GEORGIAN',
                        'hi': 'DEVANAGARI', 'mr': 'DEVANAGARI', 'ne': 'DEVANAGARI',
                        'th': 'THAI', 'am': 'ETHIOPIC', 'syr': 'SYRIAC'})


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)



def strongs_sets(db_path):
    """verse_id -> set of Strong's numbers (leading zeros dropped: H07225 -> H7225)."""
    out = defaultdict(set)
    con = open_db(db_path)
    try:
        for v, sn in con.execute('SELECT verse_id, strongs_number FROM interlinear_word '
                                 'WHERE strongs_number IS NOT NULL'):
            m = re.match(r'^([GHgh])0*(\d+)', sn or '')
            if m:
                out[v].add(m.group(1).upper() + m.group(2))
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return out


def strongs_alignment(mod_sets, ref_sets):
    """
    Versification check by content: a verse whose Strong's numbers overlap a
    NEIGHBOURING reference verse clearly better than the reference verse with
    the same id sits at the wrong address. Needs no language knowledge, so it
    audits Hebrew/Greek modules and verse maps against the KJV.
    """
    freq = Counter(n for s in ref_sets.values() for n in s)
    stop = {n for n, _ in freq.most_common(40)}        # et, waw, 'the', 'and', ...

    def jac(a, b):
        a, b = a - stop, b - stop
        if not a or not b:
            return None
        return len(a & b) / len(a | b)
    scores, misplaced, disjoint = [], [], []
    for v, s in mod_sets.items():
        if v not in ref_sets or len(s - stop) < 3:
            continue
        j0 = jac(s, ref_sets[v])
        if j0 is None:
            continue
        scores.append(j0)
        best, where = j0, v
        for n in (v - 1, v + 1, v - 2, v + 2):
            if n // 1000 == v // 1000 and n in ref_sets:
                j = jac(s, ref_sets[n])
                if j is not None and j > best:
                    best, where = j, n
        if best - j0 >= 0.25 and j0 < 0.35:
            misplaced.append((v, where, round(j0, 2), round(best, 2)))
        elif j0 == 0:
            disjoint.append(v)
    return scores, misplaced, disjoint


def chapter_alignment(mod_len, ref_len):
    """Per-chapter Pearson correlation of verse lengths; chapters that fit better shifted by one verse."""
    by_chapter = defaultdict(dict)
    for v, n in mod_len.items():
        by_chapter[v // 1000][v % 1000] = n
    r0s, shifted, per_chapter = [], [], {}
    for ch, verses in by_chapter.items():
        common = [n for n in verses if ch * 1000 + n in ref_len]
        if len(common) < 8:
            continue
        ref_of = lambda n: ref_len.get(ch * 1000 + n)  # noqa: E731
        r0 = pearson([verses[n] for n in common], [ref_of(n) for n in common])
        if r0 is None:
            continue
        r0s.append(r0)
        per_chapter[ch] = r0
        best = r0
        for shift in (-1, 1):
            cc = [n for n in common if ref_of(n + shift)]
            r = pearson([verses[n] for n in cc], [ref_of(n + shift) for n in cc])
            if r is not None and r > best:
                best = r
        if best - r0 > THRESH['shift_gain'] and r0 < THRESH['shift_max_r0'] and best >= THRESH['shift_min_best']:
            shifted.append((ch, round(r0, 2), round(best, 2)))
    return r0s, shifted, per_chapter


def mt_hypothesis(mod_len, ref_len):
    """
    Re-read the module's verse numbers as Masoretic and move them with the MT
    map. Count the chapters whose alignment with the reference improves or
    worsens clearly. A module declared KJV but numbered like the Hebrew Bible
    (Gen 32:1 = KJV 31:55, Joel 3 = KJV 2:28-32, psalm titles as verse 1)
    improves in many chapters; a genuinely KJV-numbered one only worsens.
    """
    moved, hyp = set(), defaultdict(int)
    for v, n in mod_len.items():
        b, c, x = split_vid(v)
        c2, x2 = c, x
        for r in MT_RULES.get(b, ()):
            if r['c'] == c and r['from'] <= x <= r['to']:
                c2, x2 = r['dc'], max(1, x + r['off'])
                break
        if (c2, x2) != (c, x):
            moved.add((b, c))
            moved.add((b, c2))
        hyp[vid(b, c2, x2)] += n
    better, worse, examples = 0, 0, []
    for b, c in sorted(moved):
        ids = [vid(b, c, x) for x in range(1, 200) if vid(b, c, x) in ref_len]
        if len(ids) < 4:
            continue
        ys = [ref_len[i] for i in ids]
        ra = pearson([mod_len.get(i, 0) for i in ids], ys)
        rh = pearson([hyp.get(i, 0) for i in ids], ys)
        if ra is None or rh is None:
            continue
        if rh - ra > 0.3:
            better += 1
            examples.append(f'{ref_name(vid(b, c, 1))[:-2]} {ra:.2f}->{rh:.2f}')
        elif ra - rh > 0.3:
            worse += 1
    return better, worse, examples

def clip(s, n=300):
    s = s if isinstance(s, str) else str(s)
    return s if len(s) <= n else s[:n] + '...'


# ---------------------------------------------------------------------------
# External programs
# ---------------------------------------------------------------------------
def require_binary(path):
    if not os.path.exists(path):
        raise SystemExit(f'{path} is missing; build it with make (see tools/import/sword/verify/README.md)')


def probe_info(zip_path, module=None):
    require_binary(PROBE)
    cmd = [PROBE, '-i', zip_path] + (['--module', module] if module else []) + ['info']
    p = subprocess.run(cmd, capture_output=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f'sword-probe info failed: {p.stderr.decode("utf-8", "replace")[:500]}')
    return json.loads(p.stdout.decode('utf-8', 'replace'))


def probe_dump(zip_path, module=None, keep_path=None):
    require_binary(PROBE)
    cmd = [PROBE, '-i', zip_path] + (['--module', module] if module else []) + ['dump']
    p = subprocess.run(cmd, capture_output=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(f'sword-probe dump failed: {p.stderr.decode("utf-8", "replace")[:500]}')
    out = p.stdout.decode('utf-8', 'replace')
    if keep_path:
        with open(keep_path, 'w', encoding='utf-8') as f:
            f.write(out)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def open_db(path):
    # immutable=1: read without creating -wal/-shm files, never write.
    con = sqlite3.connect(f'file:{os.path.abspath(path)}?immutable=1', uri=True)
    # Invalid UTF-8 in a converted module is a finding (U+FFFD below), not a crash.
    con.text_factory = lambda raw: raw.decode('utf-8', 'replace')
    return con


def run_validator(db_path):
    if not os.path.exists(VALIDATOR) or not shutil.which('node'):
        return {'skipped': 'node or validate-module.js not available'}
    if not os.path.isdir(os.path.join(REPO, 'node_modules', 'sqlite3')):
        return {'skipped': 'run npm install in the repo root first'}
    p = subprocess.run(['node', VALIDATOR, os.path.abspath(db_path), '--json'],
                       capture_output=True, timeout=600, cwd=REPO)
    try:
        data = json.loads(p.stdout.decode('utf-8', 'replace'))
        res = data['results'][0]
        return {'ok': res.get('ok'),
                'errors': [f"{e.get('code')}: {clip(e.get('message', ''), 240)}" for e in res.get('errors', [])],
                'warnings': [f"{e.get('code')}: {clip(e.get('message', ''), 240)}" for e in res.get('warnings', [])]}
    except Exception as exc:  # noqa: BLE001 - report, never crash the batch
        return {'skipped': f'validator output unreadable ({exc}); stderr: {p.stderr[:300]!r}'}


def parse_convert_log(path):
    info = {'warnings': [], 'errors': [], 'skipped_books': {}}
    if not path or not os.path.exists(path):
        return info
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.read().splitlines()
    in_skipped = False
    for line in lines:
        s = line.strip()
        for key, pat in (('verses_converted', r'Total verses converted: (\d+)'),
                         ('remapped', r'Versification remapped: (\d+)'),
                         ('dropped', r'Versification dropped: (\d+)'),
                         ('variant_merges', r'Variant verse divisions merged: (\d+)'),
                         ('interlinear', r'Interlinear words: (\d+)')):
            m = re.search(pat, s)
            if m:
                info[key] = int(m.group(1))
        if 'canon were skipped' in s:
            in_skipped = True
            continue
        if in_skipped:
            m = re.match(r'^(\S+) \((\d+) verses\)$', s)
            if m:
                info['skipped_books'][m.group(1)] = int(m.group(2))
                continue
            in_skipped = False
        if s.startswith('WARNING') and 'canon were skipped' not in s:
            info['warnings'].append(clip(s, 240))
        elif s.startswith('ERROR') or s.startswith('Error') or 'Exception' in s:
            info['errors'].append(clip(s, 240))
        elif s.startswith('Duplicate verse') or 'Detected verse duplication' in s:
            info['warnings'].append(clip(s, 240))
    info['warnings'] = info['warnings'][:30]
    info['errors'] = info['errors'][:30]
    return info


# ---------------------------------------------------------------------------
# Source -> expected verse id (mirrors sword2bible.cpp's decisions, so that a
# disagreement means the converter did something other than what it intends)
# ---------------------------------------------------------------------------
def source_chapter_max(recs):
    out = {}
    for r in recs:
        k = (r.get('book'), r.get('c'))
        out[k] = max(out.get(k, 0), r.get('v', 0))
    return out


def expected_verse_id(rec, versification, src_max=None):
    book = canon_book(rec.get('book', ''))
    if not book:
        return None, 'outside canon'
    c, v = rec.get('c', 0), rec.get('v', 0)
    if c <= 0 or v <= 0:
        return None, 'intro'
    vers = versification.lower()
    remapped = False
    if vers in LIBSWORD_MAPPED:
        kjv = rec.get('kjv') or ''
        parts = kjv.split('.')
        if len(parts) != 3:
            return None, 'no KJV mapping'
        book2 = canon_book(parts[0])
        if not book2:
            return None, 'maps outside canon'
        c2, v2 = int(parts[1]), int(parts[2])
        remapped = (book2, c2, v2) != (book, c, v)
        book, c, v = book2, c2, v2
    elif vers in EXTERNAL_MAPS:
        for r in external_rules(vers).get(book, ()):
            if r['c'] == c and r['from'] <= v <= r['to']:
                remapped = (r['dc'], v + r['off']) != (c, v)
                c, v = r['dc'], v + r['off']
                break
    if v <= 0 and c > 0:
        v = 1
    if c <= 0:
        return None, 'no KJV mapping'
    if not remapped and (book, c, v) in VARIANT_VERSE_MAP:
        c, v = VARIANT_VERSE_MAP[(book, c, v)]
    counts = KJV_COUNTS[book]
    if c > len(counts):
        return None, 'chapter beyond canonical book (addition)'
    if v > counts[c - 1]:
        # Mirrors sword2bible's translateReferenceToKjv(): an unmoved verse in a
        # source chapter running 3+ verses past the KJV chapter is an addition
        # and is dropped; anything else is folded onto the chapter's last verse.
        smax = (src_max or {}).get((rec.get('book'), rec.get('c')), 0)
        if vers in LIBSWORD_MAPPED and not remapped and smax - counts[c - 1] >= 3:
            return None, 'addition past the chapter end'
        return vid(book, c, counts[c - 1]), 'folded past the chapter end'
    return vid(book, c, v), None


# ---------------------------------------------------------------------------
# Bible verification
# ---------------------------------------------------------------------------
class Findings:
    def __init__(self):
        self.items = []    # (severity, code, message)

    def add(self, severity, code, message):
        self.items.append({'severity': severity, 'code': code, 'message': message})

    def status(self):
        sev = {i['severity'] for i in self.items}
        return 'FAIL' if 'error' in sev else ('WARN' if 'warn' in sev else 'PASS')


def scan_patterns(rows, severity_cap=None):
    """rows: iterable of (key, text). Returns {name: {'severity', 'rows', 'examples'}}."""
    hits = {}
    for key, text in rows:
        if not text:
            continue
        for name, severity, rx in BAD_PATTERNS:
            if name == 'angle_bracket' and 'html_tag' in hits and key in hits['html_tag']['_keys']:
                continue
            m = rx.search(text)
            if not m:
                continue
            h = hits.setdefault(name, {'severity': severity, 'rows': 0, 'examples': [], '_keys': set()})
            h['rows'] += 1
            h['_keys'].add(key)
            if len(h['examples']) < 5:
                lo = max(0, m.start() - 40)
                h['examples'].append({'key': key, 'match': m.group(0), 'context': text[lo:m.end() + 40]})
    for h in hits.values():
        h['_keys'] = h['_keys']
    return hits


def verify_bible(zip_path, db_path, log_path=None, reference_db=None, catalog_row=None,
                 module=None, keep_dump=None, seed=1, run_validate=True, versification_override=None):
    t0 = time.time()
    F = Findings()
    report = OrderedDict()
    report['module'] = module or os.path.splitext(os.path.basename(zip_path))[0]
    report['zip'] = zip_path
    report['db'] = db_path

    info = probe_info(zip_path, module)
    versification = versification_override or info.get('versification', '') or 'KJV'
    conf = info.get('conf', {})
    report['source'] = OrderedDict([
        ('name', info.get('name')), ('description', info.get('description')),
        ('versification', versification),
        ('declared_versification', info.get('versification', '')), ('source_type', conf.get('SourceType', '')),
        ('encoding', conf.get('Encoding', '')), ('lang', conf.get('Lang', '')),
        ('features', conf.get('Feature', '')), ('global_option_filters', conf.get('GlobalOptionFilter', '')),
        ('direction', conf.get('Direction', '')), ('version', conf.get('Version', '')),
    ])
    if catalog_row:
        report['catalog'] = {k: catalog_row.get(k) for k in (
            'repository', 'module_id', 'language_name', 'app_language_code', 'right_to_left',
            'versification_sword', 'source_markup', 'scope', 'conversion_license_verdict',
            'license_class', 'issues')}

    recs = probe_dump(zip_path, module, keep_dump)
    conv = parse_convert_log(log_path)
    report['conversion_log'] = conv

    # --- group source entries by the verse id they should land on ----------
    groups = OrderedDict()
    reasons = Counter()
    noncanon_books = Counter()
    folds = defaultdict(list)       # verse id -> source refs folded onto it past the chapter end
    linked_ids = set()
    canon_books_in_source = set()
    linked = 0
    latin1 = 0
    src_raw_features = Counter()
    src_max = source_chapter_max(recs)
    dropped_additions = Counter()
    for r in recs:
        if r.get('linked'):
            linked += 1
        if r.get('raw_latin1'):
            latin1 += 1
        raw = r.get('raw', '')
        for feat, pat in (('osis_q_jesus', 'who="Jesus"'), ('gbf_red', '<FR>'),
                          ('note', '<note'), ('gbf_note', '<RF>'), ('strongs', 'strong:'),
                          ('gbf_strongs', '<WH'), ('gbf_strongs_g', '<WG'),
                          ('thml_strongs', 'type="Strongs"'), ('title', '<title'),
                          ('divine_name', '<divineName'), ('trans_change', '<transChange'),
                          ('paragraph_x_p', 'x-p'), ('pilcrow', '\u00b6'), ('poetry_l', '<l ')):
            if pat in raw:
                src_raw_features[feat] += 1
        verse_id, why = expected_verse_id(r, versification, src_max)
        if verse_id is None:
            reasons[why] += 1
            if why == 'addition past the chapter end':
                dropped_additions[f"{r.get('book')} {r.get('c')}"] += 1
            if why == 'outside canon':
                noncanon_books[r.get('book', '?')] += 1
            continue
        if r.get('linked'):
            linked_ids.add(verse_id)      # stored once, on the first verse of the range
            continue
        if why:
            folds[verse_id].append(r.get('osis'))
        canon_books_in_source.add(verse_id // 1000000)
        g = groups.setdefault(verse_id, {'plain': [], 'red': False, 'refs': [], 'raw': []})
        # libsword's plain text of a plaintext-markup module still carries the
        # <chapter eID=.../> milestones; those are not text on either side.
        plain = ' '.join(TAG_RE.sub(' ', r.get('plain') or '').split())
        if plain:
            g['plain'].append(plain)
        g['red'] = g['red'] or bool(r.get('red'))
        g['refs'].append(r.get('osis') or r.get('key'))
        if len(g['raw']) < 3:
            g['raw'].append(raw)
        g.setdefault('raw_text', []).append(html.unescape(TAG_RE.sub(' ', raw)))

    # --- read the DB -----------------------------------------------------------
    db = open_db(db_path)
    cur = db.cursor()
    tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'bible_verse' not in tables:
        F.add('error', 'no_bible_verse', 'database has no bible_verse table')
        report['findings'] = F.items
        report['status'] = F.status()
        return report
    mi = {}
    try:
        cur.execute('SELECT * FROM module_info WHERE info_id = 1')
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        mi = dict(zip(cols, row)) if row else {}
    except sqlite3.Error as exc:
        F.add('error', 'module_info', f'module_info unreadable: {exc}')
    db_rows = OrderedDict()
    for verse_id, text, formatting, wc in cur.execute(
            'SELECT verse_id, text, formatting, word_count FROM bible_verse ORDER BY verse_id'):
        db_rows[verse_id] = (text or '', formatting, wc)
    interlinear_rows = cur.execute('SELECT COUNT(*) FROM interlinear_word').fetchone()[0] \
        if 'interlinear_word' in tables else 0
    dup_adjacent = cur.execute(
        'SELECT a.verse_id, b.verse_id, a.text FROM bible_verse a JOIN bible_verse b '
        'ON b.verse_id = a.verse_id + 1 AND a.text = b.text AND length(a.text) > 20').fetchall()
    db.close()

    # --- counts ------------------------------------------------------------------
    src_ids = {k for k, g in groups.items() if g['plain']}
    empty_src_ids = {k for k, g in groups.items() if not g['plain']}
    db_ids = set(db_rows)
    missing = sorted(src_ids - db_ids)
    extra = sorted(db_ids - src_ids - empty_src_ids)
    report['counts'] = OrderedDict([
        ('source_entries', len(recs)),
        ('source_entries_in_canon', sum(len(g['refs']) for g in groups.values())),
        ('source_not_imported_by_reason', dict(reasons)),
        ('source_noncanonical_books', dict(noncanon_books)),
        ('source_linked_entries', linked),
        ('linked_verse_ids_not_stored', len(linked_ids)),
        ('source_latin1_entries', latin1),
        ('expected_verse_ids', len(src_ids)),
        ('source_ids_with_empty_text', len(empty_src_ids)),
        ('db_verses', len(db_rows)),
        ('missing_in_db', len(missing)),
        ('extra_in_db', len(extra)),
        ('merged_ids', sum(1 for g in groups.values() if len(g['refs']) > 1)),
    ])
    missing_known = [m for m in missing if m in KNOWN_OMISSIONS]
    missing_real = [m for m in missing if m not in KNOWN_OMISSIONS]
    if missing_real:
        ratio = len(missing_real) / max(1, len(src_ids))
        sev = 'error' if (len(missing_real) >= THRESH['missing_abs_fail'] or ratio > THRESH['missing_ratio_fail']) else 'warn'
        F.add(sev, 'missing_in_db',
              f'{len(missing_real)} verse(s) have source text but no DB row, e.g. '
              + ', '.join(ref_name(m) for m in missing_real[:8]))
    if missing_known:
        F.add('warn', 'missing_known_omission',
              f'{len(missing_known)} textual-variant verse(s) present in the source are missing from the DB: '
              + ', '.join(ref_name(m) for m in missing_known[:8]))
    if extra:
        F.add('error', 'extra_in_db', f'{len(extra)} DB verse(s) have no source text, e.g. '
              + ', '.join(ref_name(m) for m in extra[:8]))
    if len(recs) == 0:
        F.add('error', 'source_empty', 'sword-probe read no entries from the module')

    # --- canon ------------------------------------------------------------------
    out_of_canon = [v for v in db_ids if not in_canon(v)]
    if out_of_canon:
        F.add('error', 'canon_violation', f'{len(out_of_canon)} verse id(s) outside the 66-book KJV '
              f'canon, e.g. {sorted(out_of_canon)[:5]}')
    db_books = {v // 1000000 for v in db_ids}
    lost_books = sorted(canon_books_in_source - db_books)
    if lost_books:
        F.add('error', 'canonical_book_lost', 'canonical book(s) in the source but absent from the DB: '
              + ', '.join(OSIS66[b - 1] for b in lost_books))
    per_book = OrderedDict()
    holes = []
    for b in sorted(db_books):
        counts = KJV_COUNTS[b]
        present = sum(1 for v in db_ids if v // 1000000 == b)
        expected = sum(counts)
        per_book[OSIS66[b - 1]] = {'db': present, 'kjv': expected}
        for c, n in enumerate(counts, start=1):
            for v in range(1, n + 1):
                x = vid(b, c, v)
                if x not in db_ids and x not in KNOWN_OMISSIONS and x not in linked_ids:
                    holes.append(x)
    partial = [k for k, v in per_book.items() if v['db'] < 0.9 * v['kjv']]
    report['canon'] = OrderedDict([
        ('db_books', len(db_books)),
        ('ot_books', sum(1 for b in db_books if b <= 39)),
        ('nt_books', sum(1 for b in db_books if b >= 40)),
        ('partial_books', partial),
        ('holes_in_present_books', len(holes)),
        ('holes_examples', [ref_name(h) for h in holes[:15]]),
        ('holes_also_missing_in_source', sum(1 for h in holes if h not in src_ids)),
        ('skipped_noncanonical_books', dict(noncanon_books)),
        ('module_versification', versification),
        ('folded_past_chapter_end', {ref_name(v): refs for v, refs in list(folds.items())[:25]}),
        ('dropped_additions_past_chapter_end', dict(dropped_additions)),
        ('per_book', per_book),
    ])
    if dropped_additions:
        F.add('info', 'addition_dropped', 'source verses past the KJV chapter end dropped as additions '
              '(66-book canon): ' + ', '.join(f'{k} ({n})' for k, n in dropped_additions.most_common(8)))
    big_folds = {v: refs for v, refs in folds.items() if len(refs) >= 3}
    if big_folds:
        F.add('warn', 'canon_fold', f'{len(big_folds)} verse(s) absorb 3+ source verses numbered past the KJV '
              'chapter end (deuterocanonical additions inside a canonical book?): '
              + '; '.join(f'{ref_name(v)} <- {refs[0]}..{refs[-1]} ({len(refs)})' for v, refs in list(big_folds.items())[:5]))
    elif folds:
        F.add('info', 'fold', f'{sum(len(r) for r in folds.values())} source verse(s) folded onto a chapter\'s last verse: '
              + ', '.join(f'{refs[0]}->{ref_name(v)}' for v, refs in list(folds.items())[:6]))
    lost_holes = [h for h in holes if h in src_ids]
    if versification.lower() not in KJV_COMPATIBLE | LIBSWORD_MAPPED | MT_MAPPED:
        F.add('warn', 'versification_unmapped',
              f'source versification {versification} is neither KJV nor mapped; verse numbers may be shifted')
    if holes:
        sev = 'info' if (partial or len(holes) > 200) else 'warn'
        F.add(sev, 'holes', f'{len(holes)} canonical verse(s) missing inside books the module has '
              f'({len(lost_holes)} with source text, {len(holes) - len(lost_holes)} absent from the source '
              'or not mapped there), e.g. ' + ', '.join(ref_name(h) for h in holes[:10]))

    # --- per-verse text fidelity -----------------------------------------------
    exact = minor = differ = 0
    word_src = word_db = 0
    diffs = []
    for verse_id in sorted(src_ids & db_ids):
        src_text = ' '.join(groups[verse_id]['plain'])
        db_text = db_rows[verse_id][0]
        word_src += count_words(src_text)
        word_db += count_words(db_text)
        a, b = norm_text(src_text), norm_text(db_text)
        if a == b:
            exact += 1
            continue
        sim = similarity(a, b)
        if sim >= THRESH['similar_ratio']:
            minor += 1
        else:
            differ += 1
        diffs.append((sim, verse_id))
    matched = max(1, len(src_ids & db_ids))
    diffs.sort()
    word_delta = (word_db - word_src) / max(1, word_src)
    report['fidelity'] = OrderedDict([
        ('matched_verses', len(src_ids & db_ids)),
        ('exact_after_normalising', exact),
        ('minor_differences', minor),
        ('material_differences', differ),
        ('exact_ratio', round(exact / matched, 4)),
        ('words_source_plain', word_src),
        ('words_db', word_db),
        ('word_delta_ratio', round(word_delta, 5)),
        ('worst', [{'ref': ref_name(v), 'verse_id': v, 'similarity': round(s, 3),
                    'source': clip(' '.join(groups[v]['plain'])), 'db': clip(db_rows[v][0])}
                   for s, v in diffs[:12]]),
    ])
    dr = differ / matched
    if dr > THRESH['diff_ratio_fail']:
        F.add('error', 'text_differs', f'{differ} verse(s) ({dr:.1%}) differ materially from libsword\'s plain text')
    elif dr > THRESH['diff_ratio_warn']:
        F.add('warn', 'text_differs', f'{differ} verse(s) ({dr:.1%}) differ materially from libsword\'s plain text')
    elif differ:
        F.add('info', 'text_differs', f'{differ} verse(s) differ materially from libsword\'s plain text')
    if abs(word_delta) > THRESH['word_delta_fail']:
        F.add('error', 'word_count_delta', f'DB has {word_delta:+.2%} words vs the source plain text ({word_db} vs {word_src})')
    elif abs(word_delta) > THRESH['word_delta_warn']:
        F.add('warn', 'word_count_delta', f'DB has {word_delta:+.2%} words vs the source plain text ({word_db} vs {word_src})')

    # --- formatting + red letter --------------------------------------------------
    span_counts = Counter()
    block_counts = Counter()
    fmt_errors = []
    red_db = set()
    red_outside = Counter()
    word_count_bad = 0
    headings = []
    source_verse_rows = 0
    for verse_id, (text, formatting, wc) in db_rows.items():
        tokens = len(text.split(' ')) if text else 0
        if wc is not None and wc != tokens:
            word_count_bad += 1
        if not formatting:
            continue
        try:
            doc = json.loads(formatting)
        except ValueError:
            fmt_errors.append(f'{ref_name(verse_id)}: invalid JSON')
            continue
        unknown = set(doc) - {'v', 'block', 'spans', 'source_verses'}
        if unknown or doc.get('v') != 1:
            fmt_errors.append(f'{ref_name(verse_id)}: unexpected keys/version {sorted(unknown)} v={doc.get("v")}')
        block = doc.get('block') or {}
        for k, val in block.items():
            block_counts[k] += 1
            if k not in BLOCK_KEYS:
                fmt_errors.append(f'{ref_name(verse_id)}: unknown block key {k}')
        if block.get('heading'):
            headings.append((verse_id, block['heading']))
        if 'poetry_level' in block and block['poetry_level'] not in (1, 2, 3):
            fmt_errors.append(f'{ref_name(verse_id)}: poetry_level {block["poetry_level"]}')
        if doc.get('source_verses'):
            source_verse_rows += 1
        for s in doc.get('spans') or []:
            t = s.get('type')
            span_counts[t] += 1
            if t not in SPAN_TYPES:
                fmt_errors.append(f'{ref_name(verse_id)}: unknown span type {t}')
            st, en = s.get('start'), s.get('end')
            if not isinstance(st, int) or not isinstance(en, int) or not (0 <= st <= en < max(tokens, 1)):
                fmt_errors.append(f'{ref_name(verse_id)}: span {t} {st}..{en} outside 0..{tokens - 1}')
            if t == 'quotation' and s.get('ref') and not in_canon(int(s['ref'])):
                fmt_errors.append(f'{ref_name(verse_id)}: quotation ref {s["ref"]} not a canonical verse')
            if t == 'words_of_christ':
                red_db.add(verse_id)
                if verse_id // 1000000 not in RED_LETTER_BOOKS:
                    red_outside[OSIS66[verse_id // 1000000 - 1]] += 1
    if fmt_errors:
        F.add('error', 'formatting_invalid', f'{len(fmt_errors)} formatting problem(s), e.g. ' + '; '.join(fmt_errors[:5]))
    if word_count_bad:
        F.add('error', 'word_count_column', f'{word_count_bad} row(s) where word_count != tokens of text')

    red_src = {v for v in src_ids if groups[v]['red']}
    both = red_src & red_db
    src_only = sorted(red_src - red_db)
    db_only = sorted(red_db - red_src)
    union = red_src | red_db
    agree = len(both) / len(union) if union else 1.0
    report['red_letter'] = OrderedDict([
        ('source_verses_red', len(red_src)), ('db_verses_red', len(red_db)),
        ('both', len(both)), ('source_only', len(src_only)), ('db_only', len(db_only)),
        ('agreement', round(agree, 4)),
        ('source_only_examples', [ref_name(v) for v in src_only[:10]]),
        ('db_only_examples', [ref_name(v) for v in db_only[:10]]),
        ('db_red_outside_expected_books', dict(red_outside)),
        ('source_markup_hits', {k: src_raw_features[k] for k in ('osis_q_jesus', 'gbf_red')}),
    ])
    if union:
        if not red_db and red_src:
            F.add('error', 'red_letter_lost', f'source has {len(red_src)} red-letter verses, the DB none')
        elif agree < THRESH['red_agree_fail']:
            F.add('error', 'red_letter_mismatch', f'red-letter agreement {agree:.1%} ({len(src_only)} source-only, {len(db_only)} DB-only)')
        elif agree < THRESH['red_agree_warn']:
            F.add('warn', 'red_letter_mismatch', f'red-letter agreement {agree:.1%} ({len(src_only)} source-only, {len(db_only)} DB-only)')
    if red_outside:
        # Red letter the source itself marks there (WEB-family 1 Tim 5:18
        # quoting Luke 10:7) is inherited; only red letter the DB adds is suspect.
        added = Counter(OSIS66[v // 1000000 - 1] for v in db_only if v // 1000000 not in RED_LETTER_BOOKS)
        msg = ('words_of_christ spans outside the Gospels/Acts/1-2 Cor/Rev: '
               + ', '.join(f'{k} {v}' for k, v in red_outside.most_common(6)))
        if added:
            F.add('warn', 'red_letter_unexpected_books', msg + f' ({sum(added.values())} not red in the source)')
        else:
            F.add('info', 'red_letter_unexpected_books', msg + ' (red in the source too)')

    report['formatting'] = OrderedDict([
        ('rows_with_formatting', sum(1 for r in db_rows.values() if r[1])),
        ('span_types', dict(span_counts)), ('block_fields', dict(block_counts)),
        ('rows_with_source_verses', source_verse_rows),
        ('headings', len(headings)),
        ('heading_examples', [f'{ref_name(v)}: {clip(h, 120)}' for v, h in headings[:6]]),
        ('source_markup_features', dict(src_raw_features)),
        ('interlinear_rows', interlinear_rows),
        ('errors', fmt_errors[:20]),
    ])
    if src_raw_features.get('title', 0) > 50 and not headings:
        F.add('warn', 'headings_lost', f'source has {src_raw_features["title"]} entries with <title>, DB has no headings')
    if src_raw_features.get('strongs', 0) > 1000 and interlinear_rows == 0:
        F.add('warn', 'strongs_lost', f'source has Strong\'s tags in {src_raw_features["strongs"]} entries, DB has no interlinear rows')

    # --- hygiene ------------------------------------------------------------------
    db_hits = scan_patterns((v, r[0]) for v, r in db_rows.items())
    head_hits = scan_patterns((v, h) for v, h in headings)
    # A pattern counts as inherited when libsword's plain text OR the source's own
    # (entity-decoded, tag-stripped) raw text carries it: modules that escape
    # stray markup ("3,20</reference>&gt;") show it only in the raw.
    src_hits = scan_patterns((v, ' '.join(g['plain']) + ' \u2063 ' + ' '.join(g.get('raw_text', [])))
                             for v, g in groups.items())
    hygiene = OrderedDict()
    for name, severity, _ in BAD_PATTERNS:
        if name not in db_hits and name not in head_hits:
            continue
        h = db_hits.get(name) or {'rows': 0, 'examples': []}
        hh = head_hits.get(name) or {'rows': 0, 'examples': []}
        inherited = (src_hits.get(name) or {}).get('rows', 0)
        introduced = len((h.get('_keys') or set()) - ((src_hits.get(name) or {}).get('_keys') or set()))
        hygiene[name] = {'severity': severity, 'db_rows': h['rows'], 'introduced_by_conversion': introduced,
                         'source_rows': inherited, 'heading_rows': hh['rows'],
                         'examples': [{'ref': ref_name(e['key']), 'match': e['match'], 'context': e['context']}
                                      for e in (h['examples'] + hh['examples'])[:5]]}
        if severity == 'info':
            continue
        if introduced or hh['rows']:
            sev = 'error' if severity == 'error' else 'warn'
            F.add(sev, f'text_{name}', f'{name}: {h["rows"]} verse(s) ({introduced} introduced by conversion, '
                  f'{inherited} in the source) + {hh["rows"]} heading(s)')
        elif h['rows']:
            F.add('warn', f'text_{name}_inherited', f'{name}: {h["rows"]} verse(s), all inherited from the source text')
    report['hygiene'] = hygiene

    # verse-number prefixes that leaked into the text
    num_prefix = [v for v, r in db_rows.items() if re.match(rf'^\(?{v % 1000}\)?[\s.:]', r[0])]
    if len(num_prefix) > 20:
        F.add('warn', 'verse_number_in_text', f'{len(num_prefix)} verse(s) start with their own verse number, e.g. '
              + ', '.join(ref_name(v) for v in num_prefix[:5]))
    empties = [v for v, r in db_rows.items() if not r[0].strip()]
    if empties:
        F.add('error', 'empty_verse', f'{len(empties)} empty verse(s)')
    if dup_adjacent:
        F.add('warn' if len(dup_adjacent) < 20 else 'error', 'duplicate_adjacent',
              f'{len(dup_adjacent)} pair(s) of consecutive verses with identical text '
              f'(SWORD linked entries: {linked}), e.g. ' + ', '.join(ref_name(a) for a, _, _ in dup_adjacent[:5]))

    # --- versification alignment against a reference translation --------------------
    align = OrderedDict()
    overlong = []
    chapter_r0 = {}
    shifted = []
    if reference_db and os.path.exists(reference_db) and os.path.abspath(reference_db) != os.path.abspath(db_path):
        # Character counts, not word counts: scripts written without spaces
        # (Chinese, Thai, ...) would otherwise have one "word" per verse. The
        # reference keeps psalm titles in block.heading while many modules keep
        # them in the text, so its heading characters count too.
        rdb = open_db(reference_db)
        ref_len = {}
        for v, text, fmt in rdb.execute('SELECT verse_id, text, formatting FROM bible_verse'):
            heading = ''
            if fmt and '"heading"' in fmt:
                try:
                    heading = (json.loads(fmt).get('block') or {}).get('heading') or ''
                except ValueError:
                    pass
            ref_len[v] = len((text or '').replace(' ', '')) + len(heading.replace(' ', ''))
        rdb.close()
        mod_len = {v: len(r[0].replace(' ', '')) for v, r in db_rows.items()}
        ratios = sorted(mod_len[v] / ref_len[v] for v in db_ids if ref_len.get(v))
        scale = statistics.median(ratios) if ratios else 1.0
        r0s, shifted, chapter_r0 = chapter_alignment(mod_len, ref_len)
        for v in db_ids:
            rl = ref_len.get(v)
            w = mod_len[v]
            if rl and w > THRESH['overlong_factor'] * scale * rl and w - scale * rl > THRESH['overlong_min_extra_chars']:
                overlong.append((w / (scale * rl), v))
        overlong.sort(reverse=True)
        align = OrderedDict([
            ('reference', os.path.basename(reference_db)),
            ('length_ratio_vs_reference', round(scale, 3)),
            ('chapters_compared', len(r0s)),
            ('median_chapter_correlation', round(statistics.median(r0s), 3) if r0s else None),
            ('chapters_low_correlation', sum(1 for r in r0s if r < 0.3)),
            ('chapters_looking_shifted', [f'{ref_name(c * 1000 + 1)[:-2]} r0={a} best={b}' for c, a, b in shifted[:15]]),
            ('overlong_verses', [{'ref': ref_name(v), 'factor': round(f, 1), 'db': clip(db_rows[v][0], 200)}
                                 for f, v in overlong[:10]]),
        ])
        med = align['median_chapter_correlation']
        if med is not None and med < 0.5:
            F.add('warn', 'versification_alignment', f'median per-chapter verse-length correlation with the reference is {med}')
        if shifted:
            F.add('warn', 'versification_shift', f'{len(shifted)} chapter(s) look shifted by one verse against the reference, e.g. '
                  + '; '.join(align['chapters_looking_shifted'][:4]))
        if overlong:
            F.add('warn', 'overlong_verses', f'{len(overlong)} verse(s) are {THRESH["overlong_factor"]}x+ longer than the reference '
                  '(folded additions?), e.g. ' + ', '.join(ref_name(v) for _, v in overlong[:5]))
        # Is the text really numbered like the Hebrew Bible, whatever the .conf says?
        if versification.lower() in KJV_COMPATIBLE | {'nrsv', 'nrsva'} and MT_RULES:
            better, worse, examples = mt_hypothesis(mod_len, ref_len)
            align['mt_numbering_better_chapters'] = better
            align['mt_numbering_worse_chapters'] = worse
            align['mt_numbering_examples'] = examples[:10]
            if better >= 3 and better > 3 * worse:
                F.add('warn', 'versification_looks_mt',
                      f'declared {versification}, but {better} chapter(s) align with the reference only when read as '
                      f'Hebrew (MT) numbering (vs {worse} against), e.g. {", ".join(examples[:4])}. If confirmed, convert '
                      'with --versification MT (tools/import/sword/data/versification-overrides.json)')
    # Strong's numbers: an independent, content-based versification check for
    # any module carrying them (original-language texts, KJV-family English).
    if reference_db and os.path.exists(reference_db) and interlinear_rows and \
            os.path.abspath(reference_db) != os.path.abspath(db_path):
        mod_sets = strongs_sets(db_path)
        ref_sets = strongs_sets(reference_db)
        if mod_sets and ref_sets:
            scores, misplaced, disjoint = strongs_alignment(mod_sets, ref_sets)
            align['strongs_verses_compared'] = len(scores)
            align['strongs_median_overlap'] = round(statistics.median(scores), 3) if scores else None
            align['strongs_misplaced'] = [f'{ref_name(v)} looks like {ref_name(w)} ({a} vs {b})'
                                          for v, w, a, b in sorted(misplaced)[:40]]
            align['strongs_misplaced_count'] = len(misplaced)
            align['strongs_no_overlap'] = [ref_name(v) for v in sorted(disjoint)[:20]]
            align['strongs_no_overlap_count'] = len(disjoint)
            # Misplaced TEXT is claimed only where the verse lengths say so too:
            # in a chapter the length check finds shifted. Elsewhere the text is
            # at the right address and only the Strong's tags are off (eBible
            # modules tag by Hebrew verse in the MT-difference chapters: RV_th,
            # engmsb2024eb, frasbl2022eb). Converting engmsb2024eb as MT to
            # "fix" its tags shifted 28 chapters of correctly placed text.
            shifted_chapters = {c for c, _, _ in shifted}
            text_bad = [m for m in misplaced if m[0] // 1000 in shifted_chapters]
            text_ok = [m for m in misplaced if m[0] // 1000 not in shifted_chapters]
            align['strongs_misplaced_in_aligned_chapters'] = len(text_ok)
            if text_bad:
                sev = 'error' if len(text_bad) > max(10, 0.002 * len(scores)) else 'warn'
                F.add(sev, 'strongs_misplaced', f'{len(text_bad)} verse(s) share more Strong\'s numbers with a '
                      'neighbouring KJV verse than with their own address, e.g. '
                      + '; '.join(f'{ref_name(v)} looks like {ref_name(w)} ({a} vs {b})' for v, w, a, b in text_bad[:5]))
            if text_ok:
                F.add('warn', 'strongs_tags_offset', f'{len(text_ok)} verse(s) carry the Strong\'s numbers of a neighbouring '
                      'verse although the text itself lines up with the reference (tags attached by another '
                      'versification?), e.g. ' + '; '.join(f'{ref_name(v)} ~ {ref_name(w)}' for v, w, a, b in text_ok[:5]))
    report['alignment'] = align

    # --- metadata -------------------------------------------------------------------
    meta = OrderedDict((k, mi.get(k)) for k in (
        'abbreviation', 'full_name', 'language_code', 'right_to_left', 'license_spdx', 'license_url',
        'versification', 'content_version', 'content_sha256', 'is_original_language'))
    h = hashlib.sha256()
    for verse_id, (text, _, _) in db_rows.items():
        h.update(f'{verse_id}\t{text}\n'.encode('utf-8'))
    meta['content_sha256_matches'] = (h.hexdigest() == mi.get('content_sha256'))
    report['metadata'] = meta
    if not meta['content_sha256_matches']:
        F.add('error', 'content_sha256', 'module_info.content_sha256 does not match the verse text')
    if not mi.get('license_spdx'):
        F.add('warn', 'license_unknown', 'module_info.license_spdx is empty (treated as restricted)')
    # Licence text that contradicts the SPDX id: THOT's ShortCopyright says
    # CC BY-NC-ND 4.0 while its DistributionLicense (-> license_spdx) says CC BY.
    spdx = (mi.get('license_spdx') or '').upper()
    blurb = f"{mi.get('copyright') or ''} {mi.get('description') or ''}".upper()
    for marker, needle in (('non-commercial', r'\bNC\b|BY-NC|NON-?COMMERCIAL'), ('no-derivatives', r'\bND\b|-ND\b|NO ?DERIV')):
        if re.search(needle, blurb) and not re.search(needle, spdx) and 'NONCOMMERCIAL' not in spdx:
            F.add('warn', 'license_conflict', f'module text mentions a {marker} licence but license_spdx is '
                  f'{mi.get("license_spdx") or "(empty)"}: {clip(mi.get("copyright") or "", 160)}')
            break
    for field in ('full_name', 'copyright', 'description'):
        if mi.get(field) and re.search(r'\\(par|pard|qc|u\d+)|<[a-zA-Z/][^>]*>', str(mi.get(field))):
            F.add('warn', f'metadata_{field}_markup', f'module_info.{field} contains RTF/HTML markup')
    if catalog_row:
        lc = (catalog_row.get('app_language_code') or '').strip()
        if lc and mi.get('language_code') and lc != mi.get('language_code'):
            F.add('info', 'language_code', f'module_info.language_code {mi.get("language_code")!r} vs catalog {lc!r}')
        rtl = (catalog_row.get('right_to_left') or '0').strip() == '1'
        if rtl != bool(mi.get('right_to_left')):
            F.add('warn', 'right_to_left', f'right_to_left {mi.get("right_to_left")!r} but catalog right_to_left={int(rtl)}')

    # --- gut-check stats -------------------------------------------------------------
    texts = [r[0] for r in db_rows.values()]
    rnd = random.Random(seed)
    sample_texts = rnd.sample(texts, min(3000, len(texts))) if texts else []
    tokens = Counter(w.strip('.,;:!?()[]"\'\u201c\u201d\u2018\u2019').casefold()
                     for t in sample_texts for w in t.split())
    scripts = dominant_script(sample_texts)
    lang = (mi.get('language_code') or conf.get('Lang', '')).split('-')[0].lower()
    report['stats'] = OrderedDict([
        ('verses', len(db_rows)), ('words', sum(len(t.split()) for t in texts)),
        ('mean_words_per_verse', round(sum(len(t.split()) for t in texts) / max(1, len(texts)), 2)),
        ('mean_chars_per_verse', round(sum(len(t) for t in texts) / max(1, len(texts)), 1)),
        ('shortest', [f'{ref_name(v)}: {clip(r[0], 80)}' for v, r in sorted(db_rows.items(), key=lambda x: len(x[1][0]))[:5]]),
        ('top_tokens', tokens.most_common(15)),
        ('scripts', scripts), ('language', lang),
    ])
    exp = EXPECTED_SCRIPT.get(lang)
    if exp and scripts and scripts[0][0] != exp:
        F.add('warn', 'script_mismatch', f'language {lang!r} expects {exp} script, text is mostly {scripts[0][0]}')

    # --- validator -----------------------------------------------------------------------
    if run_validate:
        val = run_validator(db_path)
        report['validator'] = val
        if val.get('ok') is False:
            F.add('error', 'validate_module', 'validate-module.js failed: ' + '; '.join(val.get('errors', [])[:3]))

    for w in conv.get('warnings', []):
        if 'not in the cache' in w or 'had no host verse' in w or 'NOT imported' in w:
            F.add('error', 'converter_dropped_text', clip(w, 200))

    # --- samples for review ---------------------------------------------------------------
    def sample_entry(verse_id, why):
        g = groups.get(verse_id)
        row = db_rows.get(verse_id)
        return OrderedDict([
            ('why', why), ('ref', ref_name(verse_id)), ('verse_id', verse_id),
            ('source_refs', g['refs'] if g else []),
            ('sword_plain', clip(' '.join(g['plain']), 600) if g else None),
            ('db_text', clip(row[0], 600) if row else None),
            ('db_formatting', clip(row[1], 400) if row and row[1] else None),
        ])
    samples = []
    for ref in KEY_VERSES:
        v = parse_ref(ref)
        if v in db_rows or v in groups:
            samples.append(sample_entry(v, 'key verse'))
    pool = sorted(db_ids)
    for v in rnd.sample(pool, min(12, len(pool))):
        samples.append(sample_entry(v, 'random'))
    for s, v in diffs[:6]:
        samples.append(sample_entry(v, f'largest difference (similarity {s:.2f})'))
    for v in src_only[:3]:
        samples.append(sample_entry(v, 'red letter in source, not in DB'))
    for v in db_only[:3]:
        samples.append(sample_entry(v, 'red letter in DB, not in source'))
    for _, v in overlong[:3]:
        samples.append(sample_entry(v, 'overlong vs reference'))
    for v in missing_real[:3]:
        samples.append(sample_entry(v, 'missing in DB'))
    report['samples'] = samples

    report['findings'] = F.items
    report['status'] = F.status()
    report['seconds'] = round(time.time() - t0, 1)
    return report


# ---------------------------------------------------------------------------
# Non-bible modules: lighter, generic checks
# ---------------------------------------------------------------------------
CONTENT_COLUMNS = {
    'commentary': ('commentary_entry', 'content'),
    'dictionary': ('dictionary_entry', 'definition'),
    'devotional': ('devotional_entry', 'content'),
    'book': ('book_section', 'content'),
}


def verify_generic(zip_path, db_path, module_type, log_path=None, catalog_row=None, module=None,
                   seed=1, run_validate=True):
    t0 = time.time()
    F = Findings()
    report = OrderedDict([('module', module or os.path.basename(zip_path)), ('type', module_type),
                          ('zip', zip_path), ('db', db_path)])
    info = probe_info(zip_path, module)
    report['source'] = {'name': info.get('name'), 'type': info.get('type'),
                        'source_type': info.get('conf', {}).get('SourceType', ''),
                        'encoding': info.get('conf', {}).get('Encoding', '')}
    recs = probe_dump(zip_path, module)
    src_nonempty = sum(1 for r in recs if (r.get('plain') or '').strip())
    report['conversion_log'] = parse_convert_log(log_path)
    table, column = CONTENT_COLUMNS[module_type]
    db = open_db(db_path)
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
    if column not in cols:
        F.add('error', 'content_column', f'{table}.{column} missing (columns: {cols})')
        report['findings'] = F.items
        report['status'] = F.status()
        return report
    key_col = next((c for c in ('title', 'headword', 'term', 'entry_key', 'key', 'section_title', 'date_key',
                                'reference', 'verse_id_start') if c in cols), cols[0])
    rows = db.execute(f'SELECT rowid, {key_col}, {column} FROM {table}').fetchall()
    links = db.execute('SELECT COUNT(*) FROM verse_link').fetchone()[0] if 'verse_link' in {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} else None
    db.close()
    report['counts'] = {'source_entries': len(recs), 'source_nonempty': src_nonempty, 'db_rows': len(rows),
                        'verse_links': links}
    if not rows:
        F.add('error', 'empty', 'no rows converted')
    elif src_nonempty and len(rows) < 0.8 * src_nonempty:
        F.add('warn', 'row_count', f'{len(rows)} DB rows for {src_nonempty} non-empty source entries')
    elif src_nonempty and len(rows) > 1.5 * src_nonempty:
        # One SWORD entry covering a verse range stored once per verse (MHCC:
        # 28,718 rows from 4,047 entries). the format anchors an entry to
        # its passage with verse_id_start..verse_id_end: one row per entry.
        F.add('warn', 'entries_duplicated', f'{len(rows)} DB rows for {src_nonempty} non-empty source entries: '
              'range entries appear to be stored once per verse')
    src_words = sum(count_words(r.get('plain') or '') for r in recs)
    db_words = sum(count_words(r[2] or '') for r in rows)
    report['counts'].update({'source_words': src_words, 'db_words': db_words})
    if src_words and abs(db_words - src_words) / src_words > 0.10:
        F.add('warn', 'word_count_delta', f'DB has {db_words} words vs {src_words} in the source plain text')
    hits = scan_patterns((r[1], r[2] or '') for r in rows)
    report['hygiene'] = {k: {'severity': v['severity'], 'rows': v['rows'], 'examples': v['examples']}
                         for k, v in hits.items()}
    # Only bible_verse.text must be markup-free (the format's clean-text rule); the content
    # of other module types may carry markup, so it is counted, not judged.
    markup = {'html_tag', 'entity', 'angle_bracket', 'whitespace', 'backslash'}
    for k, v in hits.items():
        if v['severity'] == 'error':
            F.add('info' if k in markup else 'warn', f'text_{k}', f'{k}: {v["rows"]} row(s)')
    rnd = random.Random(seed)
    report['samples'] = [{'key': clip(r[1], 120), 'content': clip(r[2] or '', 500)}
                         for r in rnd.sample(rows, min(10, len(rows)))]
    if run_validate:
        val = run_validator(db_path)
        report['validator'] = val
        if val.get('ok') is False:
            F.add('error', 'validate_module', 'validate-module.js failed: ' + '; '.join(val.get('errors', [])[:3]))
    report['findings'] = F.items
    report['status'] = F.status()
    report['seconds'] = round(time.time() - t0, 1)
    return report


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def render_markdown(r):
    out = [f"# {r['module']}: {r['status']}", '']
    src = r.get('source', {})
    cat = r.get('catalog') or {}
    out.append(f"Source: {src.get('description') or src.get('name')} | versification {src.get('versification', '?')} | "
               f"markup {src.get('source_type') or '?'} | encoding {src.get('encoding') or '(default)'}"
               + (f" | {cat.get('repository')} | licence verdict: {cat.get('conversion_license_verdict')}" if cat else ''))
    out += ['', '## Findings', '']
    order = {'error': 0, 'warn': 1, 'info': 2}
    for f in sorted(r.get('findings', []), key=lambda x: order[x['severity']]):
        out.append(f"- **{f['severity'].upper()}** `{f['code']}`: {f['message']}")
    if not r.get('findings'):
        out.append('- none')
    for section in ('counts', 'fidelity', 'red_letter', 'canon', 'alignment', 'formatting', 'hygiene',
                    'metadata', 'stats', 'validator', 'conversion_log'):
        val = r.get(section)
        if not val:
            continue
        out += ['', f'## {section.replace("_", " ").title()}', '']
        if isinstance(val, dict):
            for k, v in val.items():
                if k in ('worst', 'per_book'):
                    continue
                out.append(f'- {k}: {json.dumps(v, ensure_ascii=False)}')
            if section == 'fidelity' and val.get('worst'):
                out += ['', 'Largest differences (libsword plain vs DB):', '']
                for w in val['worst'][:8]:
                    out.append(f"- {w['ref']} ({w['similarity']})\n  - SWORD: {w['source']}\n  - DB:    {w['db']}")
    if r.get('samples'):
        out += ['', '## Sample verses', '',
                'For review: compare SWORD plain (libsword) with the DB text; formatting is the span JSON.', '']
        for s in r['samples']:
            if 'verse_id' in s:
                out.append(f"### {s['ref']} ({s['why']}; source {', '.join(map(str, s['source_refs'])) or '-'})")
                out.append(f"- SWORD: {s['sword_plain']}")
                out.append(f"- DB:    {s['db_text']}")
                if s.get('db_formatting'):
                    out.append(f"- fmt:   `{s['db_formatting']}`")
            else:
                out.append(f"### {s['key']}")
                out.append(s['content'])
            out.append('')
    return '\n'.join(out) + '\n'


def write_report(report, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    def default(o):
        if isinstance(o, set):
            return sorted(o)
        return str(o)
    with open(os.path.join(out_dir, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=default)
    with open(os.path.join(out_dir, 'report.md'), 'w', encoding='utf-8') as f:
        f.write(render_markdown(report))


# ---------------------------------------------------------------------------
# Catalog + batch
# ---------------------------------------------------------------------------
def read_catalog(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = [r for r in csv.DictReader(f)]
    return [r for r in rows if r.get('module_id') and not (r.get('repository') or '').startswith('GENERAL')
            and r.get('sword_module_type')]


def select_rows(rows, args):
    verdicts = allowed_verdicts(args.license)
    types = set(args.types.split(','))
    picked, excluded = [], Counter()
    wanted = {m.lower() for m in args.modules.split(',')} if args.modules else None
    repos = set(args.repos.split(',')) if args.repos else None
    for r in rows:
        verdict = (r.get('conversion_license_verdict') or '').strip()
        if wanted and r['module_id'].lower() not in wanted:
            continue
        if verdict in NEVER_VERDICTS:
            excluded[f'licence: {verdict or "(none)"}'] += 1
            continue
        if verdict not in verdicts:
            excluded[f'licence tier: {verdict}'] += 1
            continue
        if (r.get('app_module_type') or '') not in types:
            excluded[f'type: {r.get("app_module_type")}'] += 1
            continue
        if (r.get('encrypted') or '').strip():
            excluded['encrypted'] += 1
            continue
        if repos and r['repository'] not in repos:
            excluded['repository filter'] += 1
            continue
        if not (r.get('zip_download_url') or '').strip():
            excluded['no zip url'] += 1
            continue
        picked.append(r)
    # one copy per module id: prefer the main repository, never the attic when another exists
    best = {}
    for r in picked:
        k = r['module_id'].lower()
        rank = REPO_STATUS_RANK.get(r.get('repository_status', ''), 9)
        if k not in best or rank < best[k][0]:
            best[k] = (rank, r)
    dropped = len(picked) - len(best)
    if dropped:
        excluded['duplicate id (other repository preferred)'] += dropped
    chosen = [v[1] for v in best.values()]
    chosen.sort(key=lambda r: (r['repository'], r['module_id'].lower()))
    if args.sample:
        rnd = random.Random(args.seed)
        strata = defaultdict(list)
        for r in chosen:
            strata[(r['repository'], (r.get('versification_sword') or '').lower(), r.get('source_markup'))].append(r)
        for lst in strata.values():
            rnd.shuffle(lst)
        keys = sorted(strata)
        rnd.shuffle(keys)
        sample = []
        while len(sample) < args.sample and any(strata.values()):
            for k in keys:
                if strata[k] and len(sample) < args.sample:
                    sample.append(strata[k].pop())
        chosen = sorted(sample, key=lambda r: (r['repository'], r['module_id'].lower()))
    if args.limit:
        chosen = chosen[:args.limit]
    return chosen, excluded


def slug(text):
    return re.sub(r'[^A-Za-z0-9]+', '_', text).strip('_')


def download(url, dest, timeout=300):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return
    if os.path.exists(dest):
        os.remove(dest)          # an empty file left by an earlier failed download
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + '.part'
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'bible-scripts swordcheck'})
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, 'wb') as f:
                shutil.copyfileobj(resp, f)
            # An empty or non-zip body is a failed download, not a module
            # (FreJer.zip came back as 0 bytes and then failed as "Not a zip").
            import zipfile
            if os.path.getsize(tmp) == 0 or not zipfile.is_zipfile(tmp):
                size = os.path.getsize(tmp)
                os.remove(tmp)
                raise RuntimeError(f'not a zip archive ({size} bytes)')
            os.replace(tmp, dest)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f'download failed: {url}: {last}')


def db_name(row):
    t = row['app_module_type']
    return f"{t}_{slug(row['module_id']).lower()}.db"


def process_module(row, work, reference_db, keep_db, reconvert, run_validate):
    mid = row['module_id']
    t = row['app_module_type']
    out_dir = os.path.join(work, 'reports', slug(mid))
    summary = {'module_id': mid, 'repository': row['repository'], 'type': t,
               'versification': row.get('versification_sword'), 'markup': row.get('source_markup'),
               'language': row.get('app_language_code'), 'licence': row.get('conversion_license_verdict'),
               'status': 'ERROR', 'stage': '', 'codes': '', 'verses': '', 'exact_ratio': '', 'red_src': '',
               'red_db': '', 'seconds': 0}
    t0 = time.time()
    zip_path = os.path.join(work, 'cache', slug(row['repository']), os.path.basename(row['zip_download_url']))
    db_path = os.path.join(work, 'db', db_name(row))
    log_path = os.path.join(work, 'logs', f'{slug(mid)}.convert.log')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        summary['stage'] = 'download'
        download(row['zip_download_url'], zip_path)
        summary['stage'] = 'convert'
        conv = CONVERTERS.get(t)
        if not conv:
            raise RuntimeError(f'no converter for module type {t}')
        require_binary(conv)
        override = load_overrides().get(mid.lower()) if t == 'bible' else None
        summary['versification_override'] = override or ''
        if reconvert or not os.path.exists(db_path):
            # Not every converter replaces an existing output (sword2commentary
            # fails with "table module_info already exists"), so start clean.
            for stale in (db_path, db_path + '-wal', db_path + '-shm'):
                if os.path.exists(stale):
                    os.remove(stale)
            cmd = [conv, '-i', zip_path, '-o', db_path] + (['--versification', override] if override else [])
            with open(log_path, 'w', encoding='utf-8') as log:
                p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            if p.returncode != 0:
                if os.path.exists(db_path):
                    os.remove(db_path)
                conv_log = parse_convert_log(log_path)
                reason = (conv_log['errors'] or ['converter exited with code %d' % p.returncode])[0]
                summary.update({'status': 'NOT CONVERTED', 'codes': clip(reason, 160)})
                write_report({'module': mid, 'status': 'NOT CONVERTED', 'catalog': row,
                              'conversion_log': conv_log, 'findings': [
                                  {'severity': 'error', 'code': 'not_converted', 'message': reason}]}, out_dir)
                return summary
        summary['stage'] = 'verify'
        if t == 'bible':
            rep = verify_bible(zip_path, db_path, log_path, reference_db, row, run_validate=run_validate,
                               versification_override=override)
            summary.update({'verses': rep['counts']['db_verses'] if 'counts' in rep else '',
                            'exact_ratio': rep.get('fidelity', {}).get('exact_ratio', ''),
                            'red_src': rep.get('red_letter', {}).get('source_verses_red', ''),
                            'red_db': rep.get('red_letter', {}).get('db_verses_red', '')})
        else:
            rep = verify_generic(zip_path, db_path, t, log_path, row, run_validate=run_validate)
            summary['verses'] = rep.get('counts', {}).get('db_rows', '')
        write_report(rep, out_dir)
        summary['status'] = rep['status']
        summary['codes'] = ' '.join(sorted({f['code'] for f in rep['findings'] if f['severity'] != 'info'}))
        summary['stage'] = 'done'
        if keep_db == 'none' or (keep_db == 'flagged' and rep['status'] == 'PASS'):
            os.remove(db_path)
    except subprocess.TimeoutExpired as exc:
        summary['codes'] = f'timeout in {summary["stage"]}: {exc}'
    except Exception as exc:  # noqa: BLE001 - one bad module must not stop the batch
        summary['codes'] = clip(f'{summary["stage"]}: {exc}', 300)
    summary['seconds'] = round(time.time() - t0, 1)
    return summary


SUMMARY_FIELDS = ['module_id', 'repository', 'type', 'status', 'codes', 'verses', 'exact_ratio', 'red_src',
                  'red_db', 'versification', 'markup', 'language', 'licence', 'stage', 'seconds']


def write_summary(work, rows):
    rows = sorted(rows, key=lambda r: ({'FAIL': 0, 'ERROR': 1, 'NOT CONVERTED': 2, 'WARN': 3, 'PASS': 4}.get(r['status'], 5),
                                       r['module_id'].lower()))
    with open(os.path.join(work, 'summary.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    status = Counter(r['status'] for r in rows)
    codes = Counter(c for r in rows for c in (r['codes'].split() if r['status'] in ('FAIL', 'WARN') else []))
    not_conv = Counter(re.sub(r"'[^']*'", "'…'", r['codes'])[:90] for r in rows if r['status'] == 'NOT CONVERTED')
    lines = ['# SWORD conversion summary', '',
             f'{len(rows)} module(s): ' + ', '.join(f'{k} {v}' for k, v in status.most_common()), '',
             '## Finding codes (FAIL/WARN modules)', '']
    lines += [f'- `{k}`: {v}' for k, v in codes.most_common()] or ['- none']
    if not_conv:
        lines += ['', '## Not converted (converter refused or failed)', '']
        lines += [f'- {v} x {k}' for k, v in not_conv.most_common()]
    lines += ['', '## Modules', '', '| module | repo | status | verses | exact | red src/db | versif. | markup | codes |',
              '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        lines.append(f"| {r['module_id']} | {r['repository'][:12]} | {r['status']} | {r['verses']} | {r['exact_ratio']} | "
                     f"{r['red_src']}/{r['red_db']} | {r['versification']} | {r['markup']} | {clip(r['codes'], 120)} |")
    with open(os.path.join(work, 'summary.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def ensure_reference(work, catalog_rows, reference):
    if reference:
        return reference
    path = os.path.join(work, 'db', 'bible_kjv.db')
    if os.path.exists(path):
        return path
    kjv = next((r for r in catalog_rows if r['module_id'] == 'KJV' and r['repository'] == 'CrossWire'), None)
    if not kjv:
        return None
    zip_path = os.path.join(work, 'cache', 'CrossWire', 'KJV.zip')
    download(kjv['zip_download_url'], zip_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.join(work, 'logs'), exist_ok=True)
    with open(os.path.join(work, 'logs', 'KJV.reference.convert.log'), 'w') as log:
        subprocess.run([CONVERTERS['bible'], '-i', zip_path, '-o', path], stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=1800)
    return path


def cmd_select(args):
    rows = read_catalog(args.catalog)
    chosen, excluded = select_rows(rows, args)
    print(f'{len(chosen)} module(s) selected')
    for k, v in excluded.most_common():
        print(f'  excluded {v:5d}  {k}')
    if args.verbose:
        for r in chosen:
            print(f"  {r['repository'][:14]:14} {r['module_id']:24} {r['app_module_type']:10} "
                  f"{r.get('versification_sword', ''):14} {r.get('conversion_license_verdict')}")


def cmd_batch(args):
    rows = read_catalog(args.catalog)
    chosen, excluded = select_rows(rows, args)
    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    print(f'{len(chosen)} module(s) selected; excluded: ' + ', '.join(f'{k} {v}' for k, v in excluded.most_common()),
          flush=True)
    reference = ensure_reference(work, rows, args.reference)
    results_path = os.path.join(work, 'results.jsonl')
    done = {}
    if os.path.exists(results_path) and not args.reconvert:
        with open(results_path, encoding='utf-8') as f:
            for line in f:
                r = json.loads(line)
                done[(r['module_id'], r['repository'])] = r
    if args.reverify:
        done = {}      # verify again every selected module; the DBs are reused, not rebuilt
    todo = [r for r in chosen if (r['module_id'], r['repository']) not in done]
    print(f'{len(todo)} to process, {len(chosen) - len(todo)} already in results.jsonl', flush=True)
    results = [done[(r['module_id'], r['repository'])] for r in chosen if (r['module_id'], r['repository']) in done]
    with open(results_path, 'a', encoding='utf-8') as out, \
            concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(process_module, r, work, reference, args.keep_db, args.reconvert,
                               not args.no_validate): r for r in todo}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            res = fut.result()
            results.append(res)
            out.write(json.dumps(res, ensure_ascii=False) + '\n')
            out.flush()
            print(f"[{i}/{len(todo)}] {res['module_id']:24} {res['status']:13} {res['seconds']:6.1f}s {clip(res['codes'], 100)}",
                  flush=True)
    # The summary covers every module in results.jsonl (latest result each),
    # not just this invocation's selection, so successive batches accumulate.
    cmd_summary(argparse.Namespace(work=work))


def cmd_summary(args):
    work = os.path.abspath(args.work)
    latest = OrderedDict()
    with open(os.path.join(work, 'results.jsonl'), encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            latest[(r['module_id'], r['repository'])] = r
    write_summary(work, list(latest.values()))
    print(os.path.join(work, 'summary.md'))


def cmd_verify(args):
    catalog_row = None
    if args.catalog:
        name = args.module or os.path.splitext(os.path.basename(args.zip))[0]
        catalog_row = next((r for r in read_catalog(args.catalog) if r['module_id'].lower() == name.lower()), None)
    if args.type == 'bible':
        rep = verify_bible(args.zip, args.db, args.log, args.reference, catalog_row, args.module,
                           args.keep_dump, args.seed, not args.no_validate, args.versification)
    else:
        rep = verify_generic(args.zip, args.db, args.type, args.log, catalog_row, args.module, args.seed,
                             not args.no_validate)
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.db)), 'reports',
                                   slug(rep['module']))
    write_report(rep, out)
    print(f"{rep['module']}: {rep['status']}")
    for f in rep['findings']:
        print(f"  {f['severity']:5} {f['code']}: {clip(f['message'], 200)}")
    print(f'report: {os.path.join(out, "report.md")}')
    return 0 if rep['status'] != 'FAIL' else 1


def cmd_probe(args):
    """Side-by-side view of chosen verses: SWORD (raw, html, plain) and the DB row."""
    require_binary(PROBE)
    db = open_db(args.db) if args.db else None
    targets = []
    if args.grep or args.sql:
        if not db:
            raise SystemExit('--grep/--sql need --db')
        if args.sql:
            ids = [r[0] for r in db.execute(args.sql)]
        else:
            rx = re.compile(args.grep)
            ids = [v for v, t in db.execute('SELECT verse_id, text FROM bible_verse ORDER BY verse_id') if rx.search(t or '')]
        print(f'{len(ids)} matching verse(s); showing {min(len(ids), args.limit)}')
        targets = ids[:args.limit]
    else:
        targets = [parse_ref(r) for r in args.refs]
    info = probe_info(args.zip, args.module)
    versification = info.get('versification', 'KJV')
    source_refs = defaultdict(list)
    if versification.lower() not in KJV_COMPATIBLE:
        # Find the source verses that land on each target id.
        recs = probe_dump(args.zip, args.module)
        src_max = source_chapter_max(recs)
        for rec in recs:
            v, _ = expected_verse_id(rec, versification, src_max)
            if v in targets:
                source_refs[v].append(rec.get('osis'))
    for v in targets:
        refs = source_refs.get(v) or [ref_name(v)]
        print('#' * 78)
        print(f'# {ref_name(v)} ({v}); source versification {versification}; source ref(s): {", ".join(refs)}')
        p = subprocess.run([PROBE, '-i', args.zip] + (['--module', args.module] if args.module else [])
                           + ['verse'] + [r.replace('.', ' ', 1).replace('.', ':', 1) if re.match(r'^\w+\.\d+\.\d+$', r) else r
                                          for r in refs], capture_output=True)
        print(p.stdout.decode('utf-8', 'replace').rstrip())
        if db:
            row = db.execute('SELECT text, formatting, word_count FROM bible_verse WHERE verse_id = ?', (v,)).fetchone()
            print('--- DB')
            if row:
                print(f'text:       {row[0]}\nformatting: {row[1]}\nword_count: {row[2]}')
                il = db.execute('SELECT word_position_start, word_position_end, strongs_number, gloss FROM interlinear_word '
                                'WHERE verse_id = ? ORDER BY word_position_start LIMIT 40', (v,)).fetchall()
                if il:
                    print('interlinear: ' + ' '.join(f'[{a}-{b} {s} "{g}"]' for a, b, s, g in il))
            else:
                print('(no row)')
        print()
    if db:
        db.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def add_select(p):
        p.add_argument('--catalog', required=True, help='sword_modules_catalog CSV')
        p.add_argument('--types', default='bible', help='comma list of app_module_type (bible,commentary,dictionary,book,devotional)')
        p.add_argument('--license', default='open', choices=list(LICENSE_TIERS),
                       help='open (default) | verbatim (+CC ND) | noncommercial (+NC); NEEDS PERMISSION and MANUAL REVIEW are never selected')
        p.add_argument('--repos', help='comma list of repository names')
        p.add_argument('--modules', help='comma list of module ids')
        p.add_argument('--sample', type=int, help='stratified sample of N modules (repository x versification x markup)')
        p.add_argument('--seed', type=int, default=1)
        p.add_argument('--limit', type=int)

    p = sub.add_parser('select', help='dry run: which catalog rows a batch would process')
    add_select(p)
    p.add_argument('-v', '--verbose', action='store_true')
    p.set_defaults(func=cmd_select)

    p = sub.add_parser('batch', help='download, convert and verify many modules')
    add_select(p)
    p.add_argument('--work', required=True, help='output directory (cache/, db/, logs/, reports/, summary.md)')
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--reference', help='reference KJV bible db for alignment checks (default: converts CrossWire KJV)')
    p.add_argument('--keep-db', default='all', choices=['all', 'flagged', 'none'])
    p.add_argument('--reconvert', action='store_true', help='reconvert and re-verify even if results exist')
    p.add_argument('--reverify', action='store_true',
                   help='verify again even if results exist, reusing converted DBs (converts only what is missing)')
    p.add_argument('--no-validate', action='store_true', help='skip scripts/modules/validate-module.js')
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser('summary', help='rebuild summary.md/csv from results.jsonl')
    p.add_argument('--work', required=True)
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser('verify', help='verify one converted module against its SWORD source')
    p.add_argument('--zip', required=True)
    p.add_argument('--db', required=True)
    p.add_argument('--type', default='bible', choices=['bible'] + list(CONTENT_COLUMNS))
    p.add_argument('--module', help='module name inside the zip (default: the first one)')
    p.add_argument('--log', help='converter log, for its counters and warnings')
    p.add_argument('--reference', help='reference KJV bible db for alignment checks')
    p.add_argument('--catalog', help='catalog CSV, to compare metadata')
    p.add_argument('--out', help='report directory')
    p.add_argument('--keep-dump', help='also write the sword-probe dump (JSON Lines) here')
    p.add_argument('--versification', help='the override the module was converted with (sword2bible --versification)')
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--no-validate', action='store_true')
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser('probe', help='show verses side by side: SWORD raw/html/plain and the DB row')
    p.add_argument('--zip', required=True)
    p.add_argument('--db')
    p.add_argument('--module')
    p.add_argument('--grep', help='regex over DB text; show the matching verses')
    p.add_argument('--sql', help='SQL returning verse ids to show')
    p.add_argument('--limit', type=int, default=10)
    p.add_argument('refs', nargs='*', help='references in KJV numbering: "John 3:16", 43003016')
    p.set_defaults(func=cmd_probe)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == '__main__':
    sys.exit(main())
