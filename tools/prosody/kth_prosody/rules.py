"""The softening rules: which phrases repeat, and how to correct them where Kokoro over-stresses.

Pure functions over the model's predictions (frames per token, pitch, energy), so they are
tested without the model.
"""
import hashlib, json, os
from collections import Counter
import numpy as np
from .align import text_words, phoneme_words, align, sounds_like

DEFAULT_RULES = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'prosody', 'rules.json')


def load_rules(path=None):
    path = path or DEFAULT_RULES
    raw = open(path, 'rb').read()
    r = json.loads(raw)
    if r.get('format') != 'kth-prosody-rules/1':
        raise ValueError(f'{path}: not a kth-prosody-rules/1 file')
    r['hash'] = hashlib.sha256(raw).hexdigest()
    return r


def repeated_phrases(texts, rules):
    """Phrases of small words only, repeated at least minCount times across the texts (a
    chapter). Content words are new information and never qualify; nor do bare
    preposition + article pairs ("of the"), which Kokoro already reads lightly."""
    rp = rules['repeated']; small = set(rp['smallWords']); prep = set(rp['prepositions']) | {'the', 'a'}
    c = Counter()
    for t in texts:
        w = text_words(t); seen = set()
        for n in range(rp['minWords'], rp['maxWords'] + 1):
            for i in range(len(w) - n + 1):
                g = tuple(w[i:i + n])
                if g not in seen:
                    seen.add(g); c[g] += 1
    keep = [g for g, k in c.items() if k >= rp['minCount'] and all(x in small for x in g)
            and not all(x in prep for x in g)]
    # a phrase inside a longer kept one with the same count adds nothing
    def inside(g, h):
        return len(h) > len(g) and any(h[i:i + len(g)] == g for i in range(len(h) - len(g) + 1))
    keep = [g for g in keep if not any(inside(g, h) and c[h] == c[g] for h in keep)]
    return sorted(keep, key=lambda g: (-len(g), g))


def occurrences(text, ps, vocab, phrases, rules):
    """Phoneme words plus, per occurrence of a phrase, (its phoneme word indices, the index of
    the next content word or None). Empty when the words cannot be matched safely."""
    small = set(rules['repeated']['smallWords'])
    tw = text_words(text); pw = phoneme_words(ps, vocab)
    amap = align(tw, pw, small)
    if amap is None:
        return pw, []
    out = []
    for g in phrases:
        n = len(g)
        for i in range(len(tw) - n + 1):
            if tuple(tw[i:i + n]) != g:
                continue
            if not all(sounds_like(tw[j], pw[amap[j]][2]) or (j > 0 and amap[j] == amap[j - 1])
                       for j in range(i, i + n)):
                continue
            nxt = next((amap[j] for j in range(i + n, len(tw)) if tw[j] not in small), None)
            if nxt is not None and nxt in {amap[j] for j in range(i, i + n)}:
                nxt = None
            out.append((sorted({amap[j] for j in range(i, i + n)}), nxt))
    return pw, out


def word_stats(pw, frames, f0, energy):
    """Per phoneme word: frames, pitch peak (Hz, voiced frames) and mean energy. Pitch and
    energy run at twice the frame rate."""
    st = np.concatenate([[0], np.cumsum(frames)]); out = []
    for a, b, _ in pw:
        s, e = 2 * st[a], 2 * st[b + 1]
        v = f0[s:e][f0[s:e] > 60]
        out.append(dict(frames=int(st[b + 1] - st[a]), peak=float(v.max()) if len(v) else 0.0,
                        energy=float(energy[s:e].mean()) if e > s else 0.0))
    return out


def plan_edits(pw, occ, stats, frames, rules):
    """New frames per token, and (word index, Hz down (negative = up), energy drop) edits."""
    s = rules['soften']; frames2 = frames.copy(); edits = []
    peaks = [w['peak'] for w in stats if w['peak']]
    for idx, nxt in occ:
        ref = stats[nxt]['peak'] if nxt is not None and stats[nxt]['peak'] else (np.median(peaks) if peaks else 0)
        hit = False
        for i in idx:
            over = stats[i]['peak'] - (ref - s['marginHz'])
            if stats[i]['peak'] and over > 0:
                a, b, _ = pw[i]
                if stats[i]['frames'] > s['minFramesToShorten']:
                    frames2[a:b + 1] = np.maximum(1, np.round(frames[a:b + 1] * s['durationScale'])).astype(int)
                edits.append((i, float(over), s['energyDrop'])); hit = True
        if hit and nxt is not None and s['liftNextHz']:
            edits.append((nxt, -float(s['liftNextHz']), 0.0))
    return frames2, edits


def taper(n, edge=6):
    w = np.ones(n)
    for i in range(min(edge, n // 2)):
        w[i] = w[-1 - i] = (i + 1) / (edge + 1)
    return w


def apply_edits(pw, frames2, f0, energy, edits):
    """Shift pitch (voiced frames only) and energy over each edited word, tapered at the edges."""
    f0 = f0.copy(); energy = energy.copy(); cs = np.concatenate([[0], np.cumsum(frames2)])
    for i, hz, drop in edits:
        a, b, _ = pw[i]
        s0, e0 = max(0, 2 * cs[a] - 6), min(len(f0), 2 * cs[b + 1] + 6)
        w = taper(e0 - s0)
        f0[s0:e0] -= hz * w * (f0[s0:e0] > 60)
        energy[s0:e0] -= drop * w
    return f0, energy
