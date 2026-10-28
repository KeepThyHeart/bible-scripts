#!/usr/bin/env python3
"""
nave-to-topical.py - build topical_nave.db from the CrossWire "Nave" SWORD module.

Why this exists: the earlier builder of topical_nave.db was not kept. Its output
flattened Nave's nesting (sub-entries became siblings), cut chapter-only
references ("Jos 6") down to verse 1, and kept the "." indent markers in names.

Usage:
    mod2imp Nave > nave.imp          # SWORD utilities; run in a dir with the unzipped module
    python3 nave-to-topical.py --imp nave.imp --template topical_nave.db \
        --output out/topical_nave.db [--report report.json]

The --template is an existing topical_nave.db: its schema and module_info row are
reused (uuid, description, licence), so identity stays stable. Without it the
script cannot build the database (there is no standalone schema copy).

Structure recovered from the SWORD text (it carries no explicit depth):
  <lb/>-> HEADING        level 1, child of the entry
  <lb/>N. sense          level 1 (or child of a refless, listless "->" heading)
  <lb/>text              item-level line outside a <list>
  <list><item>..         children of the line before the list
  " .text" / <lb/>.text  "." is Nave's indent marker: text is one level deeper,
                         a child of the item before it
  refless item (not "See")  a sub-heading: the items after it, up to the next
                         refless item or "See" item, are its children
  "See X"                cross-reference: becomes the node's description
                         ("See X", the app shows a link) or, for several,
                         metadata.seeAlso
  "(See each one ...)"   a note after a heading: becomes its description
"""
import argparse, hashlib, html, json, os, re, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERS = os.path.join(HERE, '..', '..', '..', '..', 'scripts', 'data', 'kjv-versification.json')

OSIS = ['Gen','Exod','Lev','Num','Deut','Josh','Judg','Ruth','1Sam','2Sam','1Kgs','2Kgs','1Chr','2Chr','Ezra','Neh','Esth','Job','Ps','Prov','Eccl','Song','Isa','Jer','Lam','Ezek','Dan','Hos','Joel','Amos','Obad','Jonah','Mic','Nah','Hab','Zeph','Hag','Zech','Mal','Matt','Mark','Luke','John','Acts','Rom','1Cor','2Cor','Gal','Eph','Phil','Col','1Thess','2Thess','1Tim','2Tim','Titus','Phlm','Heb','Jas','1Pet','2Pet','1John','2John','3John','Jude','Rev']
BOOK = {a: i + 1 for i, a in enumerate(OSIS)}


# The SWORD text has no indent depth, so a refless item adopts the items after it until the next
# refless or "See" item. Where that runs too far, the limit is set by hand: (entry, heading) -> children.
GROUP_LIMITS = {
    ('Jesus, The Christ', 'Facts before the birth of'): 4,   # Gabriel, Mary visits, Magnificat, Joseph's angel
}


class Node:
    __slots__ = ('name', 'refs', 'kids', 'see', 'desc', 'had_list', 'sort')

    def __init__(self, name):
        self.name = name; self.refs = []; self.kids = []; self.see = []; self.desc = None
        self.had_list = False; self.sort = 0


class Refs:
    def __init__(self):
        self.counts = json.load(open(VERS, encoding='utf-8'))['books']
        self.counts = {b['book_number']: b['verse_counts'] for b in self.counts}
        self.bad = []
        self.chapter_only = 0

    def one(self, s, end):
        p = s.split('.')
        book = BOOK.get(p[0])
        if not book:
            raise ValueError(s)
        ch = int(p[1])
        if len(p) >= 3:
            v = int(p[2])
        else:
            self.chapter_only += 1 if not end else 0
            vc = self.counts[book]
            v = vc[ch - 1] if end and ch <= len(vc) else 1
        return book * 1000000 + ch * 1000 + v

    def parse(self, osis):
        a, _, b = osis.partition('-')
        try:
            s = self.one(a, False)
            e = self.one(b, True) if b else self.one(a, True)
        except (ValueError, IndexError, KeyError):
            self.bad.append(osis); return None
        if e < s:
            self.bad.append(osis); return None
        return (s, e)


TOK = re.compile(r'(<lb/>|<list>|</list>|<item>|</item>|<ref [^>]*>.*?</ref>|<[^>]+>)', re.S)
DOT = re.compile(r'(?:^|\s)\.(?=[^\s.\d])')


