"""Kokoro-82M (PyTorch) split between its prosody predictor and its decoder."""
import re
import numpy as np

REPO = 'hexgrad/Kokoro-82M'
SAMPLE_RATE = 24000
MODEL_LABEL = 'Kokoro-82M v1.0 (PyTorch fp32)'


def parse_voice(spec):
    """"am_michael:0.5+bm_george:0.5" or "am_michael" -> [(id, weight)], weights summing to 1."""
    parts = []
    for p in spec.split('+'):
        m = re.fullmatch(r'([a-z]{2}_[a-z]+)(?::([0-9.]+))?', p.strip())
        if not m:
            raise ValueError(f'bad voice "{p}"')
        parts.append((m[1], float(m[2]) if m[2] else 1.0))
    total = sum(w for _, w in parts)
    return [(v, w / total) for v, w in parts]


class Kokoro:
    def __init__(self, threads=None):
        import torch
        from kokoro.model import KModel
        if threads:
            torch.set_num_threads(threads)
        self.torch = torch
        self.model = KModel(repo_id=REPO).eval()
        self.vocab = self.model.vocab
        self._packs = {}

    def pack(self, spec):
        if spec not in self._packs:
            from huggingface_hub import hf_hub_download
            t = self.torch
            self._packs[spec] = sum(w * t.load(hf_hub_download(REPO, f'voices/{v}.pt'), weights_only=True)
                                    for v, w in parse_voice(spec))
        return self._packs[spec]

    def predict(self, ps, voice, speed):
        """Everything up to the durations: returns state for frames() and decode()."""
        t = self.torch; m = self.model
        with t.no_grad():
            ids = [0] + [self.vocab[c] for c in ps if c in self.vocab] + [0]
            ref = self.pack(voice)[max(0, min(len(ids) - 3, 509))]
            x = t.LongTensor([ids]); L = t.LongTensor([len(ids)]); mask = t.zeros_like(x).bool()
            bert = m.bert(x, attention_mask=(~mask).int())
            d_en = m.bert_encoder(bert).transpose(-1, -2)
            s = ref[:, 128:]
            d = m.predictor.text_encoder(d_en, s, L, mask)
            h, _ = m.predictor.lstm(d)
            dur = t.sigmoid(m.predictor.duration_proj(h)).sum(-1) / speed
            t_en = m.text_encoder(x, L, mask)
        frames = np.clip(np.round(dur.squeeze(0).numpy()), 1, None).astype(int)
        return dict(n=len(ids), ref=ref, s=s, d=d, t_en=t_en), frames

    def pitch(self, st, frames):
        """Pitch (Hz) and energy for the given frames per token, at twice the frame rate."""
        t = self.torch
        with t.no_grad():
            n = st['n']; fr = t.LongTensor(frames)
            idx = t.repeat_interleave(t.arange(n), fr)
            aln = t.zeros(n, len(idx)); aln[idx, t.arange(len(idx))] = 1; aln = aln.unsqueeze(0)
            f0, en = self.model.predictor.F0Ntrain(st['d'].transpose(-1, -2) @ aln, st['s'])
        st['aln'] = aln
        return f0.squeeze(0).numpy(), en.squeeze(0).numpy()

    def decode(self, st, f0, energy):
        t = self.torch
        with t.no_grad():
            a = self.model.decoder(st['t_en'] @ st['aln'], t.tensor(f0)[None].float(),
                                   t.tensor(energy)[None].float(), st['ref'][:, :128])
        return a.squeeze().numpy().astype(np.float32)
