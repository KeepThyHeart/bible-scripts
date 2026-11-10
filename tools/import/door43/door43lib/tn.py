"""translationNotes -> commentary module (design section 3.2).

Three source layouts: the 7-column TSV (``Reference ID Tags SupportReference Quote
Occurrence Note``), the legacy 9-column TSV (``Book Chapter Verse ID SupportReference
OrigQuote Occurrence GLQuote OccurrenceNote``) and legacy Markdown, one file per chunk
(``<bk>/<cc>/<vv>.md``). One commentary entry per distinct Reference; every reference is
mapped ufw -> KJV. ``build`` returns a report dict.

Helpers shared with tq.py: ``read_tsv``, ``unquote_cell``, ``book_from_filename``,
``find_legacy_files``, ``chunk_end``, ``ref_range``, ``scope_range``, ``strip_legacy_ta``.
"""
import html
import json
import os
import re

from . import arabic_norm, books, fetch, identity, markdown_html, refs, schema, versification

TSV_NAME_RE = re.compile(r'\.tsv$', re.I)
LEGACY_RE = re.compile(r'(?:^|/)([0-9a-z]{3})/(front|\d{1,3})/(intro|\d{1,3})\.md$', re.I)

# words that introduce an old plain translationAcademy reference, e.g. "(veja: figs_you)"
SEE_WORDS = {'en': ['see'], 'pt': ['veja', 'ver'], 'es': ['ver', 'vea'], 'fr': ['voir'],
             'hi': ['देखें'], 'bn': ['দেখুন'], 'ar': ['أنظر', 'انظر']}


# ---------------------------------------------------------------- generic helpers

def _links_module():
    try:
        from . import links
        return links
    except ImportError:
        return None


_load_links = _links_module  # tests patch this


def unquote_cell(cell):
    """``"a ""b"" c"`` -> ``a "b" c``; anything not wrapped in quotes is returned unchanged."""
    if len(cell) >= 2 and cell[0] == '"' and cell[-1] == '"':
        return cell[1:-1].replace('""', '"')
    return cell


def read_tsv(text):
    """Return ``(header, rows)``. Rows are lists padded/merged to the header width, cells unquoted.
    Blank lines are dropped."""
    lines = [ln for ln in text.replace('\r\n', '\n').replace('\r', '\n').lstrip('﻿').split('\n')]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return [], []
    header = [h.strip() for h in lines[0].split('\t')]
    n = len(header)
    rows = []
    for ln in lines[1:]:
        cells = ln.split('\t')
        if len(cells) > n:
            cells = cells[:n - 1] + ['\t'.join(cells[n - 1:])]
        cells += [''] * (n - len(cells))
        rows.append([unquote_cell(c) for c in cells])
    return header, rows


def book_from_filename(name):
    """``tn_3JN.tsv`` / ``es-419_tn_57-TIT.tsv`` / ``65-3JN.tsv`` -> book number or None."""
    stem = os.path.basename(name)
    stem = TSV_NAME_RE.sub('', stem)
    return books.try_book_number(stem.split('_')[-1])


def find_tsv_files(archive):
    """[(book_number, member_name)] sorted by book number."""
    out = []
    for n in archive.names():
        if TSV_NAME_RE.search(n):
            b = book_from_filename(n)
            if b:
                out.append((b, n))
    return sorted(out)


def find_legacy_files(archive):
    """[(book, chapter_or_None, verse_or_None_or_'intro', member)] for ``<bk>/<cc>/<vv>.md`` layouts.
    chapter None = front; verse 'intro' for intro files."""
    out = []
    for n in archive.names():
        m = LEGACY_RE.search(n)
        if not m:
            continue
        b = books.try_book_number(m.group(1))
        if not b:
            continue
        ch = None if m.group(2).lower() == 'front' else int(m.group(2))
        v = 'intro' if m.group(3).lower() == 'intro' else int(m.group(3))
        out.append((b, ch, v, n))
    return sorted(out, key=lambda t: (t[0], t[1] or 0, 0 if t[2] == 'intro' else t[2]))


def strip_legacy_ta(text, lang='en'):
    """Remove old plain tA references such as ``(veja: figs_you)``."""
    words = list(dict.fromkeys(SEE_WORDS.get(lang, []) + SEE_WORDS['en']))
    alt = '|'.join(re.escape(w) for w in words)
    pat = r'[ \t]*[(（]\s*(?:%s)\s*[:：]\s*[\w\-]+(?:\s*[,;]\s*[\w\-]+)*\s*[)）]' % alt
    return re.sub(pat, '', text, flags=re.I)


def _chapter_len(vers, book, ch):
    counts = (vers.kjv_counts or {}).get(book) or []
    if not 1 <= ch <= len(counts):
        raise versification.UnmappedVerse(f'book {book} chapter {ch} not in KJV')
    return counts[ch - 1]