# The source has two systematic reference defects (text after the </ref> holds the real part):
#   <ref osisRef="Jude.1.1">Jude 1</ref>:14      single-chapter books: the verse is in the text after
#   <ref osisRef="PrAzar.1.2">Azariah 2</ref>Ch 15:1   "2Ch"/"2Ki" split after the name (also Wis.2)
FIXABLE = re.compile(r'^(?:(?:Obad|Phlm|2John|3John|Jude)\.1\.1|PrAzar\.1\.2|Wis\.2|[1-3A-Za-z]+\.\d+(?:\.\d+)?(?:-[1-3A-Za-z]+\.\d+\.\d+)?)$')
VS = r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*'
SUFFIX = re.compile(r'^:(' + VS + ')')
SUFFIX2 = re.compile(r'^(Ch|Ki)\s+(\d+):(' + VS + ')')
# plain-text continuation the source left without a <ref>: "...</ref>; 34:14."
CONT = re.compile(r'^;\s*(\d+):(' + VS + ')')


def _verses(book, ch, spec):
    out = []
    for part in spec.split(','):
        if '-' in part:
            a, b = part.split('-')
            out.append(f'{book}.{ch}.{a}-{book}.{ch}.{b}')
        else:
            out.append(f'{book}.{ch}.{part}')
    return out


def apply_fix(fix, t):
    """Replace the wrong osisRef (last in the list) by the real ones from the text after it."""
    reflist, osis = fix
    if not reflist or reflist[-1] != osis:
        return t
    if osis.split('.')[0] in ('Obad', 'Phlm', '2John', '3John', 'Jude'):
        m = SUFFIX.match(t)
        if m:
            reflist.pop(); reflist.extend(_verses(osis.split('.')[0], 1, m.group(1)))
            return t[m.end():]
    elif osis.split('.')[0] in ('PrAzar', 'Wis'):
        m = SUFFIX2.match(t)
        if m:
            book = {'Ch': '2Chr', 'Ki': '2Kgs'}[m.group(1)]
            reflist.pop(); reflist.extend(_verses(book, int(m.group(2)), m.group(3)))
            return t[m.end():]
    else:
        m = CONT.match(t)
        if m:
            book = osis.split('-')[-1].split('.')[0]
            reflist.extend(_verses(book, int(m.group(1)), m.group(2)))
            return t[m.end():]
    return t


def clean(t):
    t = html.unescape(re.sub(r'<[^>]*>', '', t))
    t = re.sub(r'\s+', ' ', t).strip()
    return t.strip(' ;,:')


class Segs:
    """Pieces of one line or item: [(text, [osisRefs])], split at the " ." indent marker."""
    def __init__(self):
        self.s = [['', [], False]]  # text, refs, seen-ref

    def text(self, t):
        parts = DOT.split(t)
        for i, p in enumerate(parts):
            if i > 0:
                self.s.append(['', [], False])
            cur = self.s[-1]
            if not cur[2]:
                cur[0] += p

    def ref(self, osis):
        cur = self.s[-1]; cur[2] = True
        if osis:
            cur[1].append(osis)

    def empty(self):
        return all(not clean(x[0]) and not x[1] for x in self.s)


