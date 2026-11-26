"""Listen-check by proxy: each output is non-silent, of plausible length, and whisper hears the verse.
Adds 'output_checks' to VOICES/NAME-report.json.   wvenv/bin/python -I tools/check_outputs.py NAME"""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcribe import load16k
from wer import words, ed

V = os.path.expanduser('~/bible-audio/voices')
VERSE = 'For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish, but have everlasting life.'


def main():
    name = sys.argv[1]
    from faster_whisper import WhisperModel
    m = WhisperModel('small.en', device='cpu', compute_type='int8', cpu_threads=int(os.environ.get('WTHREADS', 6)))
    files = [f'{name}-john3-16.ogg', f'{name}-john3-16-shaped.ogg', 'stock-john3-16.ogg', f'{name}-reader-sample.ogg']
    files += [f for f in sorted(os.listdir(os.path.join(V, 'out'))) if f.startswith(name + '-debreath-')]
    res = {}
    for f in files:
        p = os.path.join(V, 'out', f)
        if not os.path.exists(p): continue
        a = load16k(p); dur = len(a) / 16000
        fr = a[:len(a) // 160 * 160].reshape(-1, 160); db = 10 * np.log10((fr ** 2).mean(1) + 1e-12)
        segs, _ = m.transcribe(a, language='en', beam_size=5)
        hyp = ' '.join(s.text.strip() for s in segs)
        r = dict(seconds=round(dur, 2), kbytes=round(os.path.getsize(p) / 1024, 1), peak=round(float(np.abs(a).max()), 3),
                 rms_db=round(float(10 * np.log10((a ** 2).mean() + 1e-12)), 1),
                 speech_frac=round(float((db > db.max() - 35).mean()), 2), whisper=hyp)
        if 'john3-16' in f:
            rw, hw = words(VERSE), words(hyp); r['wer'] = round(ed(rw, hw) / len(rw), 3)
        res[f] = r; print(f, json.dumps(r), flush=True)
    rp = os.path.join(V, f'{name}-report.json')
    rep = json.load(open(rp)) if os.path.exists(rp) else {}
    rep['output_checks'] = res; json.dump(rep, open(rp, 'w'), indent=1)


if __name__ == '__main__':
    main()
