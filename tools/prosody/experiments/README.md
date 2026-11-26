# Prosody experiments

Scratch scripts from the listening rounds (pauses, paragraph breaks, voice shaping, softening).
`pauses.py` renders chapters with punctuation- and paragraph-based pauses; `web_paragraphs.py`
built `prosody/kjv-paragraph-supplement.json` (paragraph starts after the last KJV pilcrow, taken
from the public-domain World English Bible). `dump-phon.js` / `dump-all.js` dump the phoneme strings
audio-build would synthesize (run from audio-build's `dist`). Paths assume `~/bible-audio/`.
Kept for reference; the maintained engine is `../kth_prosody`.