def source_chapter_len(vers, book, ch):
    """Verses of a chapter in ufw numbering: KJV count plus verses merged into it."""
    n = _chapter_len(vers, book, ch)
    extra = [m.v for m in vers.merges.values() if m.book == book and m.ch == ch and m.v > n]
    return max([n] + extra)


def chunk_end(vers, book, ch, start, next_start):
    """Last source verse of a chunk: one before the next chunk, else the end of the chapter."""
    if next_start is not None:
        return next_start - 1
    return source_chapter_len(vers, book, ch)


def ref_range(vers, ref):
    """(start_id, end_id) KJV verse ids for a verse/passage Ref (raises UnmappedVerse)."""
    pieces = ref.pieces or ((ref.ch, ref.v1, ref.ch2, ref.v2),)
    ids = []
    for (c1, v1, c2, v2) in pieces:
        a = books.verse_id(ref.book, *vers.map_verse(ref.book, c1, v1))
        b = books.verse_id(ref.book, *vers.map_verse(ref.book, c2, v2))
        ids.append((min(a, b), max(a, b)))
    return min(i[0] for i in ids), max(i[1] for i in ids)


def piece_ranges(vers, ref):
    """One (start_id, end_id) per piece of the reference."""
    pieces = ref.pieces or ((ref.ch, ref.v1, ref.ch2, ref.v2),)
    out = []
    for (c1, v1, c2, v2) in pieces:
        a = books.verse_id(ref.book, *vers.map_verse(ref.book, c1, v1))
        b = books.verse_id(ref.book, *vers.map_verse(ref.book, c2, v2))
        out.append((min(a, b), max(a, b)))
    return out


def scope_range(vers, ref):
    """Whole-chapter or whole-book KJV range for a chapter/book Ref."""
    counts = (vers.kjv_counts or {}).get(ref.book) or []
    if not counts:
        raise versification.UnmappedVerse(f'book {ref.book} not in KJV')
    if ref.level == 'book':
        return books.verse_id(ref.book, 1, 1), books.verse_id(ref.book, len(counts), counts[-1])
    n = _chapter_len(vers, ref.book, ref.ch)
    return books.verse_id(ref.book, ref.ch, 1), books.verse_id(ref.book, ref.ch, n)


def count_words(html_text):
    return len(re.sub(r'<[^>]+>', ' ', html_text).split())


def final_pragmas(conn):
    conn.commit()
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.execute('PRAGMA journal_mode=DELETE')


def finish_module_info(source, archive, kind, norm_counts):
    """module_info columns (dict, metadata a dict) with Arabic counts folded in."""
    manifest = {}
    for mname in ('manifest.yaml', 'manifest.yml'):
        if archive.exists(mname):
            try:
                manifest = archive.manifest(mname)
            except Exception:
                manifest = {}
            break
    lic = archive.read_text('LICENSE.md') if archive.exists('LICENSE.md') else None
    cols = identity.build_module_info(manifest, kind, source, lic, zip_sha256=fetch.sha256_file(archive.path))
    if norm_counts:
        cols['metadata']['modifications'] += '; Persian letters normalised (%s)' % ', '.join(
            '%s x%d' % (k, v) for k, v in sorted(norm_counts.items()))
        cols['metadata']['persian_replacements'] = dict(norm_counts)
    return cols


class Normalizer:
    """Arabic normalisation (only for language ar) with running counts."""

    def __init__(self, lang, words=None):
        self.active = (lang or '').lower() == 'ar'
        self.words = words
        self.counts = {}

    def __call__(self, text):
        if not self.active or not text:
            return text
        out, c = arabic_norm.normalize_arabic(text, self.words)
        for k, v in c.items():
            self.counts[k] = self.counts.get(k, 0) + v
        return out


# ---------------------------------------------------------------- build

class _Entry:
    def __init__(self, ref):
        self.ref = ref
        self.parts = []   # html fragments
        self.notes = []   # metadata notes
        self.links = []   # (start, end) from the resolver


