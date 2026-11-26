"""Paragraph starts from the World English Bible USFM, for KJV verses that have no pilcrow
(Acts 20:37 to Revelation). Only breaks at the start of a verse count; verse 1 is skipped.
python web_paragraphs.py USFM_DIR KJV_DB OUT.json"""
import json, os, re, sqlite3, sys
NT = 'MAT MRK LUK JHN ACT ROM 1CO 2CO GAL EPH PHP COL 1TH 2TH 1TI 2TI TIT PHM HEB JAS 1PE 2PE 1JN 2JN 3JN JUD REV'.split()
BREAK = re.compile(r'^\\(p|pi\d?|pm|pmo|b|ms\d?|s\d?|nb)\b')
usfm, db, out = sys.argv[1:4]
kjv = {v for (v,) in sqlite3.connect(f'file:{db}?mode=ro', uri=True).execute('select verse_id from bible_verse')}
breaks, total = {}, 0
for code in NT:
    book = 40 + NT.index(code)
    f = next(x for x in os.listdir(usfm) if x[3:].startswith(code) or x[4:].startswith(code))
    chap, pending = 0, False
    for line in open(os.path.join(usfm, f), encoding='utf-8'):
        line = line.strip()
        m = re.match(r'^\\c (\d+)', line)
        if m: chap, pending = int(m.group(1)), False; continue
        b = BREAK.match(line)
        if b:
            rest = line[b.end():].strip()
            if b.group(1) == 'nb': pending = False; continue
            if not rest: pending = True; continue          # marker alone on its line: break before the next verse
            line = rest
        m = re.match(r'^\\v (\d+)', line)
        if m:
            v = int(m.group(1)); vid = book * 1000000 + chap * 1000 + v
            if pending and v > 1 and vid > 44020036 and vid in kjv:
                breaks.setdefault(str(book), {}).setdefault(str(chap), []).append(v); total += 1
            pending = False
        elif line and not line.startswith('\\'):
            pending = False
json.dump({'format': 'kth-paragraphs/1',
           'source': 'World English Bible (public domain), ebible.org eng-web_usfm, verse-start paragraphs only',
           'appliesTo': 'KJV verses after the last KJV pilcrow (Acts 20:36), Acts 20:37 to Revelation 22:21',
           'breaks': breaks}, open(out, 'w'), indent=None, separators=(',', ':'))
print(total, 'breaks; 1 John:', breaks.get('62'), '\nEph:', breaks.get('49'), '\nCol:', breaks.get('51'))
