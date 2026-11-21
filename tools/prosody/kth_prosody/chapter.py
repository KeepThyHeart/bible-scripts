"""Synthesize the pieces of one chapter with the rules applied."""
from .rules import repeated_phrases, occurrences, word_stats, plan_edits, apply_edits


def synthesize_chapter(model, pieces, voice, speed, rules):
    """pieces: [{key, text, phonemes, detect?}]. Repeated phrases are found across the pieces
    with detect (default true; false for a chapter heading). Yields (piece, audio, edits)."""
    phrases = repeated_phrases([p['text'] for p in pieces if p.get('detect', True)], rules)
    for p in pieces:
        st, frames = model.predict(p['phonemes'], voice, speed)
        occ = []
        if phrases and p.get('detect', True):
            pw, occ = occurrences(p['text'], p['phonemes'], model.vocab, phrases, rules)
        if not occ:
            f0, en = model.pitch(st, frames)
            yield p, model.decode(st, f0, en), []
            continue
        f0, en = model.pitch(st, frames)
        frames2, edits = plan_edits(pw, occ, word_stats(pw, frames, f0, en), frames, rules)
        if not edits:
            yield p, model.decode(st, f0, en), []
            continue
        f0b, enb = model.pitch(st, frames2)
        f0b, enb = apply_edits(pw, frames2, f0b, enb, edits)
        yield p, model.decode(st, f0b, enb), [(pw[i][2], round(hz, 1)) for i, hz, _ in edits]
