#!/usr/bin/env python3
"""Door43 importers CLI (design: docs/door43-importers.md).

  door43.py fetch   <source-id|lang|all> --work-dir W [--local-zip ID=PATH ...]
  door43.py build   <source-id|lang|all> --work-dir W [--ref-dir DIR_OF_0220_DBS]
  door43.py compare <source-id|lang|all> --work-dir W --ref-dir DIR
  door43.py report  --work-dir W

Output: W/out/<lang>/*.db, W/reports/<abbr>.json, W/reports/SUMMARY.md.
"""
import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from door43lib import (bible_build, check_module, compare as compare_mod, fetch, identity, links, obs, tn,  # noqa: E402
                       tq, tw)
from door43lib import versification as vers_mod  # noqa: E402

HELP_ORDER = ('tw', 'obs', 'tn', 'tq')


def select(selector, sources):
    """Source rows matching an id, a language (``lang`` or ``door43_lang``) or ``all``."""
    if selector == 'all':
        return list(sources)
    rows = [s for s in sources if s['id'] == selector]
    if not rows:
        rows = [s for s in sources if selector in (s['lang'], s.get('door43_lang'))]
    if not rows:
        raise SystemExit('no source matches %r' % selector)
    return rows


def _report_path(work, abbr):
    return os.path.join(work, 'reports', '%s.json' % abbr)


