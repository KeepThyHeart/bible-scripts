"""Pick the starting voice: score stock Kokoro voices (and small blends of the best) by speaker
similarity to the reader, and estimate the reader's pace against Kokoro's.

  prosody/venv/bin/python -I tools/init_voice.py PREPDIR OUT.json [--n 8] [--threads 6]"""
import argparse, itertools, json, sys, time, os
import numpy as np
import torch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vfit, spk


def trimmed_len(a, thr_db=35):
    db = vfit.frame_db(torch.tensor(a)).numpy(); sp = np.flatnonzero(db > db.max() - thr_db)
    return (sp[-1] - sp[0] + 1) * 240 / vfit.SR if len(sp) else len(a) / vfit.SR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('prep'); ap.add_argument('out')
    ap.add_argument('--n', type=int, default=8); ap.add_argument('--threads', type=int, default=6)
    ap.add_argument('--voices', default=' '.join(vfit.STOCK_EN))
    a = ap.parse_args()
    torch.set_num_threads(a.threads); t0 = time.time()
    enc = spk.SpeakerEncoder(); S = vfit.Synth(a.threads)
    train = vfit.load_utts(a.prep, 'train')
    E = torch.stack([enc(torch.tensor(vfit.read_wav(u['wav']))) for u in train])
    cen = E.mean(0); cen = cen / cen.norm()
    torch.save(cen, os.path.join(a.prep, 'reader-centroid.pt'))
    # a spread of short-to-medium utterances for scoring
    pool = sorted([u for u in train if 3 <= u['dur'] <= 7], key=lambda u: u['id'])
    pick = pool[::max(1, len(pool) // a.n)][:a.n]
    ts = [S.text_side(u['phonemes']) for u in pick]

    def score(pack):
        sims = []
        with torch.no_grad():
            for t in ts:
                au, *_ = S.run(t, pack[S.row(t['n'])], 1.0)
                sims.append(float(enc(au) @ cen))
        return float(np.mean(sims))
    packs = {v: vfit.stock_pack(v) for v in a.voices.split()}
    singles = {}
    for v, p in packs.items():
        singles[v] = score(p); print(f'{v:12s} {singles[v]:.3f}', flush=True)
    ranked = sorted(singles, key=singles.get, reverse=True)
    blends = {}
    for x, y in itertools.combinations(ranked[:4], 2):
        for w in (0.25, 0.5, 0.75):
            spec = f'{x}:{w}+{y}:{1 - w}'
            blends[spec] = score(w * packs[x] + (1 - w) * packs[y]); print(f'{spec:32s} {blends[spec]:.3f}', flush=True)
    x, y, z = ranked[:3]
    spec = f'{x}:0.4+{y}:0.3+{z}:0.3'
    blends[spec] = score(0.4 * packs[x] + 0.3 * packs[y] + 0.3 * packs[z]); print(spec, f'{blends[spec]:.3f}')
    best = max({**singles, **blends}.items(), key=lambda kv: kv[1])
    # pace: Kokoro's natural length at speed 1 (minus pad tokens) vs the reader's trimmed length
    init = vfit.blend(best[0]); kl, rl = [], []
    with torch.no_grad():
        for u in train:
            t = S.text_side(u['phonemes']); ref = init[S.row(t['n'])]; s = ref[:, 128:]
            d = S.m.predictor.text_encoder(t['d_en'], s, t['L'], t['mask'])
            h, _ = S.m.predictor.lstm(d)
            fr = np.clip(np.round(torch.sigmoid(S.m.predictor.duration_proj(h)).sum(-1).squeeze(0).numpy()), 1, None)
            kl.append(fr[1:-1].sum() * 600 / vfit.SR); rl.append(trimmed_len(vfit.read_wav(u['wav'])))
    kl, rl = np.array(kl), np.array(rl)
    pace = float(np.sum(kl) / np.sum(rl))
    res = dict(init=best[0], init_sim=round(best[1], 4), singles=singles, blends=blends,
               kokoro_speed1_s=round(float(kl.sum()), 1), reader_s=round(float(rl.sum()), 1),
               reader_pace_vs_kokoro=round(pace, 3),
               note='suggested speed = reader_pace_vs_kokoro (Kokoro speed at which its pace matches the reader; includes pauses inside utterances)',
               scored_on=[u['id'] for u in pick], cpu_s=round(time.time() - t0))
    json.dump(res, open(a.out, 'w'), indent=1)
    print('best', best, 'pace', pace)


if __name__ == '__main__':
    main()
