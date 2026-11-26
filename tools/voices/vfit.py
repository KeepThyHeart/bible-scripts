"""Shared pieces for fitting Kokoro voice packs: model, differentiable synthesis, features, DTW."""
import json, os, sys, warnings
import numpy as np
import torch
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'prosody'))
from kth_prosody.model import Kokoro, parse_voice, REPO  # noqa: E402

SR = 24000
STOCK_EN = ('am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa '
            'bm_daniel bm_fable bm_george bm_lewis af_heart af_bella af_nicole af_sarah bf_emma bf_isabella').split()


def stock_pack(v):
    from huggingface_hub import hf_hub_download
    return torch.load(hf_hub_download(REPO, f'voices/{v}.pt'), weights_only=True).float()


def blend(spec):
    return sum(w * stock_pack(v) for v, w in parse_voice(spec))


def load_utts(prep_dir, split=None, max_dur=None):
    rows = [json.loads(l) for l in open(os.path.join(prep_dir, 'utts.jsonl'))]
    rows = [r for r in rows if (split is None or r['split'] == split) and (max_dur is None or r['dur'] <= max_dur)]
    for r in rows:
        r['wav'] = os.path.join(prep_dir, 'utts', r['id'] + '.wav')
    return rows


def read_wav(path):
    import soundfile as sf
    a, sr = sf.read(path, dtype='float32'); assert sr == SR
    return a


class Synth:
    """Kokoro split so the voice-pack row is a differentiable input."""
    def __init__(self, threads=6):
        self.k = Kokoro(threads); self.m = self.k.model; self.vocab = self.k.vocab
        for p in self.m.parameters():
            p.requires_grad_(False)

    def ids(self, ps):
        return [0] + [self.vocab[c] for c in ps if c in self.vocab] + [0]

    @staticmethod
    def row(n_ids):
        return max(0, min(n_ids - 3, 509))

    def text_side(self, ps):
        """Style-independent encoder outputs (cached per utterance)."""
        m = self.m; ids = self.ids(ps)
        with torch.no_grad():
            x = torch.LongTensor([ids]); L = torch.LongTensor([len(ids)]); mask = torch.zeros_like(x).bool()
            bert = m.bert(x, attention_mask=(~mask).int())
            d_en = m.bert_encoder(bert).transpose(-1, -2)
            t_en = m.text_encoder(x, L, mask)
        return dict(n=len(ids), L=L, mask=mask, d_en=d_en, t_en=t_en)

    def run(self, ts, ref, speed=1.0, target_frames=None, frames=None):
        """ref: [1, 256] (grad ok). Durations are rounded (no grad); with target_frames they are
        scaled to that total so the output lines up with a recording. Returns audio, f0, energy,
        frames, raw duration sum (at speed 1)."""
        m = self.m; s = ref[:, 128:]
        d = m.predictor.text_encoder(ts['d_en'], s, ts['L'], ts['mask'])
        h, _ = m.predictor.lstm(d)
        dur = torch.sigmoid(m.predictor.duration_proj(h)).sum(-1).squeeze(0)
        if frames is None:
            dd = dur.detach().numpy() / speed
            if target_frames is not None:
                dd = dd * target_frames / max(1e-6, dd.sum())
            frames = np.clip(np.round(dd), 1, None).astype(int)
        n = ts['n']; fr = torch.LongTensor(frames)
        idx = torch.repeat_interleave(torch.arange(n), fr)
        aln = torch.zeros(n, len(idx)); aln[idx, torch.arange(len(idx))] = 1; aln = aln.unsqueeze(0)
        f0, en = m.predictor.F0Ntrain(d.transpose(-1, -2) @ aln, s)
        audio = m.decoder(ts['t_en'] @ aln, f0, en, ref[:, :128]).squeeze()
        return audio, f0.squeeze(0), en.squeeze(0), frames, float(dur.detach().sum())


# ---------- features ----------
_mel_cache = {}


def mel_fb(n_fft=1024, n_mels=80, fmax=10500.0):
    key = (n_fft, n_mels, fmax)
    if key not in _mel_cache:
        sys.path.insert(0, os.path.dirname(__file__))
        from spk import mel_filters
        _mel_cache[key] = torch.tensor(mel_filters(SR, n_fft, n_mels, fmax))
    return _mel_cache[key]


def logmel(a, hop=256, n_fft=1024):
    """a: 1-D tensor. Log-mel (80 x T) up to 10.5 kHz (the LibriVox mp3s stop at 11 kHz)."""
    X = torch.stft(a, n_fft, hop, window=torch.hann_window(n_fft), center=True, return_complex=True)
    P = X.real ** 2 + X.imag ** 2
    M = mel_fb(n_fft) @ P
    return torch.log(torch.maximum(M, M.max().detach() * 1e-5) + 1e-9)  # 50 dB range: ignores codec noise floor


