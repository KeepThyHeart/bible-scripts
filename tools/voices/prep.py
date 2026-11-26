"""Cut LibriVox chapters into training utterances with corrected transcripts and phonemes.

  prosody/venv/bin/python -I tools/prep.py OUTDIR --item WAV:TXJSON[:BOOKTEXT] ... [--holdout 0.15]

For each item: drop the LibriVox prelude/closing (keywords in the whisper words, then skip to
where the book text starts aligning), align the whisper words to the book text (difflib on
normalised words) and use the book's words and punctuation where they align, keep the reader's
own words where whisper heard them confidently and the book differs only by insertions, and drop
utterances that do not align confidently. Without a book text, words below a confidence floor
drop their utterance. Utterances are 3-12 s, split at pauses. Writes OUTDIR/utts/*.wav (24 kHz)
and OUTDIR/utts.jsonl, plus OUTDIR/prep-report.json (cut points, corrections, drops)."""
import argparse, difflib, json, os, re, sys
import numpy as np
import soundfile as sf

SR = 24000
BOILER = re.compile(r"librivox|public domain|volunteer|read by|recorded by|recording by|recording is|"
                    r"this recording|for more information|end of (the )?(chapter|section|book)\b", re.I)
NUMW = set('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen '
           'seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand'.split())


ORD = 'first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth firstly secondly thirdly lastly'.split()
ENUM_W = set(ORD) | NUMW | set('number numbers verse verses chapter chapters through to and the of part'.split())
ROMAN = set('i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx'.split())
ENUM_B = ROMAN | set('v ver vs verse verses ch chap and to the of c'.split())


def enum_only(toks, side):
    S = ENUM_W if side == 'w' else ENUM_B
    return all(t.isdigit() or t in S or (side == 'w' and t in ORD) for t in toks)


def norm(w):
    w = w.lower().replace('’', "'").replace('‘', "'")
    w = re.sub(r"[^a-z0-9']", '', w).strip("'")
    return w


def book_tokens(path):
    raw = open(path, encoding='utf-8', errors='replace').read().replace('\r', '')
    raw = re.sub(r'\[Illustration[^\]]*\]', ' ', raw)
    raw = re.sub(r'_', '', raw)
    raw = raw.replace('--', ' -- ').replace('—', ' — ')
    toks = []
    for t in raw.split():
        n = norm(t)
        if n:
            toks.append((n, t))
        elif toks and t in ('--', '—'):
            if toks[-1][1][-1:].isalnum():
                toks[-1] = (toks[-1][0], toks[-1][1] + ',')  # dash reads as a short pause
    return toks


def locate(ww, bw):
    """Window of the book roughly covering the whisper words (rare 4-gram votes)."""
    idx = {}
    for i in range(len(bw) - 3):
        idx.setdefault(tuple(bw[i:i + 4]), []).append(i)
    votes = []
    for j in range(0, len(ww) - 3):
        hits = idx.get(tuple(ww[j:j + 4]))
        if hits and len(hits) <= 2:
            votes += [h - j for h in hits]
    if not votes:
        return None
    off = int(np.median(votes))
    return max(0, off - 300), min(len(bw), off + len(ww) + 300)


