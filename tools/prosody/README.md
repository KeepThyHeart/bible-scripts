# kth-prosody

Kokoro-82M (PyTorch) with control over what it predicts between reading the phonemes and
making the sound: the length of each phoneme, the pitch and the loudness. The rules in
[`prosody/rules.json`](../../prosody/rules.json) use that to soften repeated small-word phrases
("And thou shalt ...") where Kokoro over-stresses them. The audio quality is Kokoro's own,
because Kokoro's decoder still renders the sound.

Used by the Bible repo's `audio-build` as an engine (it starts `python -m kth_prosody.worker`).
The text, phonemes, pronunciation corrections, file layout and manifests all stay in audio-build.

## How the rules work

1. **Find the formula.** Within a chapter, phrases of 2 to 5 *small words* (and, thou, shalt, he,
   shall, be ...) that occur at least 3 times. Content words never qualify, nor do "not" and
   "all", which usually carry meaning. Bare pairs like "of the" don't either.
2. **Measure.** For each occurrence, Kokoro's predicted pitch peak of each phrase word is
   compared with the next content word (usually the verb).
3. **Correct only where needed.** If a phrase word rises above `verb - marginHz`, it is lowered
   by the excess, shortened (`durationScale`) and made quieter (`energyDrop`), and the verb is
   raised by `liftNextHz`. Where Kokoro already reads the phrase lightly, nothing changes.

Each verse's words are matched to the words of its phoneme string, which espeak sometimes joins
("of the" -> ʌvðə). A verse that cannot be matched safely, about 0.6% of the KJV, is left as
Kokoro reads it, never guessed.

## Kokoro's own symbols

The pronunciation data is shared with Piper, so it uses espeak-ng IPA. Before predicting, each piece's
phonemes go through `kth_prosody/symbols.py`, which swaps in the single symbols Kokoro was trained on
where they sound better: `ɔɪ` -> `Y` (joy, destroy: an American "oy", not "ah-ee"), and saith
`sˈeɪᵻθ` -> `sˈAᵻθ` (a light second syllable that keeps the flow).

## Setup

```sh
python3 -m venv venv
venv/bin/pip install -r tools/prosody/requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
```

The first run downloads the model and voices from Hugging Face (`hexgrad/Kokoro-82M`, about
330 MB) into the Hugging Face cache.

## Use

```sh
cd tools/prosody
../../venv/bin/python -m unittest discover -s tests -t .        # tests (no model needed)
../../venv/bin/python -m kth_prosody.worker [--threads N]       # the audio-build worker (JSON lines)
../../venv/bin/python -m kth_prosody.render DUMP.jsonl OUT 2.40 19.136 --light-the   # samples
```

The worker protocol is documented at the top of `kth_prosody/worker.py`. `render` reads a
phoneme dump (JSON lines with `book`, `chapter`, `verse`, `text`, `phonemes`; verse 0 is the
chapter heading) and writes Ogg Opus for listening tests.

## Speed

On a 6-core CPU with 4 threads, about 0.5 s of computing per second of audio.
