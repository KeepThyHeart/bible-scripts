"""Check kth-audio-chapter/1 manifests by the bible repo's rules (packages/core/src/audio/manifest.ts):
required fields, intro [s,e] with 0<=s<e, verses strictly ascending, 0<=start<end, no overlap with the
previous verse or the intro, nothing ending after duration (+0.5 s slack), and the ogg's size and sha256."""
import glob, hashlib, json, os, sys
out = os.path.expanduser(sys.argv[1]); bad = 0; n = 0
for f in sorted(glob.glob(out + '/kjv-*.json')):
    m = json.load(open(f)); n += 1; e = []
    for k in ('schema', 'module', 'book', 'chapter', 'narrator', 'rev', 'duration', 'files', 'verses'):
        if k not in m: e.append('missing ' + k)
    if m.get('schema') != 'kth-audio-chapter/1': e.append('schema')
    prev_end = 0; prev_v = -1
    if 'intro' in m:
        s, t = m['intro']
        if not 0 <= s < t: e.append('intro')
        prev_end = t
    for v, s, t in m['verses']:
        if v <= prev_v: e.append(f'verse {v} not ascending')
        if not 0 <= s < t: e.append(f'verse {v} start/end')
        if s + 1e-6 < prev_end: e.append(f'verse {v} overlaps')
        if t > m['duration'] + 0.5: e.append(f'verse {v} after duration')
        prev_v, prev_end = v, t
    ogg = f[:-5] + '.ogg'; data = open(ogg, 'rb').read(); fi = m['files'][0]
    if fi['bytes'] != len(data) or fi['sha256'] != hashlib.sha256(data).hexdigest(): e.append('ogg size/hash')
    if e: bad += 1; print(os.path.basename(f), '; '.join(e[:4]))
print(f'{n} manifests, {bad} with problems')
