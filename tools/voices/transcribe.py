"""Transcribe audio files with faster-whisper (word timestamps), one JSON per file.
Run with the whisper venv:  wvenv/bin/python -I tools/transcribe.py OUTDIR WAV... [--model small.en]
Also used to re-transcribe renders for WER:  --plain prints just the text per file."""
import argparse, json, os, subprocess, sys
import numpy as np


def load16k(path):
    raw = subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', path, '-f', 'f32le', '-ac', '1', '-ar', '16000', '-'],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('wavs', nargs='+')
    ap.add_argument('--model', default='small.en'); ap.add_argument('--threads', type=int, default=6)
    ap.add_argument('--plain', action='store_true')
    a = ap.parse_args()
    from faster_whisper import WhisperModel
    m = WhisperModel(a.model, device='cpu', compute_type='int8', cpu_threads=a.threads)
    os.makedirs(a.out, exist_ok=True)
    for w in a.wavs:
        name = os.path.splitext(os.path.basename(w))[0]
        dst = os.path.join(a.out, name + '.json')
        if os.path.exists(dst) and not a.plain:
            print('skip', dst); continue
        segs, info = m.transcribe(load16k(w), language='en', word_timestamps=not a.plain, beam_size=5,
                                  vad_filter=False, condition_on_previous_text=False)
        res = []
        for s in segs:
            res.append(dict(start=s.start, end=s.end, text=s.text.strip(),
                            words=[dict(w=x.word, s=x.start, e=x.end, p=x.probability) for x in (s.words or [])]))
        if a.plain:
            print(json.dumps(dict(file=w, text=' '.join(r['text'] for r in res))), flush=True)
        else:
            json.dump(dict(file=w, duration=info.duration, segments=res), open(dst, 'w'))
            print(name, f'{info.duration:.0f}s', len(res), 'segments', flush=True)


if __name__ == '__main__':
    main()
