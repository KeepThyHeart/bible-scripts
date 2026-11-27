"""Publish rendered chapters in the bible repo's hosting layout (apps/web/docs/features/audio.md):
  audio/v1/KJV/index.json                               kth-audio-index/1
  audio/v1/KJV/{narrator}/{rev}/{book}/{ccc}.json|.ogg  kth-audio-chapter/1 + Ogg Opus
into the shared Tasks folder (on the owner's OneDrive), so the `audio` folder can be zipped and
dropped into the web app's data directory. Only chapters with a manifest are exported. Files are
moved from the old flat KJV-<voice>/ copy when they are there, otherwise copied in plain chunks
(vboxsf loops on mmap/copy_file_range writes). Re-run any time; it only touches what changed.
  export.py [VOICE ...]    default harrell60"""
import glob, json, os, re, sys
ROOT = '/media/sf_Tasks/output/AudioBibles'
HOST = ROOT + '/audio/v1/KJV'
V = os.path.expanduser('~/bible-audio/voices')
REV = 'r1'
LABELS = {'harrell60': 'Kokoro: Harrell blend (computer voice)', 'jim60': 'Kokoro: GentleJim blend (computer voice)'}

def put(src, dst, old=None):
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src): return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if old and os.path.exists(old) and os.path.getsize(old) == os.path.getsize(src):
        os.replace(old, dst); return 1
    tmp = dst + '.part'
    with open(src, 'rb') as a, open(tmp, 'wb') as b:
        while (c := a.read(256 * 1024)): b.write(c)
    if os.path.getsize(tmp) != os.path.getsize(src): raise SystemExit('size mismatch ' + dst)
    os.replace(tmp, dst); return 1

def write_json(obj, dst):
    s = json.dumps(obj, separators=(',', ':'))
    if os.path.exists(dst) and open(dst).read() == s: return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst + '.part', 'w').write(s); os.replace(dst + '.part', dst); return 1

counts = {}
for l in open(os.path.expanduser('~/bible-audio/prosody/work/kjv-phonemes.jsonl')):
    r = json.loads(l); counts.setdefault(r['book'], set()).add(r['chapter'])
narrators = []; moved = 0
for voice in sys.argv[1:] or ['harrell60']:
    src = f'{V}/kjv-{voice}'; have = {}
    for j in sorted(glob.glob(src + '/kjv-*.json')):
        b, c = int(os.path.basename(j)[4:6]), int(os.path.basename(j)[7:10])
        ogg = j[:-5] + '.ogg'
        if not os.path.exists(ogg) or os.path.exists(j[:-5] + '.f32'): continue
        m = json.load(open(j))
        m.update(module='KJV', narrator=voice, rev=REV)
        m['files'][0]['path'] = f'{b}/{c:03d}.ogg'
        d = f'{HOST}/{voice}/{REV}/{b}'
        moved += put(ogg, f'{d}/{c:03d}.ogg', f'{ROOT}/KJV-{voice}/{os.path.basename(ogg)}')
        moved += write_json(m, f'{d}/{c:03d}.json')
        have.setdefault(b, set()).add(c)
    full = sorted(b for b in have if have[b] >= counts[b])
    part = {str(b): sorted(have[b]) for b in sorted(have) if b not in full}
    narrators.append(dict(id=voice, label=LABELS.get(voice, voice), language='en', rev=REV, books=full,
                          **({'chapters': part} if part else {})))
    print(f'{voice}: {sum(len(v) for v in have.values())} chapters, {len(full)} complete books')
moved += write_json(dict(schema='kth-audio-index/1', module='KJV', narrators=narrators), HOST + '/index.json')
print(f'{moved} files written or moved')
