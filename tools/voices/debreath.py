"""Find audible breaths in the pauses between words and replace them with silence.

  prosody/venv/bin/python -I tools/debreath.py OUTDIR --item WAV:TXJSON ... [--clips N]

A breath here: a run of 150-700 ms inside a pause between whisper-aligned words (with 40 ms kept
clear of each word edge, since whisper timestamps are rough), unvoiced (no YIN periodicity),
noisy (high spectral flatness / most energy above 1 kHz is not required, flatness is), and with a
level between the pause's noise floor + 8 dB and the speech level - 6 dB. Each run is widened to
where the level falls back to the floor, kept 30 ms clear of any voiced frame, and replaced by
silence with 15 ms fades, so pause timing is unchanged. Writes OUTDIR/<name>.wav, OUTDIR/debreath.json
(per-file counts and spans) and, with --clips, before/after clip pairs around some cuts plus a
text "plot" of the level for spot checks."""
import argparse, json, os, sys
import numpy as np
import soundfile as sf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SR = 24000; HOP = 240  # 10 ms frames


def frame_feats(a, chunk=20000):
    """Per 10 ms frame: level (dB), spectral flatness, periodicity. Chunked to bound memory."""
    n = len(a) // HOP; outs = []; a = np.pad(a, (240, 0))  # frame i centred on sample i*HOP
    for c in range(0, n, chunk):
        m = min(chunk, n - c)
        seg = a[c * HOP:(c + m) * HOP + 2 * 480]
        outs.append(_frame_feats(np.pad(seg, (0, max(0, m * HOP + 2 * 480 - len(seg)))), m))
    return tuple(np.concatenate([o[i] for o in outs]) for i in range(4))


