"""Fit a Kokoro voice pack to a reader.

  prosody/venv/bin/python -I tools/fit.py PREPDIR INIT_SPEC OUTDIR [--hours 2.5] [--threads 6]

Pack = init blend + delta, delta[row] = shared 256-d offset + piecewise-linear per-row part
(knots over the token-count rows, so nearby rows move together and unseen long rows only get the
shared offset). Each step: run Kokoro's predictor on a training utterance (durations from the
init voice, scaled to the recording's length, fixed), decode a random ~2.4 s crop with gradient,
align the crop's mel to the recording by subsequence DTW, and minimise
  mel L1 (aligned, gain-normalised) + speaker-embedding (1 - cos to the reader centroid)
  + F0 median/spread match + energy-spread match + L2 on delta (in units of the stock voices' spread).
Checkpoints OUTDIR/ckpt.pt (and pack) every 25 steps; resumes from it."""
import argparse, json, math, os, random, shutil, subprocess, sys, time
import numpy as np
import torch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vfit, spk

KNOTS = [0, 30, 60, 90, 130, 180, 250, 509]
SPF = 600  # samples per decoder frame (40 Hz); F0/energy run at 80 Hz


def hat_basis():
    r = np.arange(510, dtype=np.float64); B = np.zeros((510, len(KNOTS)))
    for k, c in enumerate(KNOTS):
        lo = KNOTS[k - 1] if k > 0 else None; hi = KNOTS[k + 1] if k + 1 < len(KNOTS) else None
        if lo is not None:
            m = (r >= lo) & (r <= c); B[m, k] = (r[m] - lo) / (c - lo)
        if hi is not None:
            m = (r >= c) & (r <= hi); B[m, k] = np.maximum(B[m, k], (hi - r[m]) / (hi - c))
        B[r == c, k] = 1
    return torch.tensor(B, dtype=torch.float32)


