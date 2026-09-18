# swordcheck: bulk SWORD conversion and verification

`swordcheck.py` converts SWORD modules with the converters in `tools/import/sword/`
and checks every result against an independent reading of the original module.
The tool does the bulk of the checking; a person or an AI agent reads its reports
and follows up only where they look fishy, using the `probe` subcommand.

The independent reading comes from `probe/sword-probe` (C++, libsword). It takes
the verse text from libsword's own strip filters and red letter from libsword's
own HTML renderer, not from the converters' OSIS parser. So a parser bug shows up
as a disagreement instead of being copied into both sides of the comparison.

## Build

```bash
sudo apt-get install -y build-essential libsword-dev libsqlite3-dev libzip-dev
for d in common bible commentary dictionary book devotional probe; do make -C tools/import/sword/$d; done
npm install          # optional: lets swordcheck also run scripts/modules/validate-module.js
```

Python 3 standard library only.

## Commands

```bash
cd tools/import/sword/verify
CAT=path/to/sword_modules_catalog.csv

# Which modules a batch would take (dry run), and why the rest are excluded
python3 swordcheck.py select --catalog $CAT --license open -v

# Download, convert and verify: a stratified sample, or everything selected
python3 swordcheck.py batch --catalog $CAT --work /data/sword --sample 60 --jobs 3
python3 swordcheck.py batch --catalog $CAT --work /data/sword --jobs 3 --keep-db flagged

# One module you already converted
python3 swordcheck.py verify --zip KJV.zip --db bible_kjv.db --log kjv.log --reference bible_kjv_ref.db

# Look closer: SWORD raw / rendered / plain next to the DB row (KJV numbering)
python3 swordcheck.py probe --zip DRC.zip --db bible_drc.db "Jonah 1:17" "Jonah 2:1"
python3 swordcheck.py probe --zip RSP.zip --db bible_rsp.db --grep '\\'      # verses whose DB text matches a regex
python3 swordcheck.py probe --zip X.zip --db x.db --sql "SELECT verse_id FROM bible_verse WHERE word_count > 120"

# Rebuild summary.md / summary.csv from the results of earlier batches
python3 swordcheck.py summary --work /data/sword
```

`sword-probe` can also be used on its own:
`sword-probe -i MODULE.zip info | dump | verse "John 3:16" ...` (dump writes JSON Lines).

### Licence policy

Only rows whose catalog `conversion_license_verdict` is usable are selected.
`NEEDS PERMISSION` and `MANUAL REVIEW` are never selected, whatever the options.
`--license` widens the selection in tiers:

| tier | adds verdicts |
|---|---|
| `open` (default) | OK, OK (attribution), OK (attribution; share-alike), OK (copyleft) |
| `verbatim` | OK (verbatim; attribution), LIKELY OK (verbatim; verify): CC ND. Remapping verses or dropping books may count as an adaptation |
| `noncommercial` | OK IF NON-COMMERCIAL (...): only if the app is non-commercial |

The representative sample (this task) used `open` only. For a full conversion,
`verbatim` and `noncommercial` are approved too, as long as each module's licence
carries over accurately: `license_spdx` is derived straight from the module's own
`DistributionLicense` (see `licenseToSpdx` in `sword_common.cpp`), and `verify`'s
`license_conflict` check flags any module whose own copyright text implies a
different (typically stricter) licence than `license_spdx` says — e.g. THOT,
whose `DistributionLicense` says CC BY 4.0 but whose `ShortCopyright` attributes
the underlying text to Tyndale House under CC BY-NC-ND 4.0. Flagged conflicts
need a person to pick the correct licence before that module ships; the tool
does not guess.

Encrypted modules are skipped. When a module id is published by several
repositories, the main CrossWire copy is preferred and the attic copy is never
chosen over another one.

## Output (batch `--work DIR`)

```
cache/<repository>/<Module>.zip     downloads (kept; reruns do not download again)
db/<type>_<module>.db               converted modules (--keep-db all|flagged|none)
logs/<Module>.convert.log           converter output
reports/<Module>/report.md          findings, statistics and sample verses, for review
reports/<Module>/report.json        the same, machine-readable
results.jsonl                       one line per processed module (batches accumulate)
summary.md / summary.csv            every module: status, finding codes, key numbers
```

The alignment checks use a reference KJV database, `db/bible_kjv.db`, which the
batch converts first from CrossWire's KJV.

A module is `PASS`, `WARN` (look at it) or `FAIL` (something is wrong in the
converted data). `NOT CONVERTED` means the converter refused it, usually because
no verse map exists for its versification. `ERROR` means the pipeline itself broke.

## What a bible report checks

Each check compares the DB against libsword's reading of the same module.

| Section | Checks |
|---|---|
| counts | Source entries, entries in the 66-book canon, entries skipped and why, verses missing from the DB, DB verses with no source, merged ids, SWORD linked entries |
| fidelity | Per-verse text after normalising (letters and digits, case-folded) against libsword's plain text: exact / minor / material differences, the worst ones side by side, and total words |
| red_letter | Verses with words of Christ in the source (libsword's red-letter render) against DB verses with a `words_of_christ` span; red letter outside the Gospels, Acts, 1-2 Cor and Revelation |
| canon | Every verse id inside the 66-book KJV canon; canonical books in the source that went missing; skipped non-canonical books; holes; source verses folded onto a chapter's last verse, or dropped as additions (Esther 10:4-13 in the Vulgate) |
| alignment | Against the reference KJV: per-chapter correlation of verse lengths (characters), chapters that fit better shifted by one verse, overlong verses (folded additions), whether the text reads better as Hebrew (MT) numbering than as declared, and, when both carry Strong's numbers, verses whose Strong's numbers match a neighbouring verse better than their own |
| formatting | `formatting` JSON shape, span types and ranges, headings, paragraphs, poetry, interlinear rows; markup present in the source that vanished (headings, Strong's) |
| hygiene | HTML/XML tags, entities, pilcrows, backslashes, USFM leftovers, Strong's leftovers, control characters, U+FFFD, mojibake, odd spaces, zero-width characters, in text and headings. Each is split into "introduced by conversion" and "inherited from the source" |
| metadata | `content_sha256` recomputed, licence present, licence text that contradicts `license_spdx`, RTF/HTML in `module_info`, language and text direction against the catalog |
| stats | Verse, word and character counts, shortest verses, most frequent tokens, dominant script against the language |
| validator | `scripts/modules/validate-module.js` result |
| samples | Key verses (Gen 1:1, Ps 23:1, John 3:16, Rev 12:17 / 13:1, 3 John 14, ...), seeded random verses and the worst cases of each check, SWORD text next to DB text |

Thresholds live in `THRESH` in `swordcheck.py`.

Non-bible modules (commentary, dictionary, book, devotional) get lighter checks:
row counts against source entries, word totals, hygiene, samples and the validator.

## Versification

The converter stores only KJV-English verse ids. It translates:

- NRSV, Vulg and Synodal with libsword's own maps.
- MT with `data/versification-mt-to-kjv.json`.
- German with `data/versification-german-to-kjv.json`, which extends the MT map.
- NRSVA, SynodalProt, Leningrad and Luther through aliases, because they are
  identical to NRSV, Synodal, MT and German in all 66 canonical books (checked
  chapter by chapter against libsword's tables).

LXX, Catholic, Catholic2, Calvin, Segond, DarbyFr, Orthodox and SynodalP are
still refused (`NOT CONVERTED`).

`data/versification-overrides.json` lists modules whose text is numbered
differently from what their .conf declares. `batch` passes each active entry to
the converter as `--versification`. Entries need evidence; the file explains the rules.