def write_report(work, abbr, rep):
    os.makedirs(os.path.join(work, 'reports'), exist_ok=True)
    with open(_report_path(work, abbr), 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
        f.write('\n')


def cmd_fetch(args):
    sources = fetch.load_sources(args.sources)
    work = fetch.work_dir(args.work_dir)
    local = dict(x.split('=', 1) for x in args.local_zip or [])
    rc = 0
    for src in select(args.source, sources):
        try:
            if src['id'] in local:
                dest = fetch.cache_path(work, src)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(local[src['id']], dest)
            path, digest = fetch.fetch(src['id'], work, sources_path=args.sources)
            print('%s: %s sha256=%s' % (src['id'], path, digest))
        except Exception as e:  # report and go on with the next source
            rc = 1
            print('%s: FAILED %s: %s' % (src['id'], type(e).__name__, e), file=sys.stderr)
    return rc


def _db_path(rep):
    return rep.get('module') or rep.get('path') or rep.get('file')


def _read_registry_parts(db):
    """tW keys/words/uuid from a built dictionary DB."""
    conn = sqlite3.connect(db)
    try:
        uuid = conn.execute('SELECT module_uuid FROM module_info').fetchone()[0]
        words = {k: w for k, w in conn.execute('SELECT entry_key, word FROM dictionary_entry')}
    finally:
        conn.close()
    return uuid, words


def _obs_stories(db):
    conn = sqlite3.connect(db)
    try:
        uuid = conn.execute('SELECT module_uuid FROM module_info').fetchone()[0]
        nums = [int(r[0]) for r in conn.execute('SELECT section_number FROM book_section')
                if str(r[0]).isdigit() and 1 <= int(r[0]) <= 50]
    finally:
        conn.close()
    return uuid, nums


def _finish(work, src, rep, ref_db=None):
    """Structural check, optional node validator, write the per-module report."""
    kind = src['kind']
    db = _db_path(rep)
    rep['source_id'] = src['id']
    rep['kind'] = kind
    rep['db'] = db
    abbr = rep.get('abbreviation') or identity.abbreviation(kind, src['lang'])
    rep['abbreviation'] = abbr
    rep['check_problems'] = check_module.check_module(db)
    rep['validator'] = check_module.run_node_validator(db)
    write_report(work, abbr, rep)
    return rep


def build_language(work, lang_sources, ref_dir, sources_path, vers, out_root=None):
    """Build one language's helps in the order tW, OBS, tN, tQ and its Bibles. Returns the reports."""
    reps = []
    helps = {s['kind']: s for s in lang_sources if s['kind'] in HELP_ORDER}
    bibles = [s for s in lang_sources if s['kind'] == 'bible']
    lang = lang_sources[0]['lang']
    out = os.path.join(work, 'out', lang)
    os.makedirs(out, exist_ok=True)

    def run(src, fn, **kw):
        try:
            with fetch.Archive(fetch.fetch(src['id'], work, sources_path=sources_path)[0]) as arch:
                # re-read the row: the first fetch pins the sha256
                row = fetch.get_source(src['id'], sources_path)
                rep = fn(arch, row, out, **kw)
            return _finish(work, row, rep)
        except Exception as e:
            rep = {'source_id': src['id'], 'kind': src['kind'], 'error': '%s: %s' % (type(e).__name__, e),
                   'traceback': traceback.format_exc(limit=6),
                   'abbreviation': identity.abbreviation(src['kind'], src['lang'])
                   if src['kind'] in HELP_ORDER else src['id'], 'check_problems': ['build failed']}
            write_report(work, rep['abbreviation'], rep)
            print('%s: FAILED %s' % (src['id'], rep['error']), file=sys.stderr)
            return rep

    obs_src, tw_src = helps.get('obs'), helps.get('tw')
    # tW first. Its links to OBS only need the story numbers (static) and the OBS module uuid (a pure function).
    static = links.Registry(versification=vers)
    if obs_src:
        static.obs_stories = set(obs.load_references()) or set(range(1, 51))
        static.obs_uuid = identity.module_uuid('book', identity.abbreviation('obs', obs_src['lang']),
                                               identity.iso639_1(obs_src['lang']))
    tw_rep = obs_rep = None
    if tw_src:
        tw_rep = run(tw_src, tw.build, registry=static)
        reps.append(tw_rep)
    if obs_src:
        obs_rep = run(obs_src, obs.build)
        reps.append(obs_rep)
    # real registry from the built DBs, for tN and tQ
    reg = links.Registry(versification=vers)
    if tw_rep and _db_path(tw_rep) and not tw_rep.get('error'):
        reg.tw_uuid, reg.tw_words = _read_registry_parts(_db_path(tw_rep))
        reg.tw_keys = set(reg.tw_words)
    if obs_rep and _db_path(obs_rep) and not obs_rep.get('error'):
        reg.obs_uuid, nums = _obs_stories(_db_path(obs_rep))
        reg.obs_stories = set(nums)
    for kind, fn in (('tn', tn.build), ('tq', tq.build)):
        if kind in helps:
            reps.append(run(helps[kind], fn, registry=reg, versification_obj=vers))
    for src in bibles:
        ref_db = None
        if ref_dir and src.get('ref_db_file'):
            cand = os.path.join(ref_dir, src['ref_db_file'])
            ref_db = cand if os.path.exists(cand) else None
        rep = run(src, bible_build.build, ref_db=ref_db, versification=vers)
        reps.append(rep)
        if ref_db and not rep.get('error'):
            try:
                res = compare_mod.compare(_db_path(rep), ref_db)
                compare_mod.write_report(res, os.path.join(work, 'reports'))
                rep['compare_full'] = {k: res[k] for k in ('pass', 'text_equal_pct', 'text_equal_nospace_pct',
                                                           'alignment_coverage_pct')}
                write_report(work, rep['abbreviation'], rep)
            except Exception as e:
                rep['compare_error'] = str(e)
    return reps


def cmd_build(args):
    sources = fetch.load_sources(args.sources)
    work = fetch.work_dir(args.work_dir)
    rows = select(args.source, sources)
    vers = vers_mod.Versification.load()
    by_lang = {}
    for s in rows:
        by_lang.setdefault(s['lang'], []).append(s)
    rc = 0
    for lang, group in sorted(by_lang.items()):
        for rep in build_language(work, group, args.ref_dir, args.sources, vers):
            bad = rep.get('error') or rep.get('check_problems')
            rc = 1 if bad else rc
            print('%-12s %s %s' % (rep.get('abbreviation'), 'FAILED' if rep.get('error') else
                                    ('PROBLEMS' if rep.get('check_problems') else 'ok'),
                                    rep.get('error') or '; '.join(rep.get('check_problems') or [])))
    return rc


def _load_reports(work):
    reps = []
    for p in sorted(glob.glob(os.path.join(work, 'reports', '*.json'))):
        if p.endswith('-compare.json'):
            continue
        with open(p, encoding='utf-8') as f:
            reps.append(json.load(f))
    return reps


def cmd_compare(args):
    sources = fetch.load_sources(args.sources)
    work = fetch.work_dir(args.work_dir)
    built = {r.get('source_id'): r for r in _load_reports(work)}
    rc = 0
    for src in select(args.source, sources):
        if src['kind'] != 'bible':
            continue
        rep = built.get(src['id'])
        if not rep or not rep.get('db') or not os.path.exists(rep['db']):
            print('%s: not built' % src['id'], file=sys.stderr)
            rc = 1
            continue
        ref = os.path.join(args.ref_dir, src['ref_db_file']) if args.ref_dir and src.get('ref_db_file') else None
        if not ref or not os.path.exists(ref):
            print('%s: no reference module (ref_db_file %s)' % (src['id'], src.get('ref_db_file')))
            continue
        res = compare_mod.compare(rep['db'], ref)
        compare_mod.write_report(res, os.path.join(work, 'reports'))
        print(compare_mod.format_summary(res).split('\n')[0])
        rc = rc if res['pass'] else 1
    return rc


def _link_totals(stats):
    out = {}
    for kind, b in (stats or {}).items():
        if isinstance(b, dict):
            out[kind] = b
    return out


def render_summary(work):
    reps = _load_reports(work)
    L = ['# Door43 import summary', '', '## Modules', '',
         '| module | source | entries/notes/questions/sections | verse_links | check | validator |',
         '|---|---|---|---|---|---|']
    for r in reps:
        counts = ', '.join('%s=%s' % (k, r[k]) for k in ('entries', 'notes', 'questions', 'sections', 'verses')
                           if k in r) or '-'
        check = 'ERROR %s' % r['error'] if r.get('error') else ('ok' if not r.get('check_problems')
                                                                  else '; '.join(r['check_problems']))
        L.append('| %s | %s | %s | %s | %s | %s |' % (r.get('abbreviation'), r.get('source_id'), counts,
                                                       r.get('verse_links', '-'), check,
                                                       (r.get('validator') or {}).get('status', '-')))
    L += ['', '## Skipped rows', '']
    any_skip = False
    for r in reps:
        for reason, n in sorted((r.get('skipped') or {}).items()):
            L.append('- %s: %s = %s' % (r.get('abbreviation'), reason, n))
            any_skip = True
    if not any_skip:
        L.append('(none)')
    L += ['', '## Link resolution', '', '| module | kind | resolved | unresolved | stripped |', '|---|---|---|---|---|']
    samples = []
    for r in reps:
        for kind, b in sorted(_link_totals(r.get('links')).items()):
            L.append('| %s | %s | %s | %s | %s |' % (r.get('abbreviation'), kind, b.get('resolved', 0),
                                                     b.get('unresolved', 0), b.get('stripped', 0)))
            for s in b.get('samples', [])[:3]:
                samples.append('- %s %s: %s' % (r.get('abbreviation'), kind, s))
    if samples:
        L += ['', 'Unresolved samples:', ''] + samples
    L += ['', '## Alignment coverage per book', '']
    found = False
    for p in sorted(glob.glob(os.path.join(work, 'reports', '*-compare.json'))):
        with open(p, encoding='utf-8') as f:
            c = json.load(f)
        found = True
        L += ['### %s vs %s: %s' % (c['new'].get('abbreviation'), c['ref'].get('abbreviation'),
                                   'PASS' if c['pass'] else 'FAIL'),
              'text equal %.3f%% strict, %.3f%% ignoring whitespace; coverage %.3f%%' % (
                  c['text_equal_pct'], c.get('text_equal_nospace_pct', 0.0), c['alignment_coverage_pct']), '',
              '| book | verses | coverage % | zero-alignment verses |', '|---|---|---|---|']
        for code, b in c['books'].items():
            L.append('| %s | %d | %.2f | %d |' % (code, b['verses_new'], b['coverage_pct'], b['zero_alignment_verses']))
        L.append('')
    for r in reps:
        if r.get('kind') == 'bible' and 'alignment_coverage_pct' in r and not found:
            L.append('- %s: %.3f%% (no reference compare)' % (r.get('abbreviation'), r['alignment_coverage_pct']))
    if not found and not any(r.get('kind') == 'bible' for r in reps):
        L.append('(no Bible modules)')
    return '\n'.join(L) + '\n'


def cmd_report(args):
    work = fetch.work_dir(args.work_dir)
    os.makedirs(os.path.join(work, 'reports'), exist_ok=True)
    text = render_summary(work)
    path = os.path.join(work, 'reports', 'SUMMARY.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sources', help='sources.json to use (default: data/sources.json)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('fetch', 'build', 'compare', 'report'):
        p = sub.add_parser(name)
        if name != 'report':
            p.add_argument('source', help='source id, language code, or all')
        p.add_argument('--work-dir')
        p.add_argument('--sources', dest='sources', default=argparse.SUPPRESS)
        if name == 'fetch':
            p.add_argument('--local-zip', action='append', metavar='ID=PATH',
                           help='copy this local zip into the cache instead of downloading')
        if name in ('build', 'compare'):
            p.add_argument('--ref-dir', help='directory with the 0220 bible_*.db files')
    args = ap.parse_args(argv)
    return {'fetch': cmd_fetch, 'build': cmd_build, 'compare': cmd_compare, 'report': cmd_report}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
