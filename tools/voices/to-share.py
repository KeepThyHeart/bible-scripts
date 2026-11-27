"""Copy finished chapter oggs (and the voice packs) into the shared Tasks folder's output/AudioBibles,
with plain chunked read/write (no mmap, no copy_file_range: vboxsf loops on those), a temp name
then rename, and a size check. Skips files already there with the same size.
  to-share.py [VOICE ...]     default: harrell60"""
import glob, os, sys, time
DST = '/media/sf_Tasks/output/AudioBibles'
V = os.path.expanduser('~/bible-audio/voices')
def put(src, dst):
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src): return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '.part'
    with open(src, 'rb') as a, open(tmp, 'wb') as b:
        while True:
            c = a.read(256 * 1024)
            if not c: break
            b.write(c)
    if os.path.getsize(tmp) != os.path.getsize(src): raise SystemExit(f'size mismatch {dst}')
    os.replace(tmp, dst); return 1
n = 0; t = time.time()
for voice in sys.argv[1:] or ['harrell60']:
    for f in sorted(glob.glob(f'{V}/kjv-{voice}/kjv-*.ogg') + glob.glob(f'{V}/kjv-{voice}/kjv-*.json')):
        n += put(f, f'{DST}/KJV-{voice}/{os.path.basename(f)}')
for f in sorted(glob.glob(f'{V}/blends/*.pt') + glob.glob(f'{V}/*.pt')):
    n += put(f, f'{DST}/voice-packs/{os.path.basename(f)}')
print(f'{n} files copied in {time.time() - t:.0f}s')
