"""Collect a reader's results: copy the pack to VOICES/NAME.pt, cut the reader sample, write
report.json, and list the outputs for the whisper check.

  prosody/venv/bin/python -I tools/finalize.py NAME WORKDIR [--sample-s 20]"""
import argparse, json, os, shutil, sys
import numpy as np
import soundfile as sf
import torch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vfit

VOICES = os.path.expanduser('~/bible-audio/voices')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('name'); ap.add_argument('work')
    ap.add_argument('--sample-s', type=float, default=20); ap.add_argument('--pack', help='pack to publish (default fit/pack.pt)')
    ap.add_argument('--sample-only', action='store_true')
    a = ap.parse_args(); W = a.work; out = os.path.join(VOICES, 'out')
    pack_src = a.pack or os.path.join(W, 'fit', 'pack.pt'); dst = os.path.join(VOICES, a.name + '.pt')
    if not a.sample_only:
        pack = torch.load(pack_src, weights_only=True)
        assert tuple(pack.shape) == (510, 1, 256), pack.shape
        torch.save(pack.clone(), dst)
    # reader sample: ~20 s of body speech from the longest source, cut at pauses
    prep = json.load(open(os.path.join(W, 'prep', 'prep-report.json')))
    it = max(prep['items'], key=lambda i: i['duration'])
    name = it['name']
    clean = os.path.join(W, 'clean', name + '.wav')
    wav = clean if os.path.exists(clean) else os.path.join(W, 'wav', name + '.wav')
    au, _ = sf.read(wav, dtype='float32')
    tx = json.load(open(os.path.join(W, 'tx', name + '.json')))
    words = [w for s in tx['segments'] for w in s['words'] if w['s'] > it['prelude_cut_s'] + 30 and w['e'] < it['closing_cut_s'] - 5]
    gaps = [(words[k]['e'], words[k + 1]['s']) for k in range(len(words) - 1) if words[k + 1]['s'] - words[k]['e'] > 0.3]
    s0 = gaps[0]; s1 = min(gaps, key=lambda g: abs(g[0] - s0[1] - a.sample_s))
    t0, t1 = (s0[0] + s0[1]) / 2, (s1[0] + s1[1]) / 2
    clip = au[int(t0 * vfit.SR):int(t1 * vfit.SR)]
    fd = int(0.02 * vfit.SR); clip[:fd] *= np.linspace(0, 1, fd); clip[-fd:] *= np.linspace(1, 0, fd)
    sample = os.path.join(out, f'{a.name}-reader-sample.ogg'); vfit.to_opus(clip, sample)
    sample_text = ' '.join(w['w'].strip() for w in words if t0 <= w['s'] < t1)
    if a.sample_only:
        print(sample, round(t1 - t0, 1), 's from', name, ':', sample_text); return
    rep = dict(reader=a.name, pack=dst, pack_src=pack_src)
    for k, f in [('prep', 'prep/prep-report.json'), ('init', 'init.json'), ('eval', 'eval/eval.json'), ('wer', 'eval/wer.json'),
                 ('debreath', 'clean/debreath.json'), ('sources', 'sources.json'), ('fit_args', 'fit/args.json')]:
        p = os.path.join(W, f)
        if os.path.exists(p):
            d = json.load(open(p))
            if k == 'prep':
                d = {kk: v for kk, v in d.items() if kk != 'items'} | dict(items=[{kk: v for kk, v in i.items() if kk != 'examples'} for i in d['items']])
            if k == 'wer':
                d = d['wer']
            if k == 'debreath':
                d = {n: {kk: v for kk, v in x.items() if kk != 'spans'} for n, x in d.items()}
            rep[k] = d
    log = os.path.join(W, 'fit', 'fit-log.jsonl')
    if os.path.exists(log):
        L = [x for x in (json.loads(l) for l in open(log)) if 't' in x]
        rep['fit'] = dict(steps=L[-1]['step'], seconds=L[-1]['t'],
                          first50={k: round(float(np.mean([x[k] for x in L[:50]])), 4) for k in ('mel', 'spk', 'f0', 'en')},
                          last50={k: round(float(np.mean([x[k] for x in L[-50:]])), 4) for k in ('mel', 'spk', 'f0', 'en')},
                          delta_rms_sigma=dict(shared=L[-1]['o_norm'], rows=L[-1]['V_norm']))
    rep['reader_sample'] = dict(file=sample, source=name, t0=round(t0, 2), t1=round(t1, 2), whisper_text=sample_text)
    json.dump(rep, open(os.path.join(VOICES, f'{a.name}-report.json'), 'w'), indent=1)
    print(json.dumps(rep['reader_sample']))


if __name__ == '__main__':
    main()
