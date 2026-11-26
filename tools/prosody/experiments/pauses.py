"""Prototype: chapters with pauses from punctuation and paragraphs.
python pauses.py OUTDIR PREFIX sentence_ms paragraph_ms clause_ms comma_ms BOOK.CHAPTER..."""
import json, os, re, sqlite3, subprocess, sys, time, warnings
import numpy as np
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'prosody'))
from kth_prosody.rules import load_rules
from kth_prosody.model import Kokoro, SAMPLE_RATE
from kth_prosody.chapter import synthesize_chapter

out, name = sys.argv[1], sys.argv[2]
S, P, C, K = map(int, sys.argv[3:7])
SUPP = json.load(open(os.path.expanduser('~/bible-audio/paragraphs/kjv-supplement.json')))['breaks'] if os.environ.get('SUPP') else {}
INTRO, END_MS = int(os.environ.get('INTRO_MS', 700)), int(os.environ.get('END_MS', 0))
model = Kokoro(int(os.environ.get('THREADS', 4))); rules = load_rules(None)
# Voice shaping before the decoder: PITCH_ST lowers or raises the whole pitch curve (semitones);
# RANGE narrows pitch movement around the piece's median (log scale); EN_RANGE does the same for energy.
LOCAL, TREND_MS = float(os.environ.get('LOCAL', 1)), float(os.environ.get('TREND_MS', 400))
PITCH_ST, RANGE, EN_RANGE = float(os.environ.get('PITCH_ST', 0)), float(os.environ.get('RANGE', 1)), float(os.environ.get('EN_RANGE', 1))
_decode = model.decode
def shaped(st, f0, en):
    f0 = np.array(f0, np.float64); en = np.array(en, np.float64); v = f0 > 50
    if v.any():
        lf = np.log(np.where(v, f0, 1.0)); idx = np.arange(len(f0))
        lf = np.interp(idx, idx[v], lf[v])                     # bridge unvoiced gaps
        if LOCAL != 1:                                         # soften jumps faster than the phrase melody
            w = max(3, int(TREND_MS / 12.5)) | 1; k = np.hanning(w); k /= k.sum()
            trend = np.convolve(np.pad(lf, w // 2, mode='edge'), k, 'valid')
            lf = trend + (lf - trend) * LOCAL
        med = np.median(lf[v]); lf = med + (lf - med) * RANGE + np.log(2) * PITCH_ST / 12
        f0[v] = np.exp(lf[v])
    if EN_RANGE != 1:                                          # speech frames only; silences stay silent
        sp = en > 0
        if sp.any(): m = np.median(en[sp]); en[sp] = m + (en[sp] - m) * EN_RANGE
    a = _decode(st, f0.astype(np.float32), en.astype(np.float32))
    return level(a) if LEVEL else a

LEVEL = float(os.environ.get('LEVEL', 0))
def level(a, sr=24000):
    # Turn down syllables that are louder than the phrase around them: envelope over 30 ms,
    # phrase trend over 500 ms (speech only), downward gain by LEVEL of the excess in dB, smoothed.
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
model.decode = shaped
def render(book, chap):
    db = sqlite3.connect(os.path.expanduser('~/bible-audio/online/bible_kjv.db'))
    para = {vid % 1000 for vid, f in db.execute('select verse_id, formatting from bible_verse where verse_id/1000=?', (book * 1000 + chap,))
            if f and json.loads(f).get('block', {}).get('paragraph_start')}
    para |= set(SUPP.get(str(book), {}).get(str(chap), []))
    rows = [json.loads(l) for l in open(os.path.expanduser('~/bible-audio/prosody/work/kjv-phonemes.jsonl'))
            if l.startswith(f'{{"book":{book},"chapter":{chap},')]
    END = re.compile(r'(?<=[.!?])\s+')
    pieces = []
    for r in rows:
        ph = re.sub(r'(?<![^ ,.;:!?])ðʌ(?![^ ,.;:!?])', 'ðə', r['phonemes'])
        ts, ps = END.split(r['text'].strip()), END.split(ph.strip())
        if len(ts) != len(ps): ts, ps = [r['text']], [ph]
        for i, (t, p) in enumerate(zip(ts, ps)):
            pieces.append(dict(verse=r['verse'], key=f"{r['verse']}.{r['piece']}.{i}", text=t, phonemes=p, detect=r['verse'] != 0))
    parts = []; prev = None; t0 = time.time()
    def gap_after(text, next_verse):
        if next_verse in para: return P
        e = text.rstrip(' )')[-1:]
        return S if e in '.?!' else C if e in ';:' else K
    for p, audio, _ in synthesize_chapter(model, pieces, 'am_michael:0.5+bm_george:0.5', 0.9, rules):
        if prev is not None:
            ms = INTRO if prev['verse'] == 0 else gap_after(prev['text'], p['verse']) if p['verse'] != prev['verse'] else S
            parts.append(np.zeros(int(ms * SAMPLE_RATE / 1000), np.float32))
        nz = np.flatnonzero(np.abs(audio) > 2e-3)
        parts.append(audio[max(0, nz[0] - 720):nz[-1] + 720] if len(nz) else audio); prev = p
    parts.append(np.zeros(int(END_MS * SAMPLE_RATE / 1000), np.float32))
    os.makedirs(out, exist_ok=True); f = os.path.join(out, f'{name}-{book:02d}-{chap:03d}.f32')
    np.concatenate(parts).astype('<f4').tofile(f)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'f32le', '-ar', '24000', '-ac', '1', '-i', f, '-c:a', 'libopus', '-b:a', '24k', f[:-4] + '.ogg'], check=True)
    print(book, chap, f'{os.path.getsize(f)/4/24000/60:.2f} min audio, {(time.time()-t0)/60:.1f} min, paragraphs at {sorted(para)}'); os.remove(f)

for c in sys.argv[7:]:
    render(*map(int, c.split('.')))