class Delta(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.o = torch.nn.Parameter(torch.zeros(256))
        self.V = torch.nn.Parameter(torch.zeros(len(KNOTS), 256))
        self.register_buffer('B', hat_basis())

    def row(self, i):
        return self.o + self.B[i] @ self.V

    def full(self):
        return (self.o[None] + self.B @ self.V)[:, None, :]  # [510, 1, 256]


def dct_feats(lm, n=20):
    """Cepstra 1..n of a log-mel [80, T], mean-normalised over time (for alignment only)."""
    M = lm.shape[0]; k = np.arange(1, n + 1)[:, None]; m = np.arange(M)[None]
    D = np.cos(np.pi * k * (m + 0.5) / M)
    c = D @ lm
    c = c - c.mean(1, keepdims=True)
    return (c / (c.std(1, keepdims=True) + 1e-6)).T


def sub_dtw(A, B):
    """Subsequence DTW: all of A (gen) against a free-start/end part of B (target). Steps
    (1,1), (1,2), (2,1). Returns (ia, ib) or None."""
    T1, T2 = len(A), len(B); C = np.abs(A[:, None, :] - B[None, :, :]).mean(-1)
    INF = 1e18; D = np.full((T1, T2), INF); P = np.zeros((T1, T2), np.int8)
    D[0] = C[0]
    for i in range(1, T1):
        c1 = np.full(T2, INF); c2 = np.full(T2, INF); c3 = np.full(T2, INF)
        c1[1:] = D[i - 1, :-1]; c2[2:] = D[i - 1, :-2]
        if i >= 2: c3[1:] = D[i - 2, :-1]
        st = np.stack([c1, c2, c3]); k = st.argmin(0)
        D[i] = C[i] + st[k, np.arange(T2)]; P[i] = k
    j = int(D[-1].argmin())
    if D[-1, j] >= INF: return None
    ia, ib = [T1 - 1], [j]; i = T1 - 1
    while i > 0:
        k = P[i, j]
        if k == 0: i, j = i - 1, j - 1
        elif k == 1: i, j = i - 1, j - 2
        else: i, j = i - 2, j - 1
        if i < 0 or j < 0: return None
        ia.append(i); ib.append(j)
    return np.array(ia[::-1]), np.array(ib[::-1])


def plog(v, stage, pct, note):
    with open(os.path.expanduser('~/bible-audio/voices/progress.log'), 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M')} | {v} | {stage} | {pct}% | {note}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('prep'); ap.add_argument('init'); ap.add_argument('out')
    ap.add_argument('--hours', type=float, default=2.5); ap.add_argument('--max-steps', type=int, default=100000)
    ap.add_argument('--threads', type=int, default=6); ap.add_argument('--lr', type=float, default=0.001)
    ap.add_argument('--crop', type=float, default=2.4); ap.add_argument('--batch', type=int, default=2)
    ap.add_argument('--w-mel', type=float, default=1.0); ap.add_argument('--w-spk', type=float, default=1.0)
    ap.add_argument('--w-f0', type=float, default=4.0); ap.add_argument('--w-en', type=float, default=0.01)
    ap.add_argument('--w-reg', type=float, default=0.5); ap.add_argument('--w-reg-rows', type=float, default=2.0)
    ap.add_argument('--seed', type=int, default=1); ap.add_argument('--name', help='voice name for progress.log / interim render')
    ap.add_argument('--val-every', type=int, default=50); ap.add_argument('--patience', type=int, default=250); ap.add_argument('--val-n', type=int, default=10)
    ap.add_argument('--base-pack', help='start from this pack instead of the INIT spec (delta and L2 relative to it)')
    a = ap.parse_args()
    torch.set_num_threads(a.threads); random.seed(a.seed); torch.manual_seed(a.seed)
    os.makedirs(a.out, exist_ok=True); logf = open(os.path.join(a.out, 'fit-log.jsonl'), 'a')
    enc = spk.SpeakerEncoder(); S = vfit.Synth(a.threads)
    base = torch.load(a.base_pack, weights_only=True).float() if a.base_pack else vfit.blend(a.init)
    sigma = torch.stack([vfit.stock_pack(v).mean(0)[0] for v in vfit.STOCK_EN]).std(0) + 1e-3  # [256]
    train = vfit.load_utts(a.prep, 'train')
    cen_path = os.path.join(a.prep, 'reader-centroid.pt')
    cen = torch.load(cen_path) if os.path.exists(cen_path) else None
    # reader statistics: F0 (YIN) median / spread, energy spread over speech frames of ~crop length
    stats_path = os.path.join(a.prep, 'reader-stats.json')
    if os.path.exists(stats_path):
        st = json.load(open(stats_path))
    else:
        lfs, ens = [], []
        for u in random.sample(train, min(120, len(train))):
            w = vfit.read_wav(u['wav']); hz = vfit.yin_f0(w); lfs.append(np.log(hz[hz > 50]))
            L = int(a.crop * vfit.SR)
            for c in range(0, max(1, len(w) - L), L):
                ens.append(float(vfit.speech_db_spread(torch.tensor(w[c:c + L]))))
        lf = vfit.robust_logf0(np.concatenate(lfs))
        st = dict(f0_med=float(np.median(lf)), f0_spread=float(np.percentile(lf, 90) - np.percentile(lf, 10)),
                  en_spread=float(np.median(ens)), f0_med_hz=float(np.exp(np.median(lf))))
        if cen is None:
            E = torch.stack([enc(torch.tensor(vfit.read_wav(u['wav']))) for u in train]); cen = E.mean(0); cen = cen / cen.norm()
            torch.save(cen, cen_path)
        json.dump(st, open(stats_path, 'w'), indent=1)
    print('reader stats', st, flush=True)
    delta = Delta(); opt = torch.optim.Adam(delta.parameters(), lr=a.lr)
    ck = os.path.join(a.out, 'ckpt.pt'); step = 0; spent = 0.0
    if os.path.exists(ck):
        c = torch.load(ck, weights_only=False); delta.load_state_dict(c['delta']); opt.load_state_dict(c['opt'])
        step, spent = c['step'], c['spent']; print('resumed at step', step, flush=True)
    # held-out check: speaker similarity of full renders to the held-out recordings' centroid; keep the best
    val = [u for u in vfit.load_utts(a.prep, 'val') if 3 <= u['dur'] <= 8][:a.val_n]
    with torch.no_grad():
        Ev = torch.stack([enc(torch.tensor(vfit.read_wav(u['wav']))) for u in val]); vcen = Ev.mean(0); vcen = vcen / vcen.norm()
    vts = [S.text_side(u['phonemes']) for u in val]
    def val_check(pack):
        sims, vf = [], []
        with torch.no_grad():
            for t in vts:
                au = S.run(t, pack[S.row(t['n'])], 1.0)[0]
                sims.append(float(enc(au) @ vcen)); hz = vfit.yin_f0(au.numpy()); vf.append(float((hz > 50).mean()))
        return float(np.mean(sims)), float(np.mean(vf))
    best_path = os.path.join(a.out, 'best.json')
    best = json.load(open(best_path)) if os.path.exists(best_path) else None
    if best is None:
        vs0, vf0 = val_check(base)
        best = dict(step=0, val_sim=vs0, voiced=vf0, voiced0=vf0); json.dump(best, open(best_path, 'w'))
        torch.save(base.clone(), os.path.join(a.out, 'pack-best.pt'))
    print('val sim at start', best, flush=True)
    cache = {}; recent = []; last_log = [-1e9]; interim = [os.path.exists(os.path.join(a.out, 'pack-interim.pt'))]
    json.dump(vars(a), open(os.path.join(a.out, 'args.json'), 'w'), indent=1)
    t_start = time.time() - spent

    # durations only (predictor, no decoder): frames from the init voice scaled to the recording
    def frames_for(ts, ref0, target_frames):
        m = S.m; s = ref0[:, 128:]
        d = m.predictor.text_encoder(ts['d_en'], s, ts['L'], ts['mask']); h, _ = m.predictor.lstm(d)
        dd = torch.sigmoid(m.predictor.duration_proj(h)).sum(-1).squeeze(0).numpy()
        dd = dd * target_frames / dd.sum()
        return np.clip(np.round(dd), 1, None).astype(int)

    def get(u):
        if u['id'] not in cache:
            ts = S.text_side(u['phonemes']); w = vfit.read_wav(u['wav'])
            with torch.no_grad():
                fr = frames_for(ts, base[S.row(ts['n'])], len(w) / SPF)
            mp = u['wav'][:-4] + '.mask.npy'
            cache[u['id']] = (ts, w, fr, np.load(mp).astype(bool) if os.path.exists(mp) else None)
        return cache[u['id']]

    while step < a.max_steps and time.time() - t_start < a.hours * 3600:
        opt.zero_grad(); logs = []
        for _ in range(a.batch):
            u = random.choice(train); ts, w, frames, bm = get(u)
            row = S.row(ts['n']); ref = base[row] + delta.row(row)[None]
            m = S.m; s = ref[:, 128:]
            d = m.predictor.text_encoder(ts['d_en'], s, ts['L'], ts['mask'])
            n = ts['n']; fr = torch.LongTensor(frames); T = int(fr.sum())
            idx = torch.repeat_interleave(torch.arange(n), fr)
            aln = torch.zeros(n, T); aln[idx, torch.arange(T)] = 1; aln = aln.unsqueeze(0)
            f0, en = m.predictor.F0Ntrain(d.transpose(-1, -2) @ aln, s)
            # F0 stats over voiced predicted frames (whole utterance)
            lf = torch.log(f0[0][f0[0] > 60] + 1e-3)
            if len(lf) > 20:
                md = lf.detach().median(); lf = lf[(lf > md + math.log(0.6)) & (lf < md + math.log(1.8))]
            if len(lf) > 20:
                q = torch.quantile(lf, torch.tensor([0.1, 0.5, 0.9]))
                l_f0 = (q[1] - st['f0_med']) ** 2 + ((q[2] - q[0]) - st['f0_spread']) ** 2
            else:
                l_f0 = torch.zeros(())
            # random crop of decoder frames
            cf = min(T, int(a.crop * 40)); c0 = random.randint(0, T - cf)
            asr = (ts['t_en'] @ aln)[:, :, c0:c0 + cf]
            audio = m.decoder(asr, f0[:, 2 * c0:2 * (c0 + cf)], en[:, 2 * c0:2 * (c0 + cf)], ref[:, :128]).squeeze()
            pad = int(0.1 * vfit.SR); ga = audio[pad:-pad] if len(audio) > 4 * pad else audio
            g_lm = vfit.logmel(ga)
            # target window around the proportional position, +-0.8 s
            g0 = c0 * SPF + pad
            t0 = max(0, g0 - int(0.8 * vfit.SR)); t1 = min(len(w), g0 + len(ga) + int(0.8 * vfit.SR))
            t_lm = vfit.logmel(torch.tensor(w[t0:t1]))
            path = sub_dtw(dct_feats(g_lm.detach().numpy()), dct_feats(t_lm.numpy()))
            if path is None:
                continue
            ia, ib = path
            if bm is not None:  # drop target frames flagged as breath
                f10 = np.minimum(len(bm) - 1, (t0 + ib * 256) // 240)
                keep = ~bm[f10]
                if keep.sum() < 20: continue
                ia, ib = ia[keep], ib[keep]
            G = g_lm[:, ia]; Tm = t_lm[:, ib]
            l_mel = (G - G.mean() - (Tm - Tm.mean())).abs().mean()
            l_spk = 1 - enc(ga) @ cen
            l_en = (vfit.speech_db_spread(ga) - st['en_spread']) ** 2
            loss = a.w_mel * l_mel + a.w_spk * l_spk + a.w_f0 * l_f0 + a.w_en * l_en
            (loss / a.batch).backward()
            logs.append(dict(mel=float(l_mel), spk=float(l_spk), f0=float(l_f0), en=float(l_en)))
        reg = a.w_reg * ((delta.o / sigma) ** 2).mean() + a.w_reg_rows * ((delta.V / sigma) ** 2).mean()
        reg.backward(); opt.step(); step += 1
        if not logs:
            continue
        rec = {k: round(float(np.mean([l[k] for l in logs])), 4) for k in logs[0]}
        rec.update(step=step, reg=round(float(reg), 4), t=round(time.time() - t_start),
                   o_norm=round(float((delta.o / sigma).norm() / 16), 3), V_norm=round(float((delta.V / sigma).norm() / 16), 3))
        logf.write(json.dumps(rec) + '\n'); logf.flush()
        if step % 10 == 0:
            print(rec, flush=True)
        recent.append(rec)
        el = time.time() - t_start
        if a.name and (el - last_log[0] > 900 or step == 1):
            last_log[0] = el; R = recent[-40:]
            plog(a.name, 'fit', int(40 + 40 * min(1, el / (a.hours * 3600))),
                 f"step {step}, {el / 60:.0f}/{a.hours * 60:.0f} min, mel {np.mean([x['mel'] for x in R]):.3f}, "
                 f"speaker-sim(crop) {1 - np.mean([x['spk'] for x in R]):.3f}, f0 {np.mean([x['f0'] for x in R]):.4f}, delta {rec['o_norm']}/{rec['V_norm']} sigma")
        if a.name and not interim[0] and el > a.hours * 3600 / 2:
            interim[0] = True
            shutil.copy(os.path.join(a.out, 'pack-best.pt'), os.path.join(a.out, 'pack-interim.pt'))
            o = os.path.expanduser(f'~/bible-audio/voices/out/{a.name}-john3-16-interim.ogg')
            subprocess.run([sys.executable, '-I', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'render.py'), o,
                            '--pack', os.path.join(a.out, 'pack-interim.pt')], capture_output=True)
            if os.path.exists(o):
                plog(a.name, 'fit', int(60), f"SAMPLE {os.path.basename(o)} | John 3:16 from the best checkpoint so far (step {best['step']}, held-out sim {best['val_sim']:.3f}), raw, speed 0.9")
        if step % a.val_every == 0:
            with torch.no_grad():
                cur = (base + delta.full()).clone()
            vs, vf = val_check(cur); rec['val_sim'] = round(vs, 4)
            logf.write(json.dumps(dict(step=step, val_sim=round(vs, 4), voiced=round(vf, 3))) + '\n'); logf.flush()
            # a pack that fools the speaker encoder with unvoiced / noisy output is not better
            if vs > best['val_sim'] and vf >= 0.85 * best['voiced0']:
                best = dict(step=step, val_sim=vs, voiced=vf, voiced0=best['voiced0']); json.dump(best, open(best_path, 'w'))
                torch.save(cur, os.path.join(a.out, 'pack-best.pt'))
            if a.name:
                plog(a.name, 'fit', int(40 + 40 * min(1, (time.time() - t_start) / (a.hours * 3600))),
                     f"step {step}: held-out speaker-sim {vs:.3f}, voiced {vf:.2f} (init {best['voiced0']:.2f}); best {best['val_sim']:.3f} at step {best['step']}")
        if step - best['step'] >= a.patience and step % a.val_every == 0:
            print('early stop: no held-out gain for', a.patience, 'steps', flush=True)
            if a.name:
                plog(a.name, 'fit', 80, f"early stop at step {step}: best held-out sim {best['val_sim']:.3f} at step {best['step']}")
            break
        if step % 25 == 0:
            spent = time.time() - t_start
            torch.save(dict(delta=delta.state_dict(), opt=opt.state_dict(), step=step, spent=spent), ck + '.tmp')
            os.replace(ck + '.tmp', ck)
            with torch.no_grad():
                torch.save((base + delta.full()).clone(), os.path.join(a.out, 'pack.pt'))
                if step % 250 == 0:
                    torch.save((base + delta.full()).clone(), os.path.join(a.out, f'pack-step{step}.pt'))
    spent = time.time() - t_start
    torch.save(dict(delta=delta.state_dict(), opt=opt.state_dict(), step=step, spent=spent), ck)
    with torch.no_grad():
        torch.save((base + delta.full()).clone(), os.path.join(a.out, 'pack.pt'))
        torch.save(delta.full().clone(), os.path.join(a.out, 'delta.pt'))
    print('done', step, 'steps', round(spent / 60, 1), 'min', flush=True)


if __name__ == '__main__':
    main()