def build(archive, source_row, out_dir, registry=None, arabic_words=None, versification_obj=None):
    """Build ``commentary_TN-xx.db`` in ``out_dir`` from an opened ``fetch.Archive``; return a report dict."""
    vers = versification_obj or versification.Versification.load()
    lang = source_row.get('lang') or ''
    norm = Normalizer(lang, arabic_words)
    skipped = {}
    stats = {}
    lk = _load_links()
    if lk is not None and registry is None:
        registry = lk.Registry(versification=vers)

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    def resolve(html_text, entry, book, chapter):
        if lk is None:
            stats['links_unavailable'] = stats.get('links_unavailable', 0) + 1
            return html_text
        res = lk.resolve_links(html_text, registry, book=book, chapter=chapter)
        entry.links.extend(res.verse_links)
        for k, v in (res.stats or {}).items():
            if isinstance(v, (int, float)):
                stats[k] = stats.get(k, 0) + v
            elif isinstance(v, dict):
                d = stats.setdefault(k, {})
                for kk, vv in v.items():
                    if isinstance(vv, (int, float)):
                        d[kk] = d.get(kk, 0) + vv
                    elif isinstance(vv, list):
                        lst = d.setdefault(kk, [])
                        lst.extend(vv[:max(0, 20 - len(lst))])
            elif isinstance(v, list):
                lst = stats.setdefault(k, [])
                lst.extend(v[:max(0, 20 - len(lst))])
        return res.html

    entries = {}   # key -> _Entry (insertion ordered)
    layout = None
    tsvs = find_tsv_files(archive)
    if tsvs:
        for book, name in tsvs:
            header, rows = read_tsv(archive.read_text(name))
            if header and header[0] == 'Reference':
                layout = layout or 'tsv7'
                _tsv7(header, rows, book, vers, norm, entries, skip, resolve)
            elif header[:3] == ['Book', 'Chapter', 'Verse']:
                layout = layout or 'tsv9'
                _tsv9(header, rows, book, vers, norm, entries, skip, resolve)
            else:
                skip('unknown_header')
    else:
        legacy = find_legacy_files(archive)
        if legacy:
            layout = 'markdown'
            _legacy(archive, legacy, lang, vers, norm, entries, skip, resolve)
    if layout is None:
        raise ValueError('no translationNotes files found (no TSV, no <bk>/<cc>/<vv>.md)')

    cols = finish_module_info(source_row, archive, 'tn', norm.counts)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, identity.file_name('tn', source_row.get('lang') or source_row['door43_lang']))
    conn = schema.create_db(path, 'commentary', overwrite=True)
    n_links = n_notes = 0
    with conn:
        cols = dict(cols)
        cols.pop('module_type')
        schema.insert_module_info(conn, 'commentary', **cols)
        for e in entries.values():
            ref = e.ref
            if ref.level in ('verse', 'passage'):
                start, end = ref_range(vers, ref)
                meta = {'book': books.usfm_code(ref.book), 'chapter': ref.ch}
                scope = None
            else:
                start = end = None
                meta = {'book': books.usfm_code(ref.book)}
                if ref.level == 'chapter':
                    meta['chapter'] = ref.ch
                scope = scope_range(vers, ref)
            meta['notes'] = e.notes
            n_notes += len(e.notes)
            content = '\n'.join(e.parts)
            cur = conn.execute(
                'INSERT INTO commentary_entry (verse_id_start, verse_id_end, entry_level, content, word_count, metadata)'
                ' VALUES (?,?,?,?,?,?)',
                (start, end, ref.level, content,
                 count_words(content), json.dumps(meta, ensure_ascii=False)))
            eid = cur.lastrowid
            order = 0
            seen = set()
            def add(s, t, ltype):
                nonlocal order, n_links
                if (s, t, ltype) in seen:
                    return
                seen.add((s, t, ltype))
                conn.execute('INSERT INTO verse_link (source_type, source_id, verse_id_start, verse_id_end, link_type,'
                             ' sort_order) VALUES (?,?,?,?,?,?)', ('commentary_entry', eid, s, t, ltype, order))
                order += 1
                n_links += 1
            if scope:
                add(scope[0], scope[1], 'scope')
            if ref.pieces and len(ref.pieces) > 1:
                for s, t in piece_ranges(vers, ref):
                    add(s, t, 'reference')
            for s, t in e.links:
                add(s, t, 'reference')
        final_pragmas(conn)
    conn.close()
    return {'kind': 'tn', 'path': path, 'layout': layout, 'entries': len(entries), 'notes': n_notes,
            'verse_links': n_links, 'skipped': skipped, 'links': stats,
            'persian_replacements': dict(norm.counts)}


def _entry(entries, ref):
    e = entries.get(ref)
    if e is None:
        e = entries[ref] = _Entry(ref)
    return e


def _quote_head(quote, book):
    if not quote:
        return ''
    lang = 'hbo' if book <= 39 else 'grc'
    return '<h4><span lang="%s">%s</span></h4>' % (lang, html.escape(quote, quote=False))


def _add_note(entries, ref, row, fields, book, norm, skip, resolve):
    note = fields['Note']
    if not note.strip():
        skip('empty_note')
        return
    quote = norm(fields.get('Quote', '').strip())
    e = _entry(entries, ref)
    body = markdown_html.render(norm(markdown_html.unescape_tsv(note)))
    body = resolve(body, e, book, ref.ch)
    e.parts.append(_quote_head(quote, book) + body)
    e.notes.append({'id': fields.get('ID', ''), 'tags': fields.get('Tags', ''),
                    'support_reference': fields.get('SupportReference', ''),
                    'quote': quote, 'occurrence': fields.get('Occurrence', '')})