def parse_entry(key, body, refs):
    m = re.search(r'<def>(.*)</def>', body, re.S)
    entry = Node(key)
    if not m:
        return entry
    st = {'section': entry, 'arrow': None, 'pp': entry, 'last': entry, 'adopter': None,
          'owner': None, 'saved_pp': None, 'maybe_pp': None}

    def add(parent, name, orefs):
        n = Node(name); n.refs = orefs; parent.kids.append(n); st['last'] = n
        return n

    def attach(parent, segs, kind, depth_parent_override=None):
        """Create the nodes for a Segs chain under parent; returns (first, last) nodes."""
        first = last = None
        par = parent
        for i, (t, rs, _) in enumerate(segs.s):
            name = clean(t)
            if kind == 'arrow' and i == 0:
                name = clean(re.sub(r'^\s*→', '', t))
            elif kind == 'num' and i == 0:
                name = clean(re.sub(r'^\s*\d+\.', '', t))
            name = name.lstrip('.').strip()
            if not name:
                par.refs += rs
                continue
            if re.match(r'(?i)see\b', name) and i == 0:
                tgt = clean(re.sub(r'(?i)^see\s+(the\s+)?', '', name))
                for part in re.split(r'\s*\.\s*See\s+', tgt):
                    par.see.append(part.strip(' .'))
                par.refs += rs
                continue
            n = add(par, name, list(rs))
            first = first or n
            last = n
            par = n
        return first, last

    def line(segs, kind):
        if segs.empty():
            return None
        text0 = clean(re.sub(r'^\s*→', '', segs.s[0][0]))
        if kind == 'arrow' and text0.startswith('(') and not segs.s[0][1] and len(segs.s) == 1:
            # "(See each one in its alphabetical order, below)": a note on the heading it follows
            tgt = st['arrow'] if st['arrow'] is not None else st['section']
            tgt.desc = (tgt.desc + ' ' + text0) if tgt.desc else text0
            st['owner'] = tgt; return tgt
        if kind == 'arrow':
            parent = entry
        elif kind == 'num':
            a = st['arrow']
            parent = a if (a is not None and not a.refs and not a.had_list and not a.see) else entry
        elif kind == 'dot':
            parent = st['last'] if st['last'] is not entry else entry
        else:
            parent = st['pp']
        if kind == 'plain' and st['maybe_pp'] is not None:
            parent = st['pp'] = st['maybe_pp']
        st['maybe_pp'] = None
        first, last = attach(parent, segs, kind)
        if first is None:  # only a See line / refs-only line: its list belongs to the section
            st['owner'] = st['section'] if st['section'] is not None else entry
            return st['owner']
        if kind == 'arrow':
            st['arrow'] = first; st['section'] = first; st['pp'] = first
        elif kind == 'num':
            st['section'] = first; st['pp'] = first
        st['owner'] = last
        return last

    def item(segs):
        owner = st['owner'] or entry
        if segs.empty():
            return
        first_txt = clean(segs.s[0][0])
        if re.match(r'(?i)see\b', first_txt):
            attach(owner, segs, 'item'); st['adopter'] = None; return
        ad = st['adopter']
        if ad is not None and len(ad.kids) >= GROUP_LIMITS.get((entry.name, ad.name), 10 ** 9):
            ad = st['adopter'] = None
        parent = ad if ad is not None else owner
        first, last = attach(parent, segs, 'item')
        if first is None:
            return
        if not first.refs and not first.kids and len(segs.s) == 1 and not first.see:
            st['adopter'] = first       # refless sub-heading adopts the next items
        elif parent is st['adopter'] and parent is not owner:
            pass                         # stays under the current sub-heading
        # a refless item after an adopter replaces it, handled below

    toks = [t for t in TOK.split(m.group(1)) if t]
    segs = Segs(); kind = 'plain'; mode = 'line'
    lastfix = None

    def flush():
        nonlocal segs, kind
        if mode == 'line':
            line(segs, kind)
        else:
            item(segs)
        segs = Segs(); kind = 'plain'

    for t in toks:
        if t == '<lb/>':
            if mode == 'line':
                flush()
            continue
        if t == '<list>':
            if mode == 'line':
                flush()
            if st['owner'] is not None:
                st['owner'].had_list = True
            st['saved_pp'] = st['pp']; st['adopter'] = None; mode = 'item'
            continue
        if t == '</list>':
            if mode == 'item':
                ad = st['adopter']
                st['pp'] = ad if (ad is not None and ad.kids) else st['saved_pp']
                st['maybe_pp'] = ad if (ad is not None and not ad.kids) else None
                st['adopter'] = None; mode = 'line'
            continue
        if t == '<item>':
            segs = Segs(); continue
        if t == '</item>':
            if mode == 'item':
                prev = st['adopter']
                # a refless item following an adopter becomes the new adopter at the owner level
                if prev is not None and clean(segs.s[0][0]) and not segs.s[0][1] and len(segs.s) == 1 \
                        and not re.match(r'(?i)see\b', clean(segs.s[0][0])):
                    st['adopter'] = None
                flush()
            continue
        if t.startswith('<ref'):
            mo = re.search(r'osisRef="([^"]*)"', t)
            segs.ref(mo.group(1) if mo else None)
            lastfix = (segs.s[-1][1], mo.group(1)) if mo and FIXABLE.match(mo.group(1)) else None
            if not mo:
                # <ref target="Nave:X">X</ref>: part of a "See X" name, keep its text
                segs.s[-1][2] = False
                segs.text(re.sub(r'<[^>]*>', '', t))
            continue
        if t.startswith('<'):
            lastfix = None
            continue
        if lastfix is not None:
            t = apply_fix(lastfix, t); lastfix = None
        # text
        if mode == 'line' and not clean(segs.s[0][0]) and not segs.s[0][1] and len(segs.s) == 1:
            s = t.lstrip()
            if s.startswith('→'):
                kind = 'arrow'
            elif re.match(r'\d+\.', s):
                kind = 'num'
            elif s.startswith('.'):
                kind = 'dot'
        segs.text(t)
    if mode == 'line':
        flush()

    # entry refs: parse later (all nodes)
    return entry


def walk(n):
    yield n
    for k in n.kids:
        yield from walk(k)


