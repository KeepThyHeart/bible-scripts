"""Kokoro with access to its per-phoneme durations, pitch (F0) and energy, between predictor and decoder."""
import json, torch, numpy as np
from kokoro.model import KModel
from huggingface_hub import hf_hub_download
REPO='hexgrad/Kokoro-82M'
SR=24000
class KP:
    def __init__(self, voices=(('am_michael',.5),('bm_george',.5))):
        torch.set_num_threads(4)
        self.m=KModel(repo_id=REPO).eval()
        self.vocab=self.m.vocab
        self.pack=sum(w*torch.load(hf_hub_download(REPO,f'voices/{v}.pt'),weights_only=True) for v,w in voices)
    def ids(self, ps):
        return [0]+[self.vocab[c] for c in ps if c in self.vocab]+[0]
    @torch.no_grad()
    def predict(self, ps, speed=0.9):
        m=self.m; ids=self.ids(ps); ref=self.pack[len(ids)-3]
        x=torch.LongTensor([ids]); L=torch.LongTensor([len(ids)])
        mask=torch.zeros_like(x).bool()
        bert=m.bert(x,attention_mask=(~mask).int()); d_en=m.bert_encoder(bert).transpose(-1,-2)
        s=ref[:,128:]
        d=m.predictor.text_encoder(d_en,s,L,mask); h,_=m.predictor.lstm(d)
        dur=torch.sigmoid(m.predictor.duration_proj(h)).sum(-1)/speed
        t_en=m.text_encoder(x,L,mask)
        return dict(ids=ids,ref=ref,s=s,d=d,t_en=t_en,dur=dur.squeeze(0).numpy())
    @torch.no_grad()
    def f0n(self, st, frames):
        """F0/energy for integer frames per token (F0 is at twice the frame rate)."""
        n=len(st['ids']); fr=torch.LongTensor(frames)
        idx=torch.repeat_interleave(torch.arange(n),fr)
        aln=torch.zeros(n,len(idx)); aln[idx,torch.arange(len(idx))]=1; aln=aln.unsqueeze(0)
        en=st['d'].transpose(-1,-2)@aln
        F0,N=self.m.predictor.F0Ntrain(en,st['s'])
        return aln,F0.squeeze(0).numpy(),N.squeeze(0).numpy()
    @torch.no_grad()
    def decode(self, st, aln, F0, N):
        asr=st['t_en']@aln
        a=self.m.decoder(asr,torch.tensor(F0)[None].float(),torch.tensor(N)[None].float(),st['ref'][:,:128])
        return a.squeeze().numpy()
    def render(self, ps, speed=0.9):
        st=self.predict(ps,speed); fr=np.clip(np.round(st['dur']),1,None).astype(int)
        aln,F0,N=self.f0n(st,fr); return self.decode(st,aln,F0,N), st, fr, F0, N
