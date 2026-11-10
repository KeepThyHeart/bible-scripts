"""Open Bible Stories -> book module (design section 3.4)."""
import json
import os
import re

from . import arabic_norm, books, identity, links, markdown_html, schema
from . import versification as _vers

HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCES_PATH = os.path.join(HERE, '..', 'data', 'obs-references.json')

_IMG_RE = re.compile(r'^\s*!\[[^\]]*\]\(([^)]*)\)\s*$')
_FRAME_RE = re.compile(r'obs-[^/]*?-(\d{2})-(\d{2})\.\w+\s*$', re.I)
_TITLE_RE = re.compile(r'^\s*#\s+(.*?)\s*#*\s*$')
_REF_RE = re.compile(r'^\s*_(.+)_\s*$')
_RANGE_RE = re.compile(r'^([1-3]?[A-Z]{2,3})\s+(\d+):(\d+)(?:-(?:(\d+):)?(\d+))?$')


def parse_range(text):
    """'GEN 1:1-3:24' / 'ACT 3:1-4:22' / 'JHN 3:16' / 'MAT 14:13-21' -> (start_id, end_id)."""
    m = _RANGE_RE.match(text.strip())
    if not m:
        raise ValueError('bad OBS reference: %r' % text)
    bk = books.book_number(m.group(1))
    c1, v1 = int(m.group(2)), int(m.group(3))
    if m.group(5) is None:
        c2, v2 = c1, v1
    else:
        c2 = int(m.group(4)) if m.group(4) else c1
        v2 = int(m.group(5))
    return books.verse_id(bk, c1, v1), books.verse_id(bk, c2, v2)


def load_references(path=None):
    """{story_number: [(start_id, end_id), ...]} from data/obs-references.json."""
    with open(path or REFERENCES_PATH, encoding='utf-8') as f:
        data = json.load(f)
    return {int(k): [parse_range(r) for r in v] for k, v in data['stories'].items()}


def parse_story(md):
    """-> dict(title, frames=[(frame_id|None, text)], reference|None, story|None)."""
    title, reference, story = None, None, None
    frames = []
    cur_frame = None
    para = []

    def flush():
        nonlocal para
        t = ' '.join(s.strip() for s in para).strip()
        if t:
            frames.append((cur_frame, t))
        para = []

    for ln in md.replace('\r\n', '\n').split('\n'):
        if title is None and _TITLE_RE.match(ln) and not ln.lstrip().startswith('##'):
            title = _TITLE_RE.match(ln).group(1).strip()
            continue
        im = _IMG_RE.match(ln)
        if im:
            flush()
            fm = _FRAME_RE.search(im.group(1))
            cur_frame = fm.group(2) if fm else None
            if fm:
                story = int(fm.group(1))
            continue
        if not ln.strip():
            flush()
            continue
        para.append(ln)
    flush()
    if frames:
        rm = _REF_RE.match(frames[-1][1])
        if rm:
            reference = rm.group(1).strip()
            frames.pop()
    return dict(title=title, frames=frames, reference=reference, story=story)


def _norm(text, is_ar, words, counts):
    if not is_ar:
        return text
    out, c = arabic_norm.normalize_arabic(text, words)
    for k, v in c.items():
        counts[k] = counts.get(k, 0) + v
    return out


def _story_html(parsed, is_ar, words, counts):
    parts, frame_ids = [], []
    for fid, text in parsed['frames']:
        h = markdown_html.render_inline(_norm(text, is_ar, words, counts))
        if fid:
            parts.append('<p data-frame="%s">%s</p>' % (fid, h))
            frame_ids.append(fid)
        else:
            parts.append('<p>%s</p>' % h)
    if parsed['reference']:
        parts.append('<p class="obs-reference"><em>%s</em></p>'
                     % markdown_html.render_inline(_norm(parsed['reference'], is_ar, words, counts)))
    return '\n'.join(parts), frame_ids


