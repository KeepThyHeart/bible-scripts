"""JSON-lines worker for audio-build: one request per line on stdin, one reply per line on stdout.

  {"op":"hello"}
    -> {"ok":true,"engine":"kth-prosody","version":..,"model":..,"sampleRate":24000,
        "rules":{"version":1,"sha256":..}}
  {"op":"chapter","voice":"am_michael:0.5+bm_george:0.5","speed":0.9,"outDir":"/tmp/x",
   "pieces":[{"key":"3.0","text":"And thou shalt ...","phonemes":"ænd ðˈaʊ ...","detect":true}]}
    -> {"ok":true,"pieces":[{"key":"3.0","file":"/tmp/x/0000.f32","samples":156000,
        "edits":[["ðˈaʊ",33.0],...]}]}
  Audio is mono float32 little-endian at sampleRate, one raw file per piece.
  A failed request -> {"ok":false,"error":"..."}; the worker keeps serving.
"""
import argparse, json, os, sys, traceback
from . import __version__
from .rules import load_rules
from .chapter import synthesize_chapter


def serve(model_factory, rules, inp=sys.stdin, out=sys.stdout):
    model = None
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get('op') == 'hello':
                from .model import MODEL_LABEL, SAMPLE_RATE
                rep = dict(ok=True, engine='kth-prosody', version=__version__, model=MODEL_LABEL,
                           sampleRate=SAMPLE_RATE, rules=dict(version=rules['version'], sha256=rules['hash']))
            elif req.get('op') == 'chapter':
                model = model or model_factory()
                os.makedirs(req['outDir'], exist_ok=True); res = []
                for n, (p, audio, edits) in enumerate(synthesize_chapter(model, req['pieces'], req['voice'], float(req.get('speed', 1)), rules)):
                    f = os.path.join(req['outDir'], f'{n:04d}.f32')
                    audio.astype('<f4').tofile(f)
                    res.append(dict(key=p.get('key'), file=f, samples=int(len(audio)), edits=edits))
                rep = dict(ok=True, pieces=res)
            else:
                rep = dict(ok=False, error=f'unknown op {req.get("op")!r}')
        except Exception as e:  # report and keep serving
            traceback.print_exc(file=sys.stderr)
            rep = dict(ok=False, error=f'{type(e).__name__}: {e}')
        out.write(json.dumps(rep, ensure_ascii=False) + '\n'); out.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--rules', help='rules file (default: prosody/rules.json in this repo)')
    ap.add_argument('--threads', type=int, default=None)
    a = ap.parse_args(argv)
    import warnings; warnings.filterwarnings('ignore')
    rules = load_rules(a.rules)
    from .model import Kokoro
    serve(lambda: Kokoro(a.threads), rules)


if __name__ == '__main__':
    main()