def _frame_feats(a, n):
    W = 480
    x = a
    fr = (np.lib.stride_tricks.sliding_window_view(x, W)[::HOP][:n] * np.hanning(W)).astype(np.float32)
    P = np.abs(np.fft.rfft(fr, axis=1)) ** 2 + 1e-12
    db = 10 * np.log10(P.mean(1))
    f = np.fft.rfftfreq(W, 1 / SR); band = (f > 300) & (f < 8000)
    flat = np.exp(np.log(P[:, band]).mean(1)) / P[:, band].mean(1)  # spectral flatness
    # periodicity: autocorrelation peak at 60-400 Hz lags over a 50 ms window, normalised by the
    # window's own autocorrelation (a 20 ms window under-reads low male voices)
    L = 1200; xl = np.pad(a, (L // 2 - W // 2, L))
    fl = np.lib.stride_tricks.sliding_window_view(xl, L)[::HOP][:n] * np.hanning(L)
    ac = np.fft.irfft(np.abs(np.fft.rfft(fl, 2 * L, axis=1)) ** 2, axis=1)[:, :L]
    wh = np.hanning(L); wac = np.correlate(wh, wh, 'full')[L - 1:]
    lo, hi = SR // 400, SR // 60
    per = (ac[:, lo:hi] / wac[lo:hi]).max(1) / (ac[:, 0] / wac[0] + 1e-12)
    hf = P[:, f > 4000].sum(1) / P[:, f > 300].sum(1)  # share of energy above 4 kHz (/s/ is high)
    return db, flat, per, hf


def runs(mask):
    out = []; i = 0; n = len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]: j += 1
            out.append((i, j + 1)); i = j + 1
        else:
            i += 1
    return out


FRIC = ('s', 'sh', 'f', 'ph', 'th', 'ce', 'ci', 'cy', 'sc', 'x')
STOP = ('p', 't', 'k', 'c', 'ch', 'q', 'pt')
END_UNV = ('s', 'ss', 'se', 'ce', 'f', 'ff', 'fe', 'th', 'sh', 'ch', 'tch', 'gh', 'x', 'p', 't', 'k', 'ck', 'ct', 'pt', 'st', 'sk', 'ts', 'ks', 'ps', 'ed')


def onset_protect(word):
    """Seconds before the voicing onset that may belong to the word's initial unvoiced consonant."""
    w = ''.join(c for c in word.lower() if c.isalpha())
    if w.startswith(('sp', 'st', 'sk', 'sc', 'str', 'spr', 'scr', 'sch')): return 0.22  # s + stop
    if w.startswith(FRIC): return 0.17
    if w.startswith(('h', 'wh')): return 0.12
    if w.startswith(STOP): return 0.09
    return 0.025


def coda_protect(word):
    """Seconds after the last voiced frame that may belong to the word's final unvoiced consonant."""
    w = ''.join(c for c in word.lower() if c.isalpha())
    if w.endswith(('s', 'ss', 'se', 'ce', 'f', 'ff', 'fe', 'th', 'sh', 'ch', 'tch', 'x', 'st', 'sts', 'sk', 'ts', 'ks', 'ps')): return 0.2
    if w.endswith(END_UNV): return 0.12
    return 0.04


def process(wav, tx, min_dur=0.12, max_dur=0.8):
    a, sr = sf.read(wav, dtype='float32'); assert sr == SR
    words = [w for s in json.load(open(tx))['segments'] for w in s['words']]
    words = [w for w in words if any(c.isalnum() for c in w['w'])]
    ws = np.array([w['s'] for w in words]); we = np.array([w['e'] for w in words])
    db, flat, per, hf = frame_feats(a); n = len(db)
    floor = np.percentile(db, 5)
    voiced = (per > 0.5) & (db > floor + 12)
    vr = [r for r in runs(voiced) if r[1] - r[0] >= 3]  # voiced nuclei
    sp_level = np.median(db[np.concatenate([np.arange(r0, r1) for r0, r1 in vr])]) if vr else db.max() - 20
    vr = [r for r in vr if db[r[0]:r[1]].max() > sp_level - 10]  # weak periodic blips (in breaths) are not speech
    breathy = (db > floor + 6) & (db < sp_level - 10) & (~voiced) & (flat > 0.1) & (hf < 0.6)
    out = a.copy(); spans = []; mask = np.zeros(n, np.uint8)
    for (p0, p1), (q0, q1) in zip(vr[:-1], vr[1:]):
        t_end, t_on = p1 / 100, q0 / 100
        if t_on - t_end < 0.15: continue
        # a word boundary must fall in this pause (otherwise it is a gap inside a word)
        kb = np.flatnonzero((we > t_end - 0.25) & (we < t_on + 0.1) & (np.r_[ws[1:], 1e9] > t_end - 0.1) & (np.r_[ws[1:], 1e9] < t_on + 0.3))
        if not len(kb): continue
        k = int(kb[0]); prev_w, next_w = words[k]['w'], words[k + 1]['w'] if k + 1 < len(words) else ''
        g0 = p1 + int(coda_protect(prev_w) * 100)
        g1 = q0 - int(onset_protect(next_w) * 100)
        if (g1 - g0) / 100 < min_dur: continue
        # inhalation right before the phrase onset: loud-ish, aperiodic, mostly below 4 kHz (close-mic
        # rush of air); must start after a quiet dip so a word's tail (creak, fry) is never taken
        inh = (db[g0:g1] > floor + 6) & (db[g0:g1] < sp_level - 6) & (per[g0:g1] < 0.5) & (hf[g0:g1] < 0.35)
        rr = runs(inh)
        # merge runs separated by <= 3 frames
        mr = []
        for r in rr:
            if mr and r[0] - mr[-1][1] <= 3: mr[-1] = (mr[-1][0], r[1])
            else: mr.append(r)
        if mr and g1 - (g0 + mr[-1][1]) <= 6:
            r0, r1 = g0 + mr[-1][0], g1
            quiet_before = (db[p1:r0] < floor + 8).any()
            if quiet_before and 0.1 <= (r1 - r0) / 100 <= 0.8 and db[r0:r1].max() <= sp_level - 6:
                s0, s1 = r0 * HOP, r1 * HOP; fd = int(0.01 * SR)
                g = np.zeros(s1 - s0, np.float32); g[:fd] = np.linspace(1, 0, fd); g[-fd:] = np.linspace(0, 1, fd)
                out[s0:s1] *= g; mask[r0:r1] = 1
                spans.append(dict(t0=round(s0 / SR, 3), t1=round(s1 / SR, 3), pre_onset=True, kind='inhale',
                                  onset_gap_ms=int((q0 - r1) * 10), peak_db=round(float(db[r0:r1].max() - sp_level), 1),
                                  flat=round(float(flat[r0:r1].mean()), 2), hf=round(float(hf[r0:r1].mean()), 2),
                                  after=prev_w.strip(), before=next_w.strip()))
                g1 = r0  # the rest of the pause is handled below
        mask[g0:g1] |= breathy[g0:g1].astype(np.uint8)
        for r0, r1 in runs(breathy[g0:g1]):
            r0 += g0; r1 += g0
            ok = lambda f: floor + 3 < db[f] < sp_level - 12 and not voiced[f] and flat[f] > 0.05 and hf[f] < 0.5
            while r0 > g0 and ok(r0 - 1): r0 -= 1
            while r1 < g1 and ok(r1): r1 += 1
            if spans and r0 * HOP < spans[-1]['t1'] * SR: continue
            dur = (r1 - r0) / 100
            if not (min_dur <= dur <= max_dur): continue
            if db[r0:r1].max() > sp_level - 14 or flat[r0:r1].mean() < 0.1 or hf[r0:r1].mean() > 0.4: continue
            pre = (g1 - r1) <= 6
            s0, s1 = r0 * HOP, r1 * HOP; fd = int(0.012 * SR)
            g = np.zeros(s1 - s0, np.float32)
            g[:fd] = np.linspace(1, 0, fd); g[-fd:] = np.linspace(0, 1, fd)
            out[s0:s1] *= g; mask[r0:r1] = 1
            spans.append(dict(t0=round(s0 / SR, 3), t1=round(s1 / SR, 3), pre_onset=bool(pre), kind='breath',
                              onset_gap_ms=int((q0 - r1) * 10), peak_db=round(float(db[r0:r1].max() - sp_level), 1),
                              flat=round(float(flat[r0:r1].mean()), 2), hf=round(float(hf[r0:r1].mean()), 2),
                              after=prev_w.strip(), before=next_w.strip()))
    return a, out, spans, dict(speech_db=round(float(sp_level), 1), floor_db=round(float(floor), 1)), db, mask


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('out'); ap.add_argument('--item', action='append', required=True)
    ap.add_argument('--clips', type=int, default=0)
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); rep = {}
    for it in a.item:
        wav, tx = it.split(':'); name = os.path.splitext(os.path.basename(wav))[0]
        orig, clean, spans, lv, db, mask = process(wav, tx)
        sf.write(os.path.join(a.out, name + '.wav'), clean, SR)
        np.save(os.path.join(a.out, name + '.breathmask.npy'), mask)
        dur = len(orig) / SR
        rep[name] = dict(levels=lv, breaths=len(spans), pre_onset=sum(x['pre_onset'] for x in spans), masked_s=round(float(mask.sum()) / 100, 1), per_min=round(len(spans) / dur * 60, 1),
                         removed_s=round(sum(s['t1'] - s['t0'] for s in spans), 1), spans=spans)
        print(name, f'{len(spans)} breaths ({len(spans) / dur * 60:.1f}/min), {rep[name]["removed_s"]} s silenced', flush=True)
        if a.clips:
            cdir = os.path.join(a.out, 'clips'); os.makedirs(cdir, exist_ok=True)
            for s in spans[::max(1, len(spans) // a.clips)][:a.clips]:
                c0 = max(0, int((s['t0'] - 1.2) * SR)); c1 = min(len(orig), int((s['t1'] + 1.2) * SR))
                tag = f"{name}_{s['t0']:.2f}"
                sf.write(os.path.join(cdir, tag + '_before.wav'), orig[c0:c1], SR)
                sf.write(os.path.join(cdir, tag + '_after.wav'), clean[c0:c1], SR)
                # level trace (10 ms frames, dB re speech) with the cut marked, for a text spot check
                f0, f1 = c0 // HOP, c1 // HOP
                trace = ''.join('#' if s['t0'] * 100 <= f < s['t1'] * 100 else
                                ' .:-=+*%@'[int(np.clip((db[f] - lv['floor_db']) / max(1, lv['speech_db'] + 6 - lv['floor_db']) * 8, 0, 8))]
                                for f in range(f0, f1))
                s['trace'] = trace
        json.dump(rep, open(os.path.join(a.out, 'debreath.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
