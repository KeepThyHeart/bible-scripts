"""Kokoro-specific phoneme symbols.

The pronunciation data is shared with Piper, so it is written in espeak-ng IPA, where a diphthong is
two symbols ("ɔɪ"). Kokoro was trained on misaki's phonemes, which write some diphthongs as one
symbol (Y = ɔɪ, A = eɪ). Fed the two-symbol form, Kokoro reads "joy" and "destroy" with an
"ah-ee" vowel; the single symbol gives the American "oy". Chosen by listening, 2026-11-26.
"""
import re

_SAITH = re.compile(r's([ˈˌ]?)eɪᵻθ(?=[\s,.;:!?»—–\-"”’)]|$)')


def kokoro_symbols(ps):
    """espeak-style IPA -> the symbols Kokoro reads best."""
    ps = ps.replace('ɔɪ', 'Y')
    # saith (lexicon: sˈeɪᵻθ): Kokoro's own long-a keeps the light second syllable from breaking the flow.
    return _SAITH.sub(r's\1Aᵻθ', ps)