def _extra_section(archive, folder):
    """front/ or back/ files -> (title, html) or None."""
    names = sorted(n for n in archive.names('content/%s/' % folder) if n.endswith('.md'))
    if not names:
        return None
    title, parts = None, []
    for n in names:
        md = archive.read_text(n)
        if title is None:
            for ln in md.split('\n'):
                m = _TITLE_RE.match(ln)
                if m:
                    title = m.group(1).strip()
                    break
        parts.append(markdown_html.render(md))
    return (title or folder.capitalize()), '\n'.join(parts)


def build(archive, source_row, work_dir_out, registry=None, arabic_words=None, references_path=None):
    """Build ``book_OBS-xx.db`` in ``work_dir_out``; returns the report dict."""
    manifest = archive.manifest() if archive.exists('manifest.yaml') else {}
    license_text = archive.read_text('LICENSE.md') if archive.exists('LICENSE.md') else None
    lang = source_row['lang']
    is_ar = identity.iso639_1(lang) == 'ar'
    info = identity.build_module_info(manifest, 'obs', source_row, license_text, source_row.get('sha256'))
    refs_by_story = load_references(references_path)

    ar_counts, skipped = {}, {}
    sections = []  # (number, title, html, frames, story_number)
    front = _extra_section(archive, 'front')
    if front:
        sections.append((0, front[0], _norm(front[1], is_ar, arabic_words, ar_counts), [], None))
    for name in sorted(archive.names('content/')):
        m = re.fullmatch(r'content/(\d{1,2})\.md', name)
        if not m:
            continue
        n = int(m.group(1))
        if not 1 <= n <= 50:
            skipped['story_out_of_range'] = skipped.get('story_out_of_range', 0) + 1
            continue
        parsed = parse_story(archive.read_text(name))
        if not parsed['title'] or not parsed['frames']:
            skipped['empty_story'] = skipped.get('empty_story', 0) + 1
            continue
        html, frame_ids = _story_html(parsed, is_ar, arabic_words, ar_counts)
        title = _norm(parsed['title'], is_ar, arabic_words, ar_counts)
        sections.append((n, title, html, frame_ids, n))
    back = _extra_section(archive, 'back')
    if back:
        sections.append((51, back[0], _norm(back[1], is_ar, arabic_words, ar_counts), [], None))

    out_path = os.path.join(work_dir_out, identity.file_name('obs', lang))
    os.makedirs(work_dir_out, exist_ok=True)
    conn = schema.create_db(out_path, 'book', overwrite=True)
    meta = info.pop('metadata')
    if is_ar:
        meta['modifications'] += '; Persian letters normalised'
        meta['arabic_normalisation'] = ar_counts
    schema.insert_module_info(conn, 'book', metadata=meta, **{k: v for k, v in info.items() if k != 'module_type'})
    n_links = 0
    for number, title, html, frame_ids, story in sections:
        words = len(re.sub(r'<[^>]+>', ' ', html).split())
        cur = conn.execute(
            'INSERT INTO book_section (parent_section_id, section_number, sort_order, title, content, word_count,'
            ' metadata) VALUES (NULL, ?, ?, ?, ?, ?, ?)',
            (str(number), number, title, html, words, json.dumps({'frames': frame_ids}, ensure_ascii=False)))
        sid = cur.lastrowid
        for i, (a, b) in enumerate(refs_by_story.get(story, []) if story else []):
            conn.execute("INSERT INTO verse_link (source_type, source_id, verse_id_start, verse_id_end, link_type,"
                         " sort_order) VALUES ('book_section', ?, ?, ?, 'reference', ?)", (sid, a, b, i))
            n_links += 1
    conn.commit()
    conn.close()
    missing = sorted(set(range(1, 51)) - {s[0] for s in sections})
    return {
        'module': out_path, 'abbreviation': info['abbreviation'], 'uuid': info['module_uuid'],
        'sections': len(sections), 'stories': sum(1 for s in sections if s[4]),
        'frames': sum(len(s[3]) for s in sections), 'verse_links': n_links,
        'missing_stories': missing, 'skipped': skipped, 'arabic_replacements': ar_counts,
    }