def frame_db(a, hop=240):
    n = len(a) // hop
    e = (a[:n * hop].reshape(n, hop) ** 2).mean(1)
    return 10 * torch.log10(e + 1e-10)


def speech_db_spread(a):
    """Std of frame level (dB) over speech frames (within 35 dB of the loudest)."""
    db = frame_db(a); sp = db > db.max() - 35
    return db[sp].std() if sp.sum() > 5 else db.std()


def yin_f0(a, sr=SR, hop=240, fmin=60, fmax=400, thr=0.1):
    """Plain YIN (numpy). Returns Hz per 10 ms frame, 0 for unvoiced."""
    a = np.asarray(a, np.float64); W = int(sr / fmin) + 2; tmax = int(sr / fmin); tmin = int(sr / fmax)
    out = []
    for c in range(0, len(a) - 2 * W, hop):
        x = a[c:c + 2 * W]
        if np.sqrt(np.mean(x[:W] ** 2)) < 1e-3:
            out.append(0.0); continue
        # difference function via FFT autocorrelation
        f = np.fft.rfft(x, 4 * W); ac = np.fft.irfft(f * np.conj(f))[:tmax + 1]
        cs = np.cumsum(x ** 2)
        e0 = cs[W - 1]
        cs0 = np.concatenate([[0.0], cs]); tt = np.arange(tmax + 1)
        et = cs0[tt + W] - cs0[tt]
        dfn = e0 + et - 2 * ac
        cm = dfn[1:] * np.arange(1, tmax + 1) / np.maximum(np.cumsum(dfn[1:]), 1e-12)
        cm = np.concatenate([[1.0], cm])
        cand = np.where(cm[tmin:tmax] < thr)[0]
        if not len(cand):
            out.append(0.0); continue
        t = cand[0] + tmin
        while t + 1 < tmax and cm[t + 1] < cm[t]:
            t += 1
        if 1 <= t < tmax:
            a_, b_, c_ = cm[t - 1], cm[t], cm[t + 1]; den = a_ - 2 * b_ + c_
            t = t + (0.5 * (a_ - c_) / den if abs(den) > 1e-12 else 0)
        out.append(sr / t)
    return np.array(out)


def robust_logf0(lf):
    """Log-F0 values within [0.6, 1.8] x their median (drops octave errors)."""
    med = np.median(lf)
    return lf[(lf > med + np.log(0.6)) & (lf < med + np.log(1.8))]


def f0_stats(hz):
    v = hz[hz > 50]
    if len(v) < 5:
        return None
    lf = robust_logf0(np.log(v))
    return float(np.median(lf)), float(np.percentile(lf, 90) - np.percentile(lf, 10))


def dtw_path(A, B):
    """A [T1, D], B [T2, D] (numpy). Symmetric DTW with steps (1,1), (1,2), (2,1) so slopes stay
    in [1/2, 2]; vectorised per row. Returns index arrays (ia, ib)."""
    T1, T2 = len(A), len(B)
    C = np.abs(A[:, None, :] - B[None, :, :]).mean(-1) if T1 * T2 * A.shape[1] < 6e7 else \
        np.stack([np.abs(A[i][None] - B).mean(-1) for i in range(T1)])
    INF = 1e18; D = np.full((T1, T2), INF); P = np.zeros((T1, T2), np.int8)
    D[0, 0] = C[0, 0]
    for i in range(1, T1):
        c1 = np.full(T2, INF); c2 = np.full(T2, INF); c3 = np.full(T2, INF)
        c1[1:] = D[i - 1, :-1]
        c2[2:] = D[i - 1, :-2]
        if i >= 2:
            c3[1:] = D[i - 2, :-1]
        st = np.stack([c1, c2, c3]); k = st.argmin(0)
        D[i] = C[i] + st[k, np.arange(T2)]; P[i] = k
    if D[-1, -1] >= INF:
        return None
    ia, ib = [T1 - 1], [T2 - 1]; i, j = T1 - 1, T2 - 1
    while i > 0 or j > 0:
        k = P[i, j]
        if k == 0: i, j = i - 1, j - 1
        elif k == 1: i, j = i - 1, j - 2
        else: i, j = i - 2, j - 1
        if i < 0 or j < 0:
            return None
        ia.append(i); ib.append(j)
    return np.array(ia[::-1]), np.array(ib[::-1])


def to_opus(audio, path, kbps=32):
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix='.f32', dir=os.path.dirname(path)) as f:
        np.asarray(audio, '<f4').tofile(f.name)
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'f32le', '-ar', str(SR), '-ac', '1', '-i', f.name,
                        '-c:a', 'libopus', '-b:a', f'{kbps}k', path], check=True)
