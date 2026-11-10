# Door43 importers

Converts Door43 translationWords / translationNotes / translationQuestions / Open Bible Stories into
dictionary, commentary, quiz and book modules, and word-aligned USFM into Bible modules.
Design: `docs/door43-importers.md` (sections 2 fetch, 4 links, 6 module_info, 7 Bibles, 8 reports).
Python 3 stdlib only (PyYAML optional). The module schema is read from `$BIBLE_REPO`
(default `../bible`).

## Usage

```
W=/path/to/work
python3 tools/import/door43/door43.py fetch   all  --work-dir $W      # pinned zips -> $W/cache, sha256 pinned in sources.json
python3 tools/import/door43/door43.py build   ar   --work-dir $W --ref-dir /path/to/0220/db
python3 tools/import/door43/door43.py compare all  --work-dir $W --ref-dir /path/to/0220/db
python3 tools/import/door43/door43.py report       --work-dir $W
```

`<source>` is a `data/sources.json` id (`tw-es`, `bible-ar-avd`), a language (`ar`) or `all`.
`$DOOR43_WORK` replaces `--work-dir`; `--sources FILE` uses another sources file;
`fetch --local-zip ID=PATH` seeds the cache from a local zip.

## Layout of the work dir

```
cache/<owner>__<repo>__<tag>.zip   downloaded releases
out/<lang>/*.db                    built modules
reports/<abbr>.json                per-module build report (counts, skipped rows, link stats, check, validator)
reports/<abbr>-compare.{json,txt}  Bible vs 0220 module (text equality strict and ignoring whitespace, coverage)
reports/SUMMARY.md                 aggregate from `report`
```

Per language the build order is tW, OBS, then tN and tQ with a registry of the built tW keys and OBS
stories (so their links resolve); Bibles take `ref_db_file` from `sources.json` (a file name inside
`--ref-dir`). Every output gets the structural check (`door43lib/check_module.py`) and, when Node can
load `sqlite3`, `scripts/modules/validate-module.js`; otherwise the report says `validator not run`.
A Bible passes the compare when the verse sets are identical and at least 99.5 % of verses are equal
ignoring all whitespace (`text_equal_nospace_pct`).

## Tests

```
python3 -m unittest discover -s tools/import/door43 -t tools/import/door43 -p 'test_*.py'
```
