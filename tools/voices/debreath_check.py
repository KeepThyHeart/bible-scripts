"""Check breath cuts don't clip words: whisper a window around N cuts before and after, compare.
Also writes the owner's before/after pair: the ~12 s window with the most cuts (phrase-initial ones
count double).   wvenv/bin/python -I tools/debreath_check.py NAME WORKDIR [--n 40]"""
import argparse, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcribe import load16k
from wer import words, ed
import subprocess

V = os.path.expanduser('~/bible-audio/voices')


def opus(x, path, sr=16000):
    p = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'f32le', '-ar', str(sr), '-ac', '1', '-i', '-',
                        '-c:a', 'libopus', '-b:a', '32k', path], input=np.asarray(x, '<f4').tobytes(), check=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('name'); ap.add_argument('work'); ap.add_argument('--n', type=int, default=40)
    a = ap.parse_args(); W = a.work
    from faster_whisper import WhisperModel
    m = WhisperModel('base.en', device='cpu', compute_type='int8', cpu_threads=int(os.environ.get('WTHREADS', 1)))
    rep = json.load(open(os.path.join(W, 'clean', 'debreath.json')))
    res = dict(files={}); tot_e = tot_w = 0; changed = []
    best = None
    UNV = ('s', 'f', 'h', 'th', 'sh', 'p', 't', 'k', 'c', 'ch', 'wh', 'ph')
    def near(au, c0, lo, hi):
        seg, _ = m.transcribe(au, language='en', beam_size=5, condition_on_previous_text=False, word_timestamps=True)
        return [w.word for sg in seg for w in sg.words if lo <= c0 + w.start <= hi or lo <= c0 + w.end <= hi]
    null_e = cut_e = n_w = 0
    for name, x in rep.items():
        orig = load16k(os.path.join(W, 'wav', name + '.wav')); clean = load16k(os.path.join(W, 'clean', name + '.wav'))
        sp = x['spans']
        risky = [t for t in sp if t['pre_onset'] and t['before'].lower().lstrip('"(').startswith(UNV)]
        rest = [t for t in sp if t not in risky]
        pick = (risky[:a.n // 2] + rest[::max(1, len(rest) // a.n)])[:a.n // max(1, len(rep)) + 4]
        for t in pick:
            c0s = max(0.0, t['t0'] - 3.0); c1s = t['t1'] + 3.0
            sl = slice(int(c0s * 16000), int(c1s * 16000)); lo, hi = t['t0'] - 0.7, t['t1'] + 0.7
            ref = near(orig[sl], c0s, lo, hi)
            nul = near(orig[sl] * 0.99, c0s, lo, hi)   # same audio, 1% quieter: whisper's own noise
            cln = near(clean[sl], c0s, lo, hi)
            en, ec = ed(words(' '.join(ref)), words(' '.join(nul))), ed(words(' '.join(ref)), words(' '.join(cln)))
            null_e += en; cut_e += ec; n_w += max(1, len(words(' '.join(ref))))
            if ec > en:
                changed.append(dict(file=name, t0=t['t0'], kind=t.get('kind'), risky=t in risky, next_word=t['before'],
                                    orig=' '.join(ref).strip(), cleaned=' '.join(cln).strip()))
        res['files'][name] = dict(checked=len(pick), risky_checked=sum(t in risky for t in pick))
    res['local_word_error_null'] = round(null_e / max(1, n_w), 4)
    res['local_word_error_after_cut'] = round(cut_e / max(1, n_w), 4)
    best = None
    pr = os.path.join(W, 'prep', 'prep-report.json')
    body = {i['name']: (i['prelude_cut_s'], i['closing_cut_s']) for i in json.load(open(pr))['items']} if os.path.exists(pr) else {}
    for name, x in rep.items():
        sp = x['spans']; b0, b1 = body.get(name, (40, 1e9))
        # densest 12 s window for the listening pair (body text only, not the LibriVox prelude/closing)
        for s in sp:
            w0 = s['t0'] - 0.5; w1 = w0 + 12
            if w0 < b0 + 1 or w1 > b1 - 1: continue
            inwin = [t for t in sp if w0 <= t['t0'] and t['t1'] <= w1]
            score = len(inwin) + sum(t['pre_onset'] for t in inwin)
            if best is None or score > best[0]:
                best = (score, name, w0, w1, len(inwin), sum(t['pre_onset'] for t in inwin))
    res['changed'] = changed
    if best:
        _, name, w0, w1, k, kp = best
        orig = load16k(os.path.join(W, 'wav', name + '.wav')); clean = load16k(os.path.join(W, 'clean', name + '.wav'))
        # start/end the pair in a pause: nudge to the quietest 50 ms near each edge
        def quiet(t):
            i = int(t * 16000); seg = orig[max(0, i - 8000):i + 8000]
            e = np.convolve(seg ** 2, np.ones(800), 'valid'); return (max(0, i - 8000) + int(e.argmin()) + 400) / 16000
        w0, w1 = quiet(max(0, w0)), quiet(w1)
        sl = slice(int(w0 * 16000), int(w1 * 16000))
        opus(orig[sl], os.path.join(V, 'out', f'{a.name}-debreath-before.ogg'))
        opus(clean[sl], os.path.join(V, 'out', f'{a.name}-debreath-after.ogg'))
        res['pair'] = dict(file=name, t0=round(w0, 2), t1=round(w1, 2), cuts=k, phrase_initial=kp)
    json.dump(res, open(os.path.join(W, 'clean', 'check.json'), 'w'), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != 'changed'}))
    for c in changed: print(c)


if __name__ == '__main__':
    main()
