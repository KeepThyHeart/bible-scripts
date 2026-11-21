"""Render sample chapters to Ogg Opus from a phoneme dump (JSON lines with book, chapter,
verse, text, phonemes), for listening tests. Verse 0 is the chapter heading."""
import argparse, json, os, re, subprocess, sys, time
import numpy as np


def trim(a, thr=2e-3, pad=720):
    nz = np.flatnonzero(np.abs(a) > thr)
    return a if not len(nz) else a[max(0, nz[0] - pad):nz[-1] + pad]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('dump'); ap.add_argument('out'); ap.add_argument('chapters', nargs='+', help='BOOK.CHAPTER, e.g. 2.40')
    ap.add_argument('--voice', default='am_michael:0.5+bm_george:0.5'); ap.add_argument('--speed', type=float, default=0.9)
    ap.add_argument('--gap-ms', type=int, default=350); ap.add_argument('--intro-gap-ms', type=int, default=700)
    ap.add_argument('--rules'); ap.add_argument('--threads', type=int)
    ap.add_argument('--light-the', action='store_true', help='read a free-standing "the" as ðə (the dump has audio-build clear speech, which makes it ðʌ)')
    a = ap.parse_args(argv)
    import warnings; warnings.filterwarnings('ignore')
    from .rules import load_rules
    from .model import Kokoro, SAMPLE_RATE
    from .chapter import synthesize_chapter
    want = [tuple(map(int, c.split('.'))) for c in a.chapters]; rows = {}
    for line in open(a.dump):
        r = json.loads(line); k = (r['book'], r['chapter'])
        if k in want:
            if a.light_the:
                r['phonemes'] = re.sub(r'(?<![^ ,.;:!?])ðʌ(?![^ ,.;:!?])', 'ðə', r['phonemes'])
            rows.setdefault(k, []).append(dict(r, key=f"{r['verse']}.{r.get('piece', 0)}", detect=r['verse'] != 0))
    rules = load_rules(a.rules); model = Kokoro(a.threads); os.makedirs(a.out, exist_ok=True)
    for k in want:
        t = time.time(); parts = []; prev = None; edited = 0
        for p, audio, edits in synthesize_chapter(model, rows[k], a.voice, a.speed, rules):
            if prev is not None and p['verse'] != prev:
                parts.append(np.zeros(int((a.intro_gap_ms if prev == 0 else a.gap_ms) * SAMPLE_RATE / 1000), np.float32))
            parts.append(trim(audio)); prev = p['verse']; edited += bool(edits)
        wav = os.path.join(a.out, f'{k[0]:02d}-{k[1]:03d}.f32'); ogg = wav[:-4] + '.ogg'
        np.concatenate(parts).astype('<f4').tofile(wav)
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'f32le', '-ar', str(SAMPLE_RATE), '-ac', '1', '-i', wav,
                        '-c:a', 'libopus', '-b:a', '24k', ogg], check=True)
        secs = os.path.getsize(wav) / 4 / SAMPLE_RATE; os.remove(wav)
        print(f'{k[0]}.{k[1]}: {secs / 60:.1f} min audio in {(time.time() - t) / 60:.1f} min, {edited} pieces softened', flush=True)


if __name__ == '__main__':
    main()
