"""Verse-timing manifests (kth-audio-chapter/1) for chapters rendered before chapters.py wrote them.

The renderer joins trimmed pieces (sentences) with silences of known length: INTRO after the
"Book, chapter N" lead-in, then sentence / paragraph / clause / comma gaps by the same rules as
chapters.py. This finds the silent runs in the .ogg and picks, in order, the runs that best match
the expected gap sequence (dynamic programming), then reads piece and verse bounds off them.
  timings.py OUTDIR [BOOK.CHAPTER ...]     default: every kjv-*.ogg in OUTDIR without a .json
  timings.py --check OUTDIR B.C            compare against an exact manifest (renderer-written)
"""
import hashlib, json, os, re, sqlite3, subprocess, sys
import numpy as np

SR = 24000
S, P, C, K, INTRO = 1100, 2400, 650, 350, 1300
PAD = 0.030                       # chapters.py keeps 720 samples (30 ms) beyond the speech
HOP = 0.005                       # analysis frame
SUPP = json.load(open(os.path.expanduser('~/bible-audio/paragraphs/kjv-supplement.json')))['breaks']
END = re.compile(r'(?<=[.!?])\s+')
_rows = {}


def rows_for(book, chap):
    if not _rows:
        for l in open(os.path.expanduser('~/bible-audio/prosody/work/kjv-phonemes.jsonl')):
            r = json.loads(l); _rows.setdefault((r['book'], r['chapter']), []).append(r)
    return _rows[(book, chap)]


def pieces_and_gaps(book, chap):
    db = sqlite3.connect(os.path.expanduser('~/bible-audio/online/bible_kjv.db'))
    para = {vid % 1000 for vid, f in db.execute('select verse_id, formatting from bible_verse where verse_id/1000=?', (book * 1000 + chap,))
            if f and json.loads(f).get('block', {}).get('paragraph_start')}
    para |= set(SUPP.get(str(book), {}).get(str(chap), []))
    pieces = []
    for r in rows_for(book, chap):
        ts, ps = END.split(r['text'].strip()), END.split(r['phonemes'].strip())
        if len(ts) != len(ps): ts, ps = [r['text']], [r['phonemes']]
        for t, p in zip(ts, ps): pieces.append((r['verse'], t, len(p)))
    gaps = []
    for (v, t, _), (nv, _, _) in zip(pieces, pieces[1:]):
        if v == 0 and len(gaps) == 0: gaps.append(INTRO); continue
        if nv != v:
            e = t.rstrip(' )')[-1:]
            gaps.append(P if nv in para else S if e in '.?!' else C if e in ';:' else K)
        else: gaps.append(S)
    return pieces, gaps


def silences(a):
    h = int(HOP * SR); n = len(a) // h
    frames = np.abs(a[:n * h]).reshape(n, h).max(1)
    quiet = frames < 4e-3
    runs, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]: j += 1
            if (j - i) * HOP >= 0.2: runs.append((i * HOP, j * HOP))
            i = j
        else: i += 1
    return runs


def align(runs, gaps, pieces, dur):
    """Choose len(gaps) runs in order; cost = gap-length mismatch + speech-length mismatch + unused long runs."""
    N, M = len(gaps), len(runs)
    rate = 0.058                                   # seconds of speech per phoneme character (measured on Gen 6, Isa 62)
    INF = 1e18
    lens = [b - a for a, b in runs]
    skip = [max(0.0, l - 0.80) * 10 for l in lens]  # pauses inside speech run up to ~0.7 s; longer ones are inserted gaps
    pre = np.concatenate([[0], np.cumsum(skip)])
    dp = np.full((N + 1, M + 1), INF); bk = np.zeros((N + 1, M + 1), int)
    dp[0, :] = 0
    # dp[i][j]: boundaries 1..i matched, boundary i to run j-1
    for i in range(1, N + 1):
        g = gaps[i - 1] / 1000 + 0.06            # an inserted gap reads as its length + ~60 ms
        sp_est = pieces[i - 1][2] * rate
        for j in range(i, M - (N - i) + 1):
            gc = min(abs(lens[j - 1] - g) / 0.04, 40)
            best, arg = INF, 0
            lo = i - 1
            for k in range(max(lo, j - 25), j):    # previous boundary at run k-1 (k=0: start of file); at most 25 runs back
                if dp[i - 1, k] >= INF: continue
                if i == 1 and k != 0: continue
                start = runs[k - 1][1] if k else 0.0
                sp = runs[j - 1][0] - start
                if sp <= 0.15: continue
                c = dp[i - 1, k] + 4 * abs(np.log(sp / max(sp_est, 0.3))) + (pre[j - 1] - pre[k])
                if c < best: best, arg = c, k
            if best < INF: dp[i, j], bk[i, j] = best + gc, arg
    tail = [dp[N, j] + (pre[M] - pre[j]) for j in range(M + 1)]
    j = int(np.argmin(tail))
    if tail[j] >= INF: return None, None
    chosen = []
    for i in range(N, 0, -1): chosen.append(j - 1); j = bk[i, j]
    chosen.reverse()
    bounds, start = [], 0.0
    for c in chosen: bounds.append((start, runs[c][0] + PAD)); start = runs[c][1] - PAD
    last_end = runs[-1][0] + PAD if runs and runs[-1][1] >= dur - 0.05 else dur
    bounds.append((start, last_end))
    return bounds, float(min(tail))


