"""Differentiable Resemblyzer speaker encoder (GE2E, 3x LSTM 256) working on 24 kHz audio.
Same features as resemblyzer (40-band power mel, 25 ms window, 10 ms hop, 0-8 kHz), computed in
torch so the embedding can be back-propagated to the waveform. Weights: resemblyzer's pretrained.pt."""
import os
import numpy as np
import torch
import torch.nn as nn

WEIGHTS = os.path.expanduser('~/bible-audio/voices/src/resemblyzer/resemblyzer/pretrained.pt')
SR = 24000


def mel_filters(sr, n_fft, n_mels, fmax):
    """librosa.filters.mel (slaney scale, slaney norm), reimplemented."""
    def hz2mel(f):
        f = np.asarray(f, np.float64); m = f / (200.0 / 3)
        lin = f >= 1000.0
        return np.where(lin, 15.0 + np.log(np.maximum(f, 1e-9) / 1000.0) / (np.log(6.4) / 27.0), m)
    def mel2hz(m):
        m = np.asarray(m, np.float64); f = m * (200.0 / 3)
        lin = m >= 15.0
        return np.where(lin, 1000.0 * np.exp((np.log(6.4) / 27.0) * (m - 15.0)), f)
    fft_f = np.linspace(0, sr / 2, n_fft // 2 + 1)
    mel_f = mel2hz(np.linspace(hz2mel(0), hz2mel(fmax), n_mels + 2))
    fd = np.diff(mel_f); ramps = mel_f[:, None] - fft_f[None, :]
    W = np.zeros((n_mels, len(fft_f)))
    for i in range(n_mels):
        lo = -ramps[i] / fd[i]; hi = ramps[i + 2] / fd[i + 1]
        W[i] = np.maximum(0, np.minimum(lo, hi))
    W *= (2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels]))[:, None]
    return W.astype(np.float32)


class SpeakerEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.lstm = nn.LSTM(40, 256, 3, batch_first=True)
        self.linear = nn.Linear(256, 256)
        sd = torch.load(WEIGHTS, map_location='cpu', weights_only=True)
        sd = sd.get('model_state', sd)
        self.load_state_dict({k: v for k, v in sd.items() if not k.startswith('similarity')}, strict=True)
        self.eval()
        for p in self.parameters():
            p.requires_grad_(False)
        # 25 ms / 10 ms at 24 kHz; the 8 kHz band edge keeps the 16 kHz model's view
        self.n_fft, self.hop = 600, 240
        self.register_buffer('fb', torch.tensor(mel_filters(SR, 600, 40, 8000.0)))
        self.register_buffer('win', torch.hann_window(600))

    def mel(self, wav):
        # resemblyzer works on 16 kHz STFT power; 24 kHz with 1.5x window gives the same bins but
        # 1.5^2 more power per bin after the rfft; rescale so magnitudes match its training data
        X = torch.stft(wav, self.n_fft, self.hop, window=self.win, center=True, return_complex=True)
        P = (X.real ** 2 + X.imag ** 2) / 2.25
        return (self.fb @ P).T  # [frames, 40]

    @staticmethod
    def norm_volume(wav, target_dbfs=-30):
        rms = torch.sqrt(torch.mean(wav ** 2) + 1e-12)
        gain = 10 ** (target_dbfs / 20) / rms
        return wav * torch.clamp(gain, min=1.0).detach()  # increase only, like resemblyzer

    def forward(self, wav, partial=160, rate=1.3):
        """wav: 1-D float tensor at 24 kHz. Returns the L2-normed utterance embedding (256)."""
        m = self.mel(self.norm_volume(wav))
        n = m.shape[0]; step = max(1, int(round(100 / rate)))
        starts = list(range(0, max(1, n - partial + 1), step))
        if n - partial > starts[-1] + step * 0.25:
            starts.append(max(0, n - partial))
        parts = []
        for s in starts:
            p = m[s:s + partial]
            if p.shape[0] < partial:
                p = torch.nn.functional.pad(p, (0, 0, 0, partial - p.shape[0]))
            parts.append(p)
        x = torch.stack(parts)
        _, (h, _) = self.lstm(x)
        e = torch.relu(self.linear(h[-1]))
        e = e / (e.norm(dim=1, keepdim=True) + 1e-9)
        e = e.mean(0)
        return e / (e.norm() + 1e-9)
