"""Render KJV verses the way ~/bible-audio/prosody/src/pauses.py does (pipeline phonemes, light
"the", speed 0.9, sentence pieces, optional voice shaping) with a fitted pack or a stock voice.

  prosody/venv/bin/python -I tools/render.py OUT.ogg (--pack P.pt | --voice SPEC) [--verses 43.3.16]
      [--speed 0.9] [--pitch-st 0 --range 1 --local 1 --trend-ms 400 --en-range 1 --level 0] [--kbps 32]"""
import argparse, json, os, re, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vfit
from kth_prosody.rules import load_rules
from kth_prosody.chapter import synthesize_chapter

PHON = os.path.expanduser('~/bible-audio/prosody/work/kjv-phonemes.jsonl')


def level(a, LEVEL, sr=24000):  # as pauses.py
    hop = sr // 100; n = len(a) // hop
    if n < 10: return a
    env = np.sqrt(np.convolve(a[:n * hop] ** 2, np.ones(3 * hop) / (3 * hop), 'same')[::hop] + 1e-10)
    db = 20 * np.log10(env); sp = db > db.max() - 35
    if sp.sum() < 5: return a
    idx = np.arange(n); fill = np.interp(idx, idx[sp], db[sp])
    w = 51; k = np.hanning(w); k /= k.sum()
    trend = np.convolve(np.pad(fill, w // 2, mode='edge'), k, 'valid')
    gain_db = -LEVEL * np.clip(db - trend, 0, None) * sp
    g = np.convolve(np.pad(gain_db, 2, mode='edge'), np.ones(5) / 5, 'valid')
    g = np.interp(np.arange(len(a)) / hop, idx, g)
    return (a * 10 ** (g / 20)).astype(np.float32)


def install_shaping(model, PITCH_ST, RANGE, LOCAL, TREND_MS, EN_RANGE, LEVEL):  # as pauses.py
    _decode = model.decode
    def shaped(st, f0, en):
        f0 = np.array(f0, np.float64); en = np.array(en, np.float64); v = f0 > 50
        if v.any():
            lf = np.log(np.where(v, f0, 1.0)); idx = np.arange(len(f0))
            lf = np.interp(idx, idx[v], lf[v])
            if LOCAL != 1:
                w = max(3, int(TREND_MS / 12.5)) | 1; k = np.hanning(w); k /= k.sum()
                trend = np.convolve(np.pad(lf, w // 2, mode='edge'), k, 'valid')
                lf = trend + (lf - trend) * LOCAL
            med = np.median(lf[v]); lf = med + (lf - med) * RANGE + np.log(2) * PITCH_ST / 12
            f0[v] = np.exp(lf[v])
        if EN_RANGE != 1:
            sp = en > 0
            if sp.any(): m = np.median(en[sp]); en[sp] = m + (en[sp] - m) * EN_RANGE
        a = _decode(st, f0.astype(np.float32), en.astype(np.float32))
        return level(a, LEVEL) if LEVEL else a
    model.decode = shaped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('--pack'); ap.add_argument('--voice')
    ap.add_argument('--verses', default='43.3.16'); ap.add_argument('--speed', type=float, default=0.9)
    for k, d in [('pitch-st', 0), ('range', 1), ('local', 1), ('trend-ms', 400), ('en-range', 1), ('level', 0)]:
        ap.add_argument('--' + k, type=float, default=d)
    ap.add_argument('--kbps', type=int, default=32); ap.add_argument('--wav')
    ap.add_argument('--rms-db', type=float, default=-26.6, help='loudness match: scale the whole file to this RMS in dBFS (peak capped at 0.95); default = the stock render level; 0 disables')
    a = ap.parse_args()
    from kth_prosody.model import Kokoro
    model = Kokoro(6); rules = load_rules(None)
    if a.pack:
        import torch
        model._packs['fitted'] = torch.load(a.pack, weights_only=True).float(); voice = 'fitted'
    else:
        voice = a.voice
    if (a.pitch_st, a.range, a.local, a.en_range, a.level) != (0, 1, 1, 1, 0):
        install_shaping(model, a.pitch_st, a.range, a.local, a.trend_ms, a.en_range, a.level)
    want = [tuple(map(int, v.split('.'))) for v in a.verses.split(',')]
    pieces = []; END = re.compile(r'(?<=[.!?])\s+')
    for line in open(PHON):
        r = json.loads(line)
        if (r['book'], r['chapter'], r['verse']) in want:
            ph = re.sub(r'(?<![^ ,.;:!?])ðʌ(?![^ ,.;:!?])', 'ðə', r['phonemes'])
            ts, ps = END.split(r['text'].strip()), END.split(ph.strip())
            if len(ts) != len(ps): ts, ps = [r['text']], [ph]
            for i, (t, p) in enumerate(zip(ts, ps)):
                pieces.append(dict(verse=r['verse'], key=f"{r['verse']}.{r['piece']}.{i}", text=t, phonemes=p, detect=True))
    parts = []
    for p, audio, _ in synthesize_chapter(model, pieces, voice, a.speed, rules):
        if parts:
            parts.append(np.zeros(int(0.5 * vfit.SR), np.float32))
        nz = np.flatnonzero(np.abs(audio) > 2e-3)
        parts.append(audio[max(0, nz[0] - 720):nz[-1] + 720] if len(nz) else audio)
    out = np.concatenate([np.zeros(int(0.3 * vfit.SR), np.float32)] + parts + [np.zeros(int(0.4 * vfit.SR), np.float32)])
    if a.rms_db:
        g = 10 ** ((a.rms_db - 10 * np.log10(float((out ** 2).mean()) + 1e-12)) / 20)
        out = (out * min(g, 0.95 / max(1e-6, float(np.abs(out).max())))).astype(np.float32)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    vfit.to_opus(out, a.out, a.kbps)
    if a.wav:
        import soundfile as sf; sf.write(a.wav, out, vfit.SR)
    print(a.out, f'{len(out) / vfit.SR:.2f}s', ' '.join(p['text'] for p in pieces))


if __name__ == '__main__':
    main()