def decode(ogg):
    pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', ogg, '-f', 'f32le', '-ac', '1', '-ar', str(SR), '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, '<f4')


def manifest(out, book, chap, narrator, rev='1'):
    ogg = os.path.join(out, f'kjv-{book:02d}-{chap:03d}.ogg')
    a = decode(ogg); dur = len(a) / SR
    pieces, gaps = pieces_and_gaps(book, chap)
    bounds, cost = align(silences(a), gaps, pieces, dur)
    if bounds is None: return None, None
    intro, verses = None, {}
    for (v, _, _), (s, e), i in zip(pieces, bounds, range(len(pieces))):
        s, e = round(max(0.0, s), 3), round(min(e, dur), 3)
        if v == 0 and i == 0: intro = [s, e]; continue
        if v in verses: verses[v][1] = e
        else: verses[v] = [s, e]
    data = open(ogg, 'rb').read()
    m = dict(schema='kth-audio-chapter/1', module='KJV', book=book, chapter=chap, narrator=narrator, rev=rev,
             duration=round(dur, 3), files=[dict(codec='opus', mime='audio/ogg; codecs=opus', path=f'{book}/{chap:03d}.ogg',
                                                 bytes=len(data), sha256=hashlib.sha256(data).hexdigest())],
             **({'intro': intro} if intro else {}), verses=[[v, s, e] for v, (s, e) in sorted(verses.items())],
             timing='silence-aligned')
    return m, cost / max(1, len(gaps))


if __name__ == '__main__':
    args = sys.argv[1:]
    check = args and args[0] == '--check'
    if check: args = args[1:]
    out = os.path.expanduser(args[0]); narrator = os.path.basename(out.rstrip('/'))[4:] or 'default'
    todo = [tuple(map(int, c.split('.'))) for c in args[1:]] or sorted(
        (int(f[4:6]), int(f[7:10])) for f in os.listdir(out)
        if re.fullmatch(r'kjv-\d\d-\d{3}\.ogg', f) and not os.path.exists(os.path.join(out, f[:-4] + '.json')))
    worst = []
    for b, c in todo:
        m, q = manifest(out, b, c, narrator)
        if check:
            ex = json.load(open(os.path.join(out, f'kjv-{b:02d}-{c:03d}.json')))
            d = [max(abs(x[1] - y[1]), abs(x[2] - y[2])) for x, y in zip(ex['verses'], m['verses'])]
            print(f'{b}.{c}: verses {len(ex["verses"])}/{len(m["verses"])}, max diff {max(d):.3f}s, mean {np.mean(d):.3f}s, cost {q:.2f}')
            continue
        if m is None: print(f'{b}.{c}: FAILED to align'); continue
        json.dump(m, open(os.path.join(out, f'kjv-{b:02d}-{c:03d}.json'), 'w'), separators=(',', ':'))
        worst.append((q, b, c))
    if worst:
        worst.sort(reverse=True)
        print(f'{len(worst)} manifests written; highest cost per gap: ' + ', '.join(f'{b}.{c}={q:.2f}' for q, b, c in worst[:8]))