def segment(words, max_s=12.0, min_s=3.0, target=8.0):
    """Utterances as (i, j) word ranges, split at the longest pauses."""
    out, i, n = [], 0, len(words)
    while i < n:
        best, j = None, i
        while j < n and words[j]['e'] - words[i]['s'] <= max_s:
            dur = words[j]['e'] - words[i]['s']
            gap = (words[j + 1]['s'] - words[j]['e']) if j + 1 < n else 1.0
            end_p = words[j]['w'].strip()[-1:] in '.?!;:,'
            if dur >= min_s and (gap >= 0.25 or (end_p and gap >= 0.12)):
                score = gap + (0.3 if words[j]['w'].strip()[-1:] in '.?!' else 0) - 0.04 * abs(dur - target)
                if best is None or score > best[0]:
                    best = (score, j)
            j += 1
        if best is None:
            # no usable pause: skip to the next long pause
            k = i
            while k + 1 < n and words[k + 1]['s'] - words[k]['e'] < 0.3:
                k += 1
            i = k + 1; continue
        out.append((i, best[1])); i = best[1] + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('--item', action='append', required=True)
    ap.add_argument('--holdout', type=float, default=0.15); ap.add_argument('--min-prob', type=float, default=0.6)
    ap.add_argument('--clean-dir', help='use OUTDIR/clean/<name>.wav (e.g. de-breathed) instead of WAV for the cuts')
    a = ap.parse_args()
    from misaki import en, espeak
    g2p = en.G2P(trf=False, british=False, fallback=espeak.EspeakFallback(british=False))
    os.makedirs(os.path.join(a.out, 'utts'), exist_ok=True)
    rows, report = [], dict(items=[])
    for item in a.item:
        parts = item.split(':')
        wav, txp, text = parts[0], parts[1], (parts[2] if len(parts) > 2 else None)
        name = os.path.splitext(os.path.basename(wav))[0]
        src = os.path.join(a.clean_dir, name + '.wav') if a.clean_dir else wav
        audio, sr = sf.read(src, dtype='float32'); assert sr == SR, (src, sr)
        mpath = os.path.join(a.clean_dir, name + '.breathmask.npy') if a.clean_dir else None
        bmask = np.load(mpath) if mpath and os.path.exists(mpath) else None
        tx = json.load(open(txp))
        words = []
        for si, sg in enumerate(tx['segments']):
            for x in sg['words']:
                w = dict(w=x['w'], s=x['s'], e=x['e'], p=x['p'], seg=si)
                # whisper repeats the last word of a segment at the start of the next (low confidence): drop it
                if words and words[-1]['seg'] != si and norm(words[-1]['w']) == norm(w['w']) and w['p'] < 0.5:
                    words[-1]['e'] = w['e']; continue
                words.append(w)
        # whisper splits "o'clock", "forty-six", "1,950" into pieces without a leading space: rejoin
        merged = []
        for w in words:
            if merged and not w['w'].startswith(' ') and not merged[-1]['w'][-1:].isspace():
                m = merged[-1]; m['w'] += w['w']; m['e'] = w['e']; m['p'] = min(m['p'], w['p'])
            else:
                merged.append(w)
        words = [w for w in merged if norm(w['w'])]
        dur = len(audio) / SR
        # prelude / closing by keywords on a sliding 4-word window
        def phrase(k):
            return ' '.join(norm(w['w']) for w in words[k:k + 4])
        start, end = 0, len(words)
        for k in range(len(words)):
            if words[k]['s'] < 75 and BOILER.search(phrase(k)):
                start = max(start, min(len(words), k + 4))
        for k in range(len(words)):
            if words[k]['s'] > dur - 75 and BOILER.search(phrase(k)):
                end = min(end, k); break
        ww = [norm(w['w']) for w in words]
        info = dict(name=name, duration=round(dur, 1), prelude_kw_cut_word=start, closing_cut_word=end)
        amap = [None] * len(words)  # whisper word -> book token index (equal or corrected)
        kind = ['none'] * len(words)
        if text:
            bt = book_tokens(text); bw = [t[0] for t in bt]
            win = locate(ww[start:end], bw)
            if win is None:
                print(name, 'book text does not match; skipped', file=sys.stderr); continue
            b0, b1 = win
            sm = difflib.SequenceMatcher(None, ww[start:end], bw[b0:b1], autojunk=False)
            enum_book = set()  # book tokens of reference/enumeration apparatus the reader re-worded or skipped
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op in ('replace', 'delete', 'insert') and enum_only(ww[start + i1:start + i2], 'w') and enum_only(bw[b0 + j1:b0 + j2], 'b') \
                        and not (op == 'replace' and (i2 - i1) == (j2 - j1) and ww[start + i1:start + i2] == bw[b0 + j1:b0 + j2]):
                    enum_book.update(range(b0 + j1, b0 + j2))
                    for d in range(i2 - i1):
                        kind[start + i1 + d] = 'reader'
                    continue
                for d in range(i2 - i1):
                    wi = start + i1 + d
                    if op == 'equal':
                        amap[wi] = b0 + j1 + d; kind[wi] = 'equal'
                    elif op == 'replace':
                        if (i2 - i1) == (j2 - j1):
                            amap[wi] = b0 + j1 + d; kind[wi] = 'subst'
                        elif difflib.SequenceMatcher(None, ''.join(ww[start + i1:start + i2]),
                                                     ''.join(bw[b0 + j1:b0 + j2])).ratio() >= 0.85:
                            # same sounds, split differently ("anyone" / "any one"): take the book's
                            amap[wi] = (b0 + j1, b0 + j2) if d == 0 else None
                            kind[wi] = 'block' if d == 0 else 'blockcont'
                        else:
                            kind[wi] = 'replace_uneven'
                    elif op == 'delete':
                        kind[wi] = 'insert'   # reader (or whisper) has a word the book lacks
            # skip the title/announcement after the prelude: start at the first run of 6 equal words
            k = start
            while k < end - 6 and not all(kind[k + d] in ('equal', 'block', 'blockcont') for d in range(6)):
                k += 1
            info['prelude_cut_word'] = k; info['prelude_cut_s'] = round(words[k]['s'], 2)
            info['prelude_words'] = ' '.join(w['w'].strip() for w in words[:k])[-300:]
            start = k
            # likewise the closing: back off to the last run of 6 equal words
            k = end
            while k > start + 6 and not all(kind[k - 1 - d] in ('equal', 'block', 'blockcont') for d in range(6)):
                k -= 1
            end = k
        else:
            info['prelude_cut_word'] = start; info['prelude_cut_s'] = round(words[start]['s'], 2) if start < len(words) else None
            info['prelude_words'] = ' '.join(w['w'].strip() for w in words[:start])[-300:]
        info['closing_cut_s'] = round(words[end - 1]['e'], 2) if end > 0 else None
        info['closing_words'] = ' '.join(w['w'].strip() for w in words[end:])[:300]
        body = words[start:end]
        segs = segment(body)
        kept = corrected = dropped = 0; examples = []
        for (i, j) in segs:
            gi, gj = start + i, start + j
            ks = kind[gi:gj + 1]; ws = words[gi:gj + 1]
            wtext = ' '.join(w['w'].strip() for w in ws)
            reason = None; fixes = []
            if text:
                n_eq = sum(k_ in ('equal', 'block', 'blockcont', 'reader') for k_ in ks)
                if n_eq / len(ks) < 0.8: reason = f'aligned {n_eq}/{len(ks)}'
                elif 'replace_uneven' in ks: reason = 'uneven replace'
                out_t = []; last_b = None
                for w, k_, b in zip(ws, ks, amap[gi:gj + 1]):
                    if k_ == 'equal':
                        if last_b is not None and b > last_b + 1:
                            # book words the reader skipped (or whisper missed): doubtful
                            if not all(x in enum_book for x in range(last_b + 1, b)):
                                reason = reason or f'skipped book words {[t[1] for t in bt[last_b + 1:b]]}'
                        out_t.append(bt[b][1]); last_b = b
                    elif k_ == 'subst':
                        bw_, sw = bt[b][0], norm(w['w'])
                        sim = difflib.SequenceMatcher(None, bw_, sw).ratio()
                        numish = sw.isdigit() or sw in NUMW or bw_.isdigit() or bw_ in NUMW
                        if sim >= 0.5 or w['p'] < 0.8 or numish:
                            out_t.append(bt[b][1]); last_b = b; fixes.append(f"{w['w'].strip()} -> {bt[b][1]}")
                        else:
                            reason = reason or f"confident mismatch {w['w'].strip()!r} vs book {bt[b][1]!r}"
                    elif k_ == 'reader':
                        if w['p'] < 0.4 or re.search(r'\d[-–]\d', w['w']):
                            reason = reason or f"doubtful reference words {w['w'].strip()!r}"
                        out_t.append(w['w'].strip()); fixes.append(f"(reader) {w['w'].strip()}")
                    elif k_ == 'block':
                        out_t += [t[1] for t in bt[b[0]:b[1]]]; last_b = b[1] - 1
                        fixes.append(f"{w['w'].strip()}.. -> {' '.join(t[1] for t in bt[b[0]:b[1]])}")
                    elif k_ == 'blockcont':
                        pass
                    elif k_ == 'insert':
                        if w['p'] >= 0.85:
                            out_t.append(w['w'].strip())  # reader's own word, kept
                        else:
                            reason = reason or f"doubtful extra word {w['w'].strip()!r}"
                    else:
                        reason = reason or 'unaligned'
                utext = ' '.join(out_t)
                # carry sentence punctuation past the last word only if the book has it there
            else:
                if min(w['p'] for w in ws) < a.min_prob: reason = 'low whisper confidence'
                utext = wtext
            if any(re.search(r'\d', w['w']) for w in ws) and not text:
                reason = reason or 'digits without book text'
            if reason:
                dropped += 1
                if len(examples) < 400: examples.append(dict(drop=reason, whisper=wtext))
                continue
            if fixes:
                corrected += 1; examples.append(dict(fixes=fixes, whisper=wtext, text=utext))
            # audio bounds: pad into the neighbouring pauses
            prev_e = words[gi - 1]['e'] if gi > 0 else 0.0
            next_s = words[gj + 1]['s'] if gj + 1 < len(words) else dur
            t0 = max(prev_e + 0.02, ws[0]['s'] - min(0.25, max(0, ws[0]['s'] - prev_e) / 2))
            t1 = min(next_s - 0.02, ws[-1]['e'] + min(0.3, max(0, next_s - ws[-1]['e']) / 2))
            clip = audio[int(t0 * SR):int(t1 * SR)]
            ps, _ = g2p(utext)
            uid = f'{name}_{len(rows):04d}'
            sf.write(os.path.join(a.out, 'utts', uid + '.wav'), clip, SR)
            if bmask is not None:  # breath-like 10 ms frames left in the clip: masked out of the mel loss
                np.save(os.path.join(a.out, 'utts', uid + '.mask.npy'), bmask[int(t0 * 100):int(t0 * 100) + int((t1 - t0) * 100) + 1])
            rows.append(dict(id=uid, src=name, t0=round(t0, 3), t1=round(t1, 3), dur=round(t1 - t0, 3),
                             text=utext, whisper=wtext, phonemes=ps, corrected=bool(fixes)))
            kept += 1
        info.update(segments=len(segs), kept=kept, corrected=corrected, dropped=dropped, examples=examples)
        report['items'].append(info)
        print(f"{name}: prelude cut at {info.get('prelude_cut_s')}s, closing at {info['closing_cut_s']}s, "
              f"{len(segs)} utts, kept {kept} ({corrected} corrected), dropped {dropped}", flush=True)
    # hold out every k-th utterance (spread over all sources)
    k = max(2, round(1 / a.holdout))
    for n, r in enumerate(rows):
        r['split'] = 'val' if n % k == k // 2 else 'train'
    with open(os.path.join(a.out, 'utts.jsonl'), 'w') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    tr = [r for r in rows if r['split'] == 'train']; va = [r for r in rows if r['split'] == 'val']
    report.update(total=len(rows), train=len(tr), val=len(va),
                  train_min=round(sum(r['dur'] for r in tr) / 60, 1), val_min=round(sum(r['dur'] for r in va) / 60, 1))
    json.dump(report, open(os.path.join(a.out, 'prep-report.json'), 'w'), indent=1, ensure_ascii=False)
    print({k_: v for k_, v in report.items() if k_ != 'items'})


if __name__ == '__main__':
    main()