def finish(n):
    """Descriptions / seeAlso: single See on a bare node becomes its description."""
    for x in walk(n):
        x.see = [s for s in dict.fromkeys(x.see) if s]
        if x.desc is None and len(x.see) == 1 and not x.refs and not x.kids:
            if x.see[0].lower().startswith('each '):
                x.desc = '(See ' + x.see[0] + ')'   # a note, not a topic name: no link in the app
            else:
                x.desc = 'See ' + x.see[0]
            x.see = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--imp', required=True)
    ap.add_argument('--template', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--report')
    a = ap.parse_args()

    txt = open(a.imp, encoding='utf-8').read()
    parts = re.split(r'^\$\$\$(.*)\n', txt, flags=re.M)[1:]
    src = sqlite3.connect('file:' + a.template + '?mode=ro', uri=True)
    oldnames = {}
    for (n,) in src.execute('select name from topic where parent_topic_id is null order by topic_id'):
        oldnames.setdefault(n.upper(), n)

    def entry_name(k):
        if k.upper() in oldnames:
            return oldnames[k.upper()]
        return re.sub(r"'S\b", "'s", k.title())

    R = Refs()
    entries = []
    for i in range(0, len(parts), 2):
        e = parse_entry(entry_name(parts[i].strip()), parts[i + 1], R)
        finish(e)
        entries.append(e)

    if os.path.exists(a.output):
        os.remove(a.output)
    out = sqlite3.connect(a.output)
    src.backup(out)
    src.close()
    cur = out.cursor()
    cur.execute('DELETE FROM verse_link'); cur.execute('DELETE FROM topic')
    cur.execute("DELETE FROM sqlite_sequence WHERE name IN ('topic','verse_link')")
    cur.execute("INSERT INTO topic_fts(topic_fts) VALUES('rebuild')")

    nlinks = 0; ntopics = 0; digest = hashlib.sha256()

    def emit(n, parent, sort):
        nonlocal nlinks, ntopics
        md = json.dumps({'seeAlso': n.see}, ensure_ascii=False, separators=(',', ':')) if n.see else None
        cur.execute('INSERT INTO topic(parent_topic_id,name,description,sort_order,metadata) VALUES (?,?,?,?,?)',
                    (parent, n.name, n.desc, sort, md))
        tid = cur.lastrowid; ntopics += 1
        digest.update(f'T{tid}|{parent}|{n.name}|{n.desc}|{sort}|{md}\n'.encode())
        seen = set(); so = 0
        for osis in n.refs:
            r = R.parse(osis)
            if r is None or r in seen:
                continue
            seen.add(r); so += 1
            cur.execute("INSERT INTO verse_link(source_type,source_id,verse_id_start,verse_id_end,link_type,sort_order) VALUES ('topic',?,?,?,'reference',?)",
                        (tid, r[0], r[1], so))
            nlinks += 1
            digest.update(f'L{tid}|{r[0]}|{r[1]}|{so}\n'.encode())
        for j, k in enumerate(n.kids):
            emit(k, tid, j + 1)

    for i, e in enumerate(entries):
        emit(e, None, i + 1)

    fix = lambda s: s.replace('ߴ', '’') if isinstance(s, str) else s
    row = cur.execute('SELECT * FROM module_info WHERE info_id=1').fetchone()
    cols = [d[0] for d in cur.description]
    upd = {c: fix(v) for c, v in zip(cols, row)}
    upd['topic_count'] = ntopics; upd['verse_count'] = nlinks
    upd['content_sha256'] = digest.hexdigest()
    upd['metadata'] = json.dumps({'source_module': 'Nave (CrossWire SWORD)', 'builder': 'nave-to-topical.py'})
    cur.execute('UPDATE module_info SET ' + ','.join(f'{c}=?' for c in upd if c != 'info_id') + ' WHERE info_id=1',
                [v for c, v in upd.items() if c != 'info_id'])
    cur.execute("INSERT INTO schema_version(version_number,notes) VALUES ('2.0.1','Rebuilt by nave-to-topical.py: nested sub-entries, chapter-only refs expanded')")
    out.commit()
    out.execute('PRAGMA journal_mode=DELETE')
    cur.execute('VACUUM')
    out.close()
    rep = {'entries': len(entries), 'topics': ntopics, 'links': nlinks,
           'chapter_only_refs': R.chapter_only, 'bad_refs': R.bad[:50], 'bad_ref_count': len(R.bad)}
    print(json.dumps(rep, indent=1))
    if a.report:
        json.dump(rep, open(a.report, 'w'), indent=1)


if __name__ == '__main__':
    main()
