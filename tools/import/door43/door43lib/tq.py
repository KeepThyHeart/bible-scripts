"""translationQuestions -> quiz module (design section 3.3).

TSV columns ``Reference ID Tags Quote Occurrence Question Response``; legacy Markdown
uses ``<bk>/<cc>/<vv>.md`` files holding ``# question`` + answer pairs. ``build`` returns a
report dict.
"""
import json
import os
import re

from . import books, fetch, identity, refs, schema, versification
from .tn import (Normalizer, final_pragmas, find_legacy_files, find_tsv_files, finish_module_info, chunk_end,
                 piece_ranges, read_tsv, strip_legacy_ta)
from . import markdown_html


def _plain(s):
    return markdown_html.unescape_tsv(s).strip()


def build(archive, source_row, out_dir, registry=None, arabic_words=None, versification_obj=None):
    """Build ``quiz_TQ-xx.db`` in ``out_dir`` from an opened ``fetch.Archive``; return a report dict.
    ``registry`` is accepted for API symmetry and unused."""
    vers = versification_obj or versification.Versification.load()
    lang = source_row.get('lang') or ''
    norm = Normalizer(lang, arabic_words)
    skipped = {}
    questions = []  # dicts

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    layout = None
    keys = set()

    def add(book, ref_ranges, key, prompt, answer, tags, meta, ref_text):
        if not prompt.strip():
            skip('empty_question')
            return
        base, n = key, 1
        while key in keys:
            n += 1
            key = f'{base}-{n}'
        keys.add(key)
        questions.append({'key': key, 'prompt': norm(prompt.strip()), 'answer': norm(answer.strip()) if answer.strip() else None,
                          'tags': tags, 'meta': meta, 'ranges': ref_ranges})

    tsvs = find_tsv_files(archive)
    if tsvs:
        layout = 'tsv'
        for book, name in tsvs:
            header, rows = read_tsv(archive.read_text(name))
            idx = {h: i for i, h in enumerate(header)}
            if 'Question' not in idx:
                skip('unknown_header')
                continue
            code = books.usfm_code(book)
            for r in rows:
                f = {h: r[i] for h, i in idx.items()}
                try:
                    ref = refs.parse_reference(f.get('Reference', ''), book=book)
                except refs.RefError:
                    skip('malformed_reference')
                    continue
                if ref.level not in ('verse', 'passage'):
                    skip('non_verse_reference')
                    continue
                try:
                    ranges = piece_ranges(vers, ref)
                except versification.UnmappedVerse:
                    skip('unmapped_verse')
                    continue
                qid = f.get('ID', '').strip()
                if not qid:
                    skip('missing_id')
                    continue
                tags = [t.strip().lower() for t in re.split(r'[,;]', f.get('Tags', '')) if t.strip()]
                meta = {'id': qid, 'reference': f['Reference']}
                if f.get('Quote', '').strip():
                    meta['quote'] = f['Quote'].strip()
                    meta['occurrence'] = f.get('Occurrence', '')
                add(book, ranges, f'{code}-{qid}', _plain(f['Question']), _plain(f.get('Response', '')),
                    tags, meta, f['Reference'])
    else:
        legacy = [t for t in find_legacy_files(archive) if isinstance(t[2], int) and t[1] is not None]
        if not legacy:
            raise ValueError('no translationQuestions files found (no TSV, no <bk>/<cc>/<vv>.md)')
        layout = 'markdown'
        starts = {}
        for b, ch, v, _ in legacy:
            starts.setdefault((b, ch), []).append(v)
        for k in starts:
            starts[k].sort()
        for b, ch, v, name in legacy:
            code = books.usfm_code(b)
            nxt = next((x for x in starts[(b, ch)] if x > v), None)
            try:
                end = max(v, chunk_end(vers, b, ch, v, nxt))
                ref = refs.Ref(b, ch, v, ch, end, 'verse' if end == v else 'passage')
                ranges = piece_ranges(vers, ref)
            except versification.UnmappedVerse:
                skip('unmapped_verse')
                continue
            for n, (q, a) in enumerate(_legacy_pairs(strip_legacy_ta(archive.read_text(name), lang)), 1):
                add(b, ranges, f'{code}-{ch:02d}-{v:02d}-{n}', q, a, [], {'file': name}, name)

    cols = finish_module_info(source_row, archive, 'tq', norm.counts)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, identity.file_name('tq', source_row.get('lang') or source_row['door43_lang']))
    conn = schema.create_db(path, 'quiz', overwrite=True)
    n_links = 0
    src_id = 'door43-' + source_row['repo']
    with conn:
        cols = dict(cols)
        cols.pop('module_type')
        schema.insert_module_info(conn, 'quiz', **cols)
        md = cols['metadata']
        schema.insert_data_source(
            conn, src_id, cols['full_name'], 'CC BY-SA 4.0', url=cols['source_url'],
            attribution='; '.join(x for x in (md.get('attribution'), 'Original work available at unfoldingword.org') if x),
            notes=md.get('modifications'))
        for i, q in enumerate(questions, 1):
            conn.execute(
                'INSERT INTO quiz_question (question_id, question_key, kind, answer_mode, prompt, answer, tags, source_id,'
                ' review_status, sort_order, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (i, q['key'], 'comprehension', 'free_response' if q['answer'] else 'reflection', q['prompt'],
                 q['answer'], json.dumps(q['tags'], ensure_ascii=False) if q['tags'] else None, src_id,
                 'unreviewed', i, json.dumps(q['meta'], ensure_ascii=False)))
            for order, (s, t) in enumerate(q['ranges']):
                conn.execute('INSERT INTO verse_link (source_type, source_id, verse_id_start, verse_id_end, link_type,'
                             ' sort_order) VALUES (?,?,?,?,?,?)', ('quiz_question', i, s, t, 'primary_passage', order))
                n_links += 1
        final_pragmas(conn)
    conn.close()
    return {'kind': 'tq', 'path': path, 'layout': layout, 'questions': len(questions), 'verse_links': n_links,
            'skipped': skipped, 'persian_replacements': dict(norm.counts)}


def _legacy_pairs(text):
    """``# question`` lines each followed by answer lines -> [(question, answer)]."""
    pairs, q, buf = [], None, []
    for ln in text.replace('\r\n', '\n').split('\n'):
        m = re.match(r'^#\s+(.*?)\s*#*\s*$', ln)
        if m and not ln.startswith('##'):
            if q is not None:
                pairs.append((q, ' '.join(x.strip() for x in buf if x.strip())))
            q, buf = m.group(1), []
        else:
            buf.append(ln)
    if q is not None:
        pairs.append((q, ' '.join(x.strip() for x in buf if x.strip())))
    return pairs
