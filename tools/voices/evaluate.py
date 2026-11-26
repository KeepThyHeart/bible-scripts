"""Held-out validation: speaker similarity to the reader (held-out audio) for several packs, and
renders of a few held-out sentences for a whisper WER check (run tools/wer.py after).

  prosody/venv/bin/python -I tools/evaluate.py PREPDIR OUTDIR NAME=SPEC_OR_PACK.pt ... [--n-wer 6]

SPEC is a stock voice spec ("am_michael:0.5+bm_george:0.5"); a path ending in .pt is a pack."""
import argparse, json, os, sys, time
import numpy as np
import torch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vfit, spk


def load_pack(x):
    """SPEC | PACK.pt | back:ALPHA:PACK.pt:INIT_SPEC (init + ALPHA * (pack - init): a backed-off delta)."""
    if x.startswith('back:'):
        _, al, p, spec = x.split(':', 3); b = vfit.blend(spec)
        return b + float(al) * (torch.load(p, weights_only=True).float() - b)
    return torch.load(x, weights_only=True).float() if x.endswith('.pt') else vfit.blend(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('prep'); ap.add_argument('out'); ap.add_argument('packs', nargs='+')
    ap.add_argument('--n-wer', type=int, default=6); ap.add_argument('--threads', type=int, default=6)
    ap.add_argument('--max-utts', type=int, default=40)
    a = ap.parse_args()
    torch.set_num_threads(a.threads); t0 = time.time()
    enc = spk.SpeakerEncoder(); S = vfit.Synth(a.threads)
    val = vfit.load_utts(a.prep, 'val')
    val = val[::max(1, len(val) // a.max_utts)][:a.max_utts]
    with torch.no_grad():
        Ev = torch.stack([enc(torch.tensor(vfit.read_wav(u['wav']))) for u in val])
    cen = Ev.mean(0); cen = cen / cen.norm()
    reader_self = float(np.mean([(e @ ((Ev.sum(0) - e) / (Ev.sum(0) - e).norm())).item() for e in Ev]))
    os.makedirs(a.out, exist_ok=True)
    res = dict(n_val=len(val), reader_self_sim=round(reader_self, 4), packs={})
    wer_list = []
    for item in a.packs:
        name, src = item.split('=', 1); pack = load_pack(src)
        if name in res['packs']: continue
        sims_c, sims_u, f0s, kl, rl = [], [], [], [], []
        for i, u in enumerate(val):
            ts = S.text_side(u['phonemes'])
            with torch.no_grad():
                au, f0, en, fr, ds = S.run(ts, pack[S.row(ts['n'])], 1.0)
                e = enc(au)
            sims_c.append(float(e @ cen)); sims_u.append(float(e @ Ev[i]))
            hz = vfit.yin_f0(au.numpy()); st = vfit.f0_stats(hz)
            if st: f0s.append(st)
            if i < a.n_wer:
                p = os.path.join(a.out, f'{name}_{u["id"]}.wav')
                import soundfile as sf
                sf.write(p, au.numpy(), vfit.SR); wer_list.append(dict(pack=name, wav=p, text=u['text']))
        res['packs'][name] = dict(src=src, sim_to_val_centroid=round(float(np.mean(sims_c)), 4),
                                  sim_to_same_utt=round(float(np.mean(sims_u)), 4),
                                  f0_median_hz=round(float(np.exp(np.median([s[0] for s in f0s]))), 1),
                                  f0_spread_log=round(float(np.median([s[1] for s in f0s])), 3))
        print(name, res['packs'][name], flush=True)
    # reader's own F0 on the same utterances, same estimator
    rs = [vfit.f0_stats(vfit.yin_f0(vfit.read_wav(u['wav']))) for u in val]; rs = [s for s in rs if s]
    res['reader_f0_median_hz'] = round(float(np.exp(np.median([s[0] for s in rs]))), 1)
    res['reader_f0_spread_log'] = round(float(np.median([s[1] for s in rs])), 3)
    res['cpu_s'] = round(time.time() - t0)
    json.dump(res, open(os.path.join(a.out, 'eval.json'), 'w'), indent=1)
    json.dump(wer_list, open(os.path.join(a.out, 'wer-list.json'), 'w'), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != 'packs'}))


if __name__ == '__main__':
    main()
