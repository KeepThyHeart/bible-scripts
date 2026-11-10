"""translationWords -> dictionary module (design sections 3.1 and 6)."""
import json
import os
import re

from . import arabic_norm, identity, links, markdown_html, schema, strongs
from . import versification as _vers

_ARTICLE_RE = re.compile(r'^(?:.*/)?bible/(kt|names|other)/([^/]+)\.md$')
_MD_REL_RE = re.compile(r'\]\(\s*(?:\.{1,2}/)*(?:(kt|names|other)/)?([^/)\s]+)\.md\s*\)')
_MD_RC_RE = re.compile(r'rc\s*:\s*//[^/\s)\]]*/tw/dict/bible/(kt|names|other)/([^/\s)\]]+)', re.I)
_TITLE_RE = re.compile(r'^\s*#\s+(.*?)\s*#*\s*$')


def parse_articles(archive, persian_words=None):
    """-> (articles, arabic_counts). Each article: dict(key, category, slug, word, body_md, raw)."""
    arts, counts = [], {}
    for name in sorted(archive.names()):
        m = _ARTICLE_RE.match(name)
        if not m:
            continue
        raw = archive.read_text(name).replace('\r\n', '\n').replace('\r', '\n')
        arts.append(dict(name=name, category=m.group(1), slug=m.group(2), raw=raw))
    return arts, counts


def _split_title(raw, slug):
    lines = raw.split('\n')
    for i, ln in enumerate(lines):
        if ln.strip():
            m = _TITLE_RE.match(ln)
            if m and not ln.lstrip().startswith('##'):
                return m.group(1).strip(), '\n'.join(lines[i + 1:]).strip('\n')
            break
    return slug, raw.strip('\n')


def _related(raw, own_key, keys, slug_index):
    out = []
    for cat, slug in _MD_REL_RE.findall(raw):
        if cat:
            k = '%s/%s' % (cat, slug)
        else:
            k = slug_index.get(slug)
        if k and k in keys and k != own_key and k not in out:
            out.append(k)
    for cat, slug in _MD_RC_RE.findall(raw):
        k = '%s/%s' % (cat.lower(), slug)
        if k in keys and k != own_key and k not in out:
            out.append(k)
    return out


def build(archive, source_row, work_dir_out, registry=None, arabic_words=None):
    """Build ``dictionary_TW-xx.db`` in ``work_dir_out``; returns the report dict."""
    manifest = archive.manifest() if archive.exists('manifest.yaml') else {}
    license_text = archive.read_text('LICENSE.md') if archive.exists('LICENSE.md') else None
    lang = source_row['lang']
    is_ar = identity.iso639_1(lang) == 'ar'
    info = identity.build_module_info(manifest, 'tw', source_row, license_text, source_row.get('sha256'))

    arts, _ = parse_articles(archive)
    ar_counts = {}
    skipped = {}
    parsed = []
    seen = set()
    for a in arts:
        key = '%s/%s' % (a['category'], a['slug'])
        if key in seen:
            skipped['duplicate_key'] = skipped.get('duplicate_key', 0) + 1
            continue
        raw = a['raw']
        if not raw.strip():
            skipped['empty'] = skipped.get('empty', 0) + 1
            continue
        if is_ar:
            raw, c = arabic_norm.normalize_arabic(raw, arabic_words)
            for k, v in c.items():
                ar_counts[k] = ar_counts.get(k, 0) + v
        word, body = _split_title(raw, a['slug'])
        seen.add(key)
        parsed.append(dict(key=key, category=a['category'], slug=a['slug'], word=word, body=body, raw=raw))

    keys = {p['key'] for p in parsed}
    slug_index = {}
    for p in parsed:
        slug_index.setdefault(p['slug'], p['key'])
    for slug in [s for s in slug_index if sum(1 for p in parsed if p['slug'] == s) > 1]:
        del slug_index[slug]

    reg_in = registry
    vers = getattr(reg_in, 'versification', None) or _vers.Versification.load()
    reg = links.Registry(
        tw_keys=keys | set(getattr(reg_in, 'tw_keys', ()) or ()),
        tw_uuid=info['module_uuid'],
        obs_stories=getattr(reg_in, 'obs_stories', ()),
        obs_uuid=getattr(reg_in, 'obs_uuid', None),
        versification=vers,
        tw_words={p['key']: p['word'] for p in parsed})

    strongs_index = {}
    stats = {}
    n_links = 0
    for p in parsed:
        html = markdown_html.render(p['body'])
        res = links.resolve_links(html, reg)
        p['html'] = res.html
        p['verse_links'] = res.verse_links
        links.merge_stats(stats, res.stats)
        p['strongs'] = strongs.extract_strongs(p['raw'])
        p['related'] = _related(p['raw'], p['key'], keys, slug_index)
        for s in p['strongs']:
            strongs_index.setdefault(s, []).append(p['key'])

    out_path = os.path.join(work_dir_out, identity.file_name('tw', lang))
    os.makedirs(work_dir_out, exist_ok=True)
    conn = schema.create_db(out_path, 'dictionary', overwrite=True)
    meta = info.pop('metadata')
    if is_ar:
        meta['modifications'] += '; Persian letters normalised'
        meta['arabic_normalisation'] = ar_counts
    code = info['language_code']
    schema.insert_module_info(conn, 'dictionary', metadata=meta, **{k: v for k, v in info.items() if k != 'module_type'})
    conn.execute("UPDATE module_info SET dictionary_type='bible_dictionary', language_from=NULL, language_to=?",
                 (code,))
    for p in parsed:
        cur = conn.execute(
            'INSERT INTO dictionary_entry (entry_key, word, definition, related_words, metadata) VALUES (?,?,?,?,?)',
            (p['key'], p['word'], p['html'], json.dumps(p['related'], ensure_ascii=False),
             json.dumps({'category': p['category'], 'strongs': p['strongs']}, ensure_ascii=False)))
        eid = cur.lastrowid
        for i, (a, b) in enumerate(p['verse_links']):
            conn.execute("INSERT INTO verse_link (source_type, source_id, verse_id_start, verse_id_end, link_type,"
                         " sort_order) VALUES ('dictionary_entry', ?, ?, ?, 'reference', ?)", (eid, a, b, i))
            n_links += 1
    schema.insert_feature(conn, 'strongs_index', strongs_index)
    conn.commit()
    conn.close()

    cats = {}
    for p in parsed:
        cats[p['category']] = cats.get(p['category'], 0) + 1
    return {
        'module': out_path, 'abbreviation': info['abbreviation'], 'uuid': info['module_uuid'],
        'entries': len(parsed), 'categories': cats, 'verse_links': n_links,
        'strongs_index_size': len(strongs_index), 'related_words': sum(len(p['related']) for p in parsed),
        'skipped': skipped, 'links': stats, 'arabic_replacements': ar_counts,
    }