def _check_ref(vers, ref, skip):
    try:
        if ref.level in ('verse', 'passage'):
            ref_range(vers, ref)
        else:
            scope_range(vers, ref)
    except versification.UnmappedVerse:
        skip('unmapped_verse')
        return False
    return True


def _tsv7(header, rows, book, vers, norm, entries, skip, resolve):
    idx = {h: i for i, h in enumerate(header)}
    for r in rows:
        f = {h: r[i] for h, i in idx.items()}
        try:
            ref = refs.parse_reference(f['Reference'], book=book)
        except (refs.RefError, KeyError):
            skip('malformed_reference')
            continue
        if 'Note' not in f:
            skip('no_note_column')
            continue
        if not _check_ref(vers, ref, skip):
            continue
        _add_note(entries, ref, r, f, book, norm, skip, resolve)


def _tsv9(header, rows, book, vers, norm, entries, skip, resolve):
    idx = {h: i for i, h in enumerate(header)}
    for r in rows:
        f = {h: r[i] for h, i in idx.items()}
        ch, v = f.get('Chapter', '').strip(), f.get('Verse', '').strip()
        text = 'front:intro' if ch.lower() == 'front' else f'{ch}:{v}'
        try:
            ref = refs.parse_reference(text, book=book)
        except refs.RefError:
            skip('malformed_reference')
            continue
        if not _check_ref(vers, ref, skip):
            continue
        g = {'Note': f.get('OccurrenceNote', ''), 'Quote': f.get('OrigQuote', ''), 'ID': f.get('ID', ''),
             'Tags': '', 'SupportReference': f.get('SupportReference', ''), 'Occurrence': f.get('Occurrence', '')}
        _add_note(entries, ref, r, g, book, norm, skip, resolve)


def parse_legacy_blocks(text):
    """``# quote`` blocks, each followed by a note -> [(quote, note_markdown)]."""
    blocks, quote, buf = [], None, []
    for ln in text.replace('\r\n', '\n').split('\n'):
        m = re.match(r'^#\s+(.*?)\s*#*\s*$', ln)
        if m and not ln.startswith('##'):
            if quote is not None:
                blocks.append((quote, '\n'.join(buf).strip()))
            quote, buf = m.group(1), []
        else:
            buf.append(ln)
    if quote is not None:
        blocks.append((quote, '\n'.join(buf).strip()))
    return blocks


def _legacy(archive, files, lang, vers, norm, entries, skip, resolve):
    # next chunk start per (book, chapter)
    verses = {}
    for b, ch, v, _ in files:
        if isinstance(v, int):
            verses.setdefault((b, ch), []).append(v)
    for k in verses:
        verses[k].sort()
    for b, ch, v, name in files:
        text = archive.read_text(name)
        if v == 'intro':
            ref = (refs.Ref(b, None, None, None, None, 'book') if ch is None
                   else refs.Ref(b, ch, None, ch, None, 'chapter'))
            if not _check_ref(vers, ref, skip):
                continue
            body_md = norm(strip_legacy_ta(text, lang))
            if not body_md.strip():
                skip('empty_note')
                continue
            e = _entry(entries, ref)
            e.parts.append(resolve(markdown_html.render(body_md), e, b, ch))
            e.notes.append({'id': '', 'tags': '', 'support_reference': '', 'quote': '', 'occurrence': ''})
            continue
        if ch is None:
            skip('malformed_reference')
            continue
        vs = verses[(b, ch)]
        nxt = next((x for x in vs if x > v), None)
        try:
            end = chunk_end(vers, b, ch, v, nxt)
        except versification.UnmappedVerse:
            skip('unmapped_verse')
            continue
        end = max(end, v)
        ref = refs.Ref(b, ch, v, ch, end, 'verse' if end == v else 'passage')
        # a merged ufw verse may collapse the range onto one KJV verse
        try:
            s, t = ref_range(vers, ref)
        except versification.UnmappedVerse:
            skip('unmapped_verse')
            continue
        if s == t:
            ref = refs.Ref(b, ch, v, ch, end, 'verse')
        for quote, note in parse_legacy_blocks(strip_legacy_ta(text, lang)):
            if not note.strip():
                skip('empty_note')
                continue
            e = _entry(entries, ref)
            head = '<h4>%s</h4>' % html.escape(norm(quote), quote=False) if quote else ''
            e.parts.append(head + resolve(markdown_html.render(norm(note)), e, b, ch))
            e.notes.append({'id': '', 'tags': '', 'support_reference': '', 'quote': quote, 'occurrence': ''})
