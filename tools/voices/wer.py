"""WER of rendered clips: whisper them (whisper venv) and score against the reference texts.

  wvenv/bin/python -I tools/wer.py LIST.json OUT.json [--model small.en]
LIST.json: [{pack, wav, text}]. Prints per-pack WER."""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcribe import load16k

NUM = {'1': 'one', '2': 'two', '3': 'three', '4': 'four', '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine', '10': 'ten'}


def words(t):
    t = t.lower().replace('’', "'").replace('-', ' ')
    w = [re.sub(r"[^a-z0-9']", '', x).strip("'") for x in t.split()]
    return [NUM.get(x, x) for x in w if x]


def ed(a, b):
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        p, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (a[i - 1] != b[j - 1]))
    return d[-1]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('list'); ap.add_argument('out')
    ap.add_argument('--model', default='small.en'); a = ap.parse_args()
    from faster_whisper import WhisperModel
    m = WhisperModel(a.model, device='cpu', compute_type='int8', cpu_threads=int(os.environ.get('WTHREADS', 6)))
    items = json.load(open(a.list)); per = {}
    for it in items:
        segs, _ = m.transcribe(load16k(it['wav']), language='en', beam_size=5)
        hyp = ' '.join(s.text.strip() for s in segs)
        r, h = words(it['text']), words(hyp); e = ed(r, h)
        it.update(hyp=hyp, errors=e, ref_words=len(r))
        p = per.setdefault(it['pack'], [0, 0]); p[0] += e; p[1] += len(r)
    res = dict(wer={k: round(v[0] / max(1, v[1]), 4) for k, v in per.items()}, items=items)
    json.dump(res, open(a.out, 'w'), indent=1)
    print(json.dumps(res['wer']))
    for it in items:
        if it['errors']:
            print(it['pack'], it['errors'], '|', it['text'], '|', it['hyp'])


if __name__ == '__main__':
    main()
