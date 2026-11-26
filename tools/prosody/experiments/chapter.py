"""Render whole chapters with the softening rules (D3): verse clips joined by gaps, like audio-build."""
import re,sys,json,os,time,subprocess,numpy as np,soundfile as sf
sys.path.insert(0,os.path.dirname(__file__))
from kp import *; from words import *; from soften import *
LIGHT=lambda ps: re.sub(r'(?<![^ ,.;:!?])ðʌ(?![^ ,.;:!?])','ðə',ps)
RULES=dict(margin=20,dur=.6,energy=.3,lift=10)
def trim(a,thr=2e-3,pad=int(.03*SR)):
    nz=np.flatnonzero(np.abs(a)>thr)
    return a if not len(nz) else a[max(0,nz[0]-pad):nz[-1]+pad]
def soften_piece(k,text,ps,phrases,R=RULES,log=None):
    st=k.predict(ps); fr=np.clip(np.round(st['dur']),1,None).astype(int); aln,F0,N=k.f0n(st,fr)
    sp,pl=plan(text,ps,k.vocab,phrases)
    if not pl: return k.decode(st,aln,F0,N)
    ws=word_stats(sp,fr,F0,N); fr2=fr.copy(); edits=[]
    for idx,ref in pl:
        refpk=ws[ref]['f0max'] if ref is not None else np.median([w['f0max'] for w in ws])
        hit=False
        for i in idx:
            over=ws[i]['f0max']-(refpk-R['margin'])
            if over>0:
                a,b,_=sp[i]
                if ws[i]['dur']>6: fr2[a:b+1]=np.maximum(1,np.round(fr[a:b+1]*R['dur'])).astype(int)
                edits.append((i,over)); hit=True
        if hit and R['lift'] and ref is not None: edits.append((ref,-R['lift']))
    if not edits: return k.decode(st,aln,F0,N)
    aln2,F02,N2=k.f0n(st,fr2); cs=np.concatenate([[0],np.cumsum(fr2)])
    for i,over in edits:
        a,b,_=sp[i]; s,e=2*cs[a],2*cs[b+1]; s0,e0=max(0,s-6),min(len(F02),e+6)
        w=taper(e0-s0,6); F02[s0:e0]-=over*w*(F02[s0:e0]>60)
        if over>0: N2[s0:e0]-=R['energy']*w
    if log is not None: log.append([(sp[i][2],round(float(o))) for i,o in edits])
    return k.decode(st,aln2,F02,N2)
def gap_ms(book,chapter): return 900 if book==20 and 10<=chapter<=29 else 350
def render_chapter(k,rows,out_ogg):
    book,chapter=rows[0]['book'],rows[0]['chapter']
    body=[r for r in rows if r['verse']!=0]; phrases=repeated_phrases([r['text'] for r in body])
    parts=[]; log=[]; gap=np.zeros(int(gap_ms(book,chapter)/1000*SR))
    intro=[r for r in rows if r['verse']==0]
    if intro: parts+=[trim(k.render(LIGHT(intro[0]['phonemes']))[0]),np.zeros(int(.7*SR))]
    verses={}
    for r in body: verses.setdefault(r['verse'],[]).append(r)
    for v,ps_ in verses.items():
        clip=np.concatenate([trim(soften_piece(k,r['text'],LIGHT(r['phonemes']),phrases,log=log)) for r in ps_])
        parts+=[clip,gap]
    a=np.concatenate(parts[:-1]); wav=out_ogg[:-4]+'.wav'; sf.write(wav,a,SR)
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',wav,'-c:a','libopus','-b:a','24k',out_ogg],check=True); os.remove(wav)
    return len(a)/SR,phrases,log
if __name__=='__main__':
    src,outdir=sys.argv[1],sys.argv[2]; want=[tuple(map(int,x.split('.'))) for x in sys.argv[3:]]
    rows={}
    for l in open(src):
        r=json.loads(l); key=(r['book'],r['chapter'])
        if key in want: rows.setdefault(key,[]).append(r)
    k=KP(); os.makedirs(outdir,exist_ok=True)
    for key in want:
        t=time.time(); secs,ph,log=render_chapter(k,rows[key],f'{outdir}/{key[0]:02d}-{key[1]:03d}.ogg')
        print(f'{key}: {secs/60:.1f} min audio in {(time.time()-t)/60:.1f} min; phrases {[" ".join(p) for p in ph]}; softened {len(log)} pieces',flush=True)
