"""Find phrases repeated across nearby verses; soften them only where Kokoro over-emphasizes them."""
import re, numpy as np
from collections import Counter
from words import word_spans
WORD=re.compile(r"[A-Za-z']+")
MARK=re.compile(r"⟦([^|⟧]*)\|[^⟧]*⟧")
def words(t): return [x.lower() for x in WORD.findall(MARK.sub(r"\1",t))]
def repeated_phrases(texts, nmin=2, nmax=5, min_count=3):
    """Function-word n-grams repeated at least min_count times across the given verse texts
    (the formula, e.g. "and thou shalt"; content words are new information and are left alone)."""
    c=Counter()
    for t in texts:
        w=words(t); seen=set()
        for n in range(nmin,nmax+1):
            for i in range(len(w)-n+1):
                g=tuple(w[i:i+n])
                if g not in seen: seen.add(g); c[g]+=1
    keep=[g for g,k in c.items() if k>=min_count and all(x in FUNC for x in g) and not all(x in JOIN or x in ("the","a") for x in g)]
    # drop n-grams contained in a longer kept one with the same count
    keep=[g for g in keep if not any(len(h)>len(g) and c[h]==c[g] and any(h[i:i+len(g)]==g for i in range(len(h)-len(g)+1)) for h in keep)]
    return sorted(keep,key=len,reverse=True)
FUNC={'and','the','of','to','in','unto','a','an','that','for','with','upon','his','her','their','it','them','him','he','she','they','be','is','was','shall','thou','thee','thy','ye','shalt','wilt','which','by','from','i','me','my'}
def plan(text, ps, vocab, phrases):
    """Word indices (in the phoneme word list) that belong to a repeated phrase, and the index of the
    first content word after it (the reference the phrase must stay below)."""
    tw=words(text); sp=word_spans(ps,vocab); amap=align(tw,sp)
    if amap is None: return sp,[]   # could not match words to phonemes: leave the verse alone
    out=[]
    for g in phrases:
        n=len(g)
        for i in range(len(tw)-n+1):
            if tuple(tw[i:i+n])==g:
                # guard: every word must match its sounds (or continue a joined word), else leave it alone
                if not all(_ok(tw[j],sp[amap[j]][2]) or (j>0 and amap[j]==amap[j-1]) for j in range(i,i+n)): continue
                ref=next((amap[j] for j in range(i+n,len(tw)) if tw[j] not in FUNC),None)
                out.append((sorted({amap[j] for j in range(i,i+n)}),ref))
    return sp,out
def taper(n,edge=3):
    w=np.ones(n)
    for i in range(min(edge,n//2)): w[i]=w[-1-i]=(i+1)/(edge+1)
    return w

JOIN={'of','in','on','with','to','at','for','from','into','unto','by','upon'}
INIT={'a':'æɑɐʌəeɔaɚɪɛ','b':'b','c':'kstʃsʃ','d':'dɾʤ','e':'ɛiɪəeᵻɜʌjæ','f':'f','g':'ɡdʒʒ','h':'hwɑɛaɔoʌ','i':'ɪaɪəiᵻ','j':'dʒjh','k':'kn','l':'l','m':'m','n':'n','o':'ɔɑoʌəwɚaʊ',
      'p':'pfs','q':'k','r':'ɹ','s':'sʃzθ','t':'tθðʃɾ','u':'ʌjuʊəɜ','v':'v','w':'wɹhʊ','x':'zɛ','y':'jɪi','z':'z'}
def _ok(word, ph):
    c=ph.lstrip('ˈˌ')[:1]; return c and c in INIT.get(word[:1],'')
def align(tw, sp):
    """Text word index -> phoneme word index. espeak joins short pairs into one word ("of the" ʌvðə,
    "to be" təbi), so a phoneme word may cover a word plus a following function word. Each match must
    start with a plausible sound; returns None when no full match exists (the verse is left alone)."""
    import sys; from functools import lru_cache; sys.setrecursionlimit(10000)
    n,m=len(tw),len(sp)
    @lru_cache(None)
    def go(i,j):
        if i==n: return (0,()) if j==m else None
        if j==m: return None
        best=None
        r=go(i+1,j+1)
        if r: best=(r[0]+(0 if _ok(tw[i],sp[j][2]) else 1),(j,)+r[1])
        for k_ in (2,3):
            if i+k_<=n and all(tw[x] in FUNC|{'a','be','were','was','is','are','am','fro'} or len(tw[x])<=3 for x in range(i+1,i+k_)):
                r=go(i+k_,j+1)
                if r:
                    c=r[0]+0.5*(k_-1)+(0 if _ok(tw[i],sp[j][2]) else 1)
                    if best is None or c<best[0]: best=(c,(j,)*k_+r[1])
        return best
    r=go(0,0)
    return r[1] if r and r[0]<=0.5*abs(n-m)+2 else None
