import re, numpy as np
def word_spans(ps, vocab):
    """Token ranges (indices into ids, BOS at 0) for each word of the phoneme string."""
    chars=[c for c in ps if c in vocab]; spans=[]; cur=None
    for i,c in enumerate(chars):
        t=i+1
        if re.match(r"[\wɐ-ʯʰ-˿æðøθᵻ]",c) and c not in ' ,.;:!?':
            if cur is None: cur=[t,t]
            cur[1]=t
        else:
            if cur: spans.append(tuple(cur)); cur=None
    if cur: spans.append(tuple(cur))
    return [(a,b,''.join(chars[a-1:b])) for a,b in spans]
def word_stats(spans, fr, F0, N):
    st=np.concatenate([[0],np.cumsum(fr)]); out=[]
    for a,b,w in spans:
        f0=F0[2*st[a]:2*st[b+1]]; v=f0[f0>60]
        out.append(dict(w=w,dur=int(st[b+1]-st[a]),f0max=float(v.max()) if len(v) else 0,f0mean=float(v.mean()) if len(v) else 0,
                        en=float(N[2*st[a]:2*st[b+1]].mean())))
    return out
