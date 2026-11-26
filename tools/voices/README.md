# Kokoro voices fitted to LibriVox readers

Tools that adjust a Kokoro-82M voice pack (style vector, `[510, 1, 256]`) toward one LibriVox
reader, then blend fitted packs and render the KJV with them. Uses `kth_prosody` from `../prosody`
and the same venv. Audio, packs and working files live outside git (by default under
`~/bible-audio/voices/`); only code and small data are kept here.

## Fitting a reader

```sh
HOURS=1.0 DEBREATH=1 tools/voices/fit_reader.sh NAME TEXT_URL_OR_FILE MP3_URL [MP3_URL ...]
```

`-` as the text means Whisper only; `MP3_URL::TEXT` gives per-file text; `STAGE=N` resumes.
Stages (scripts):

1. `prep.py` downloads, cuts the LibriVox prelude and closing, splits at pauses.
2. `transcribe.py` (faster-whisper base.en), then aligned to the book text and corrected
   (`prep.py`); doubtful segments are dropped. `wer.py` reports what is left.
3. `debreath.py` removes breaths found from voicing (50 ms autocorrelation), protecting initial
   and final unvoiced consonants; a pre-onset rule handles inhalations before phrases.
   `debreath_check.py` compares before/after.
4. `init_voice.py` picks the closest stock voice or blend; `fit.py` optimises a shared offset plus
   an 8-knot per-row part (mel L1 after DTW, speaker-embedding cosine via `spk.py`, F0 median and
   spread, energy spread, L2 pull to the init). `evaluate.py` scores held-out similarity; the best
   checkpoint is kept only if renders keep their voicing (`check_outputs.py`). `finalize.py`
   writes `NAME.pt` and a report.

## Rendering

- `render.py OUT.ogg --pack P.pt --verses 43.3.16` renders verses; loudness is matched to
  -26.6 dB RMS.
- `chapters.py OUTDIR PREFIX 1100 2400 650 350 BOOK.CHAPTER ...` renders whole chapters with
  sentence / paragraph / clause / comma pauses (ms) like `../prosody/experiments/pauses.py`.
  Env: `PACK`, `OY=Y`, `SUPP=1` (paragraph supplement, `prosody/kjv-paragraph-supplement.json`
  copied to `~/bible-audio/paragraphs/kjv-supplement.json`), `INTRO_MS`, `END_MS`, `THREADS`.
  It also applies the pronunciation picks of 2026-11-26 to the pre-dumped phonemes (verily,
  merciful, yea, saith); the same picks are in the lexicon and `kth_prosody/symbols.py`, so a
  fresh phoneme dump makes these local rewrites redundant.
- `render-bible.sh PACK OUTDIR [WORKERS]` renders the whole KJV, resumable (`BOOKS=` limits it).
- `bible-job.sh start|stop|status nt|ot` runs the New and Old Testament as separate resumable
  jobs over disjoint books, so both can run at once and each can be stopped after its current chapter.

Blend recipes and the three fitted readers are in `blends.json`.
