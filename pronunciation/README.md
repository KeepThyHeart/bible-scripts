# Pronunciation overlay

IPA corrections the Bible app's speech engines (Piper in the browser, Piper and Kokoro in `tools/audio-build`) apply on top of espeak-ng: names, archaic forms, traditional readings, and homographs whose sound depends on the verse. The Bible repo holds the code that reads these files (`packages/core/src/audio/pronunciation.ts`, design in `apps/web/docs/features/audio-pronunciation.md`); this folder holds the data, so the app repo does not grow with every language and translation.

## Layers

| File | Format | What | Shared by |
|---|---|---|---|
| `<locale>/lexicon.json` (`en-us/`) | `kth-pron-lexicon/2` | `entries`: word to IPA (names, archaic forms, words with one reading). `senses`: the IPA of each meaning of a homograph (`blessed` → `adjective` / `verb`). | every translation spoken in that locale |
| `<language>/<MODULE>.json` (`en/KJV.json`) | `kth-pron-senses/1` | which meaning each homograph has, per verse and word index. A meaning is the same in every accent, so one file serves `en-us` and `en-gb`. | every locale of the language |
| `<locale>/<MODULE>.json` (optional) | `kth-pron-module/1` | IPA for single occurrences in one translation and one accent, when neither layer above fits. | one translation in one accent |

Lookup for a word: locale override for the translation, then its meaning (from the language file) through the locale's `senses`, then the locale lexicon, then espeak's own pronunciation. A meaning the locale has no IPA for falls through to espeak.

- **Entries:** a lower-case key matches any case; a capitalised key (`Job`) matches only a capitalised word, so a name never changes a common word. A key may be a hyphenated compound (`beth-lehem`). Possessives are derived by the app (`Job's` = `Job` + z), so they are listed only when the base is not.
- **Occurrences:** `"GEN.1.22": {"hash": "…", "words": {"2": ["blessed", "verb"]}}`. The word index counts `\p{L}+('\p{L}+)*` words of the verse as the app speaks it (`TextPreparer.verse()`), and the hash is FNV-1a 64 of that text. If the module's text changes, the app skips the verse's entries (and `audio-build` reports them) instead of placing them on the wrong word; regenerate them from a new dump.
- **IPA:** espeak-ng style, the symbols and stress placement in `CONVENTIONS.md`.

Current data: `en-us` (American English, common American Protestant reading-aloud tradition): 2,126 words and 45 homographs with their meanings; `en/KJV.json`: 1,781 occurrences in 1,652 verses.

## Checking and publishing

```bash
node scripts/pronunciation/check.js                        # validate; fails if index.json is stale
node scripts/pronunciation/check.js --write-index          # after editing any file
node scripts/pronunciation/check.js --verify-dump=verses.jsonl   # every occurrence against the module text
```

`index.json` lists every file with its size and SHA-256. The Bible repo's installer (`apps/web/scripts/fetch-piper-assets.mjs`) reads it, from a checkout next to the Bible repo (`../bible-scripts`) or from GitHub at the commit it pins, and verifies each file before copying it into `<data>/audio/pronunciation/`.

## Making or extending a module's data

1. In the Bible repo: `pnpm build:core && pnpm --filter @bible/audio-build run build`, then
   `node tools/audio-build/scripts/pronunciation-dump.mjs --module-file=<bible_xxx.db> --out=<dir>`.
   It writes `words.jsonl` (every distinct word with espeak's IPA, counts, capitalisation, sample refs) and `verses.jsonl` (every verse as spoken, with its phonemes).
2. Review distinct words once each (espeak says a word the same way everywhere), adding corrections to the locale lexicon; review every homograph occurrence in its verse and record its meaning in `<language>/<MODULE>.json`. Follow `CONVENTIONS.md`. Ask the owner only about well-known names whose pronunciation is disputed.
3. `check.js --write-index --verify-dump=<dir>/verses.jsonl`.

The KJV data was made this way on 2026-11-10: 12,676 distinct words and 1,790 homograph occurrences reviewed, about 230 well-known names checked again by hand.

## Open questions (defaults in use)

| Word | Default | Alternative |
|---|---|---|
| Baal (Baalim, Baal's) | BAY-uhl `bˈeɪəl` | BAHL `bˈɑːl` |
| Amen | ay-MEN (espeak's own) | ah-MEN `ˌɑːmˈɛn` |
| Habakkuk | huh-BAK-uk `həbˈækək` | HAB-uh-kuk `hˈæbəkˌʌk` |
| Shechem | SHEE-kem `ʃˈiːkəm` | SHEK-em `ʃˈɛkəm` |
| Philemon | fye-LEE-mun `faɪlˈiːmən` | fih-LEE-mun `fᵻlˈiːmən` |
| Jairus | jay-EYE-rus `dʒeɪˈaɪɹəs` | JAIR-us `dʒˈɛɹəs` |
| Bernice | ber-NYE-see `bɚnˈaɪsi` | ber-NEECE `bɚnˈiːs` |
| "be blessed" / "are cursed" (passive) | one syllable, blest / curst | two syllables, BLESS-ed |

Not yet heard by anyone: listen to name-heavy chapters (Genesis 10, 1 Chronicles 1-9, Numbers 1, Ezra 2, Matthew 1).
