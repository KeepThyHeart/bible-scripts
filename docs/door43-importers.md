# Door43 importers (design)

Two Python importers under `tools/import/door43/`. **A (helps):** Door43 translationWords (tW), translationNotes
(tN), translationQuestions (tQ) and Open Bible Stories (OBS) become dictionary, commentary, quiz and book modules.
**B (aligned Bibles):** word-aligned USFM 3 becomes Bible modules with `interlinear_word` rows in the form
`sword2bible` writes.

Every design decision below is based on files sampled on 2026-11-10: es-419_tw v37, ar_obs v1, BSOJ ar_tn/ar_tq
3JN, hi_tn 3JN, pt-br_tn and pt-br_tq 3jn, and the aligned USFM of BSOJ/ar_avd (3JN, OBA, JOL) and MVHS/fr_lsg (3JN).

## 1. Language, layout, tests

The importers use Python 3 (3.12 here), the standard library (`sqlite3`, `zipfile`, `csv`, `unicodedata`) and
`unittest`. Manifests are YAML. Use PyYAML when it can be imported (6.0.1 is installed system-wide). Otherwise fall
back to `manifest_lite.py`, which reads only the keys listed in section 6. Node is ruled out because the repo has no
`node_modules` (even `validate-module.js` needs `npm ci` before it can run). C++ is ruled out because there is no
SWORD dependency here.

```
tools/import/door43/
  door43.py                 CLI: fetch | build | compare | report   (thin; logic in door43lib)
  door43lib/  __init__.py fetch.py manifest_lite.py books.py refs.py versification.py strongs.py
              arabic_norm.py markdown_html.py links.py schema.py identity.py
              tw.py tn.py tq.py obs.py usfm_aligned.py bible_build.py compare.py
  data/sources.json                   pinned sources (sec. 2)
  data/versification-ufw-to-kjv.json  ufw -> KJV rules (sec. 5)
  data/obs-references.json            OBS story -> KJV ranges (sec. 3.4)
  tests/__init__.py  tests/test_*.py  tests/fixtures/  (hand-written synthetic fixtures; no Door43 text copied)
  README.md
```

The schema comes from the app, never from a copy. `schema.py` expands `-- @include` lines from
`$BIBLE_REPO/packages/core/sql/schemas/initial/<Type>.sql` (default `../bible`). Tests that need the schema call
`skipUnless` when it is missing.

`package.json` gets:
`"test": "node scripts/query/db-cli.test.js && npm run test:py"` and
`"test:py": "python3 -m unittest discover -s tools/import/door43 -t tools/import/door43 -p 'test_*.py' && python3 -m unittest discover -s tools/import/sword/bible -p 'test_*.py'"`.
The second discover also brings the existing overrides test into `npm test`.

## 2. Sources and fetching

`data/sources.json` holds one row per source: `{id, kind: tw|tn|tq|obs|bible, owner, repo, tag, lang, door43_lang, sha256}`.
`fetch` downloads `https://git.door43.org/<owner>/<repo>/archive/<tag>.zip` (it is the release `zipball_url`) and streams
it to `<work>/cache/<owner>__<repo>__<tag>.zip`. The work dir is `--work-dir`, or `$DOOR43_WORK`. The first fetch
writes the sha256 into sources.json; later fetches enforce it. Members are read straight from the zip with `zipfile`
and never extracted in bulk. Any path containing `..` or starting with `/` is rejected. There are no git clones and no
`raw/branch/master` URLs.

| lang | tW | tN | tQ | OBS |
|---|---|---|---|---|
| es (es-419) | es-419_gl/es-419_tw v37 | es-419_gl/es-419_tn v66 (Rut Jon Tit 3Jn) | - | - |
| fr | MVHS/fr_tw v0.0.1 (1006 articles; uW fr_tw v7.3 has 981) | Xenizo/fr_tn v2.0 | - | - |
| hi | translationCore-Create-BCS/hi_tw v8 | .../hi_tn v9 | .../hi_tq v8 | Door43-Catalog/hi_obs v8.3 |
| bn | translationCore-Create-BCS/bn_tw v4 | .../bn_tn v6 | .../bn_tq v2 | Door43-Catalog/bn_obs v4.4 |
| ar | BSOJ/ar_tw v5 | BSOJ/ar_tn v6.1 | BSOJ/ar_tq v3.1 | OBS-Aquifer/ar_obs v1 |
| pt (pt-br) | Door43-Catalog/pt-br_tw v6.4 | Door43-Catalog/pt-br_tn v7.1 (**legacy Markdown**) | .../pt-br_tq v9.2 (**legacy Markdown**) | .../pt-br_obs v3.2.3 |
| zh | none released (RPP/zh_tw has no release; only user forks such as Gracefang/zh_tn) -> **skip** | - | - | (Door43-Catalog/zh_obs v8.3 exists; out of scope) |

Bibles for importer B: BSOJ/ar_avd v6.9, BSOJ/ar_nav v2, Door43-Catalog/hi_irv v12, Door43-Catalog/bn_irv v5,
MVHS/fr_lsg v0.0.2 (NT only).

## 3. Importer A: mapping

All prose goes through `markdown_html.py` into a small HTML subset: `#`..`####` -> `h3`..`h5`, `p`, `ul`/`ol`/`li`,
`strong`, `em`, `blockquote`, `hr`, `a`, `br`; raw HTML is escaped; TSV `\n` escapes become newlines. Every emitted
tag must be on the allow-list in `DictionaryDefinitionFormatter.ts` so definitions render as HTML.
`module_info.metadata.content_format = "html"`.

### 3.1 tW -> dictionary (`dictionary_type = 'bible_dictionary'`)

The source layout is `bible/{kt,names,other}/<slug>.md`: es has 212/347/496 articles, fr 211/329/466. Each article
starts with a `# Title` line followed by `##` sections. Section names are localized and their spelling varies
("Datos de esta palabra", "Datos de la Palabra:"), so **nothing may key on section titles**.

| column | value |
|---|---|
| `entry_key` | `kt/god`: the category plus file slug, unique and stable |
| `word` | text of the `# ` line |
| `definition` | the body as HTML (without the title) |
| `related_words` | JSON list of entry_keys found in `../kt/x.md` links |
| `metadata` | `{"category":"kt","strongs":["H0430","G2316",...]}` |

**Strong's index:** collect `\b[GH]\d{1,5}[a-z]?\b` tokens anywhere in the article (in practice the last section,
"Números de Strong: H136, ... G5538"), normalize them (5.3) into `metadata.strongs`, and write one `module_feature`
row `strongs_index` whose metadata is `{"G2316":["kt/god"],...}`: a single-row Strong's -> articles lookup.
**`word_occurrence` is not used**; the schema reserves it for an exhaustive per-translation concordance, which tW is
not. `rc://*/tn/help/<bk>/<cc>/<vv>` links (e.g. "Referencias bíblicas") become `verse_link` rows
(`source_type='dictionary_entry'`, `link_type='reference'`). `**10:2**` OBS examples stay plain text;
`bible/config.yaml` is ignored (tWL word links are a later stretch). `language_from` NULL, `language_to` = module
language.

### 3.2 tN -> commentary

**7-column TSV** (ar, hi; expected for bn, es, fr): `Reference ID Tags SupportReference Quote Occurrence Note`.

The legacy **9-column** TSV (`Book Chapter Verse ID SupportReference OrigQuote Occurrence GLQuote OccurrenceNote`)
is recognised by its header. Split on tabs; a cell wrapped in `"..."` with `""` inside (hi_tn) is unquoted. Rows
with a malformed Reference (hi_tn has `:`) or an empty Note are skipped and counted. **One commentary entry per
distinct Reference**, rows in file order, each as `<h4><span lang="grc|hbo">Quote</span></h4>` + Note HTML (no
heading without a Quote); `metadata.notes = [{id, tags, support_reference, quote, occurrence}]`.

| Reference | entry_level | verse_id_start/end | extra |
|---|---|---|---|
| `front:intro` | book | NULL/NULL | `metadata.book`, plus a `verse_link` covering the whole book (`link_type='scope'`) |
| `1:intro` | chapter | NULL/NULL | `metadata.book/chapter`, plus a scope `verse_link` over the chapter |
| `1:5` | verse | v/v | |
| `1:3-5`, `1:20-2:3` | passage | first/last | |
| `1:3,5` | passage | min/max | one `verse_link` per piece |

Every reference goes through ufw->KJV (sec. 5).

**Legacy pt-br_tn** has the layout `<bk>/<cc>/<vv>.md`, one file per chunk. Each file is a series of `# quote`
blocks, each followed by a note. A chunk covers from its own first verse to one verse before the next chunk starts,
or to the end of the chapter, which makes it a passage entry. A file that is not a verse number (`intro.md`) gives a
chapter- or book-level entry. Old plain tA references such as `(veja: figs_you)`, with the "see" word taken from a
small per-language table, are removed.

### 3.3 tQ -> quiz (Quiz.sql)

This is a better fit than commentary: Quiz.sql has `kind`, `answer_mode` and `data_source` for attribution. The TSV
columns are `Reference ID Tags Quote Occurrence Question Response`.

Each row: `question_key` `3JN-r3ao` (`<BOOK>-<ID>`), `kind` `comprehension`, `answer_mode` `free_response`,
`prompt` = Question, `answer` = Response, `sort_order` = file order, `source_id` `door43-<repo>`, `review_status`
`unreviewed`, plus a `verse_link` with `link_type='primary_passage'` (mapped ufw->KJV). Legacy pt-br_tq uses
`<bk>/<cc>/<vv>.md` files of `# question` + answer pairs; key `3JN-01-01-<n>`.

### 3.4 OBS -> book

The layout is `content/NN.md`, one file per story, 50 stories. A story file holds `# N. Title`, then pairs of
`![OBS Image](https://cdn.door43.org/obs/jpg/360px/obs-en-SS-FF.jpg)` and a paragraph, and ends with a line
`_<reference>_`.

One section per story: `section_number`/`sort_order` = story number, `title` = the `#` line, `content` =
`<p data-frame="FF">…</p>` per frame plus the reference line in `<p class="obs-reference"><em>`. Images are dropped
(the app works offline); frame ids go in `metadata.frames`. `front/` and `back/`, when present, become sections 0
and 51. Verse links come from `data/obs-references.json`: 50 stories mapped to KJV ranges, built once from the English
  OBS footers. Localized footers are not parsed, because that would need a book-name table for every language.

## 4. Link resolution (`links.py`)

Links are resolved **after** all modules of one language are parsed. The resolver can then see which tW keys,
verses and OBS stories actually exist.

| source form | becomes |
|---|---|
| `rc://*/tn/help/1jn/01/05`, `rc://xx/...` (any case; `rc: //` is repaired) | `<a href="#verse-62001005" class="scripture-link">label</a>`, plus a `verse_link` |
| `rc://*/tn/help/exo/12` (chapter only) | link to v1, `verse_link` over the whole chapter |
| tN relative `./05.md`, `../01/05.md`, `../../1jn/01/05.md` | resolved against the current book and chapter, then as the row above |
| `../kt/god.md`, `./god.md`, `rc://*/tw/dict/bible/kt/god` | `<a href="#entry-kt/god" class="dictionary-link" data-module="<tW uuid>">label</a>` |
| `[[rc://*/tw/dict/bible/kt/god]]` (bare) | same, with the label set to the target article's `word` |
| `rc://*/obs/...`, `rc://*/help/obs/48/06` | `<a href="#section-48" class="book-link" data-module="<OBS uuid>">` |
| `rc://*/ta/...` (translationAcademy is not imported) | the link is removed and its label kept. A bare `[[...]]` is removed, and so is an enclosing `(See: …)` left empty |
| `http(s)://` | kept as is |
| `about:blank`, an unknown scheme, a target that is missing (an entry not in the module, a verse with no KJV home), or a module that was not built | **plain label text** (never a dangling href). It is counted and sampled (20 max) in the build report |

**Open (app side):** `CommentaryLinkProcessor` strips every `<a>` and re-links by English book-name patterns. Door43
modules need a flag such as `metadata.links = "prebuilt"` that keeps `scripture-link`/`dictionary-link` anchors, and
`#entry-…` needs a handler. Until then, `verse_link` rows still carry every scripture reference.

## 5. Versification, Strong's, Arabic

### 5.1 ufw to KJV

The Door43 manifests say `versification: ufw`. The samples show ufw to be **English-shaped in the OT**: Joel has
3 chapters of 20/32/21 verses, unlike MT's 4. In the NT it follows the critical text: 3JN has 15 verses where KJV
has 14. `data/versification-ufw-to-kjv.json` uses the rule format of `versification-mt-to-kjv.json`
(`{b,c,from,to,dc,off}`) plus `merges`, which mirror `VARIANT_VERSE_MAP` in sword2bible.cpp:

- 3JN 1:15 is appended to 1:14.
- REV 12:18 is prepended to 13:1.

The rules are derived **on evidence** by `derive_versification` (subtask D3). It lists every (book, chapter) whose
verse count differs from `scripts/data/kjv-versification.json` across all five Bibles. Each deviation is resolved by
text overlap with the matching task-0220 module; for example, 0220 AVD 64001014 already holds 1:14 plus 1:15 with
`formatting.source_verses`. Missing verses, such as a KJV verse the source folds into its neighbour, stay holes and
are reported. A source verse with no rule and no KJV address makes the build fail. A manifest that declares anything
other than `ufw` must first pass a chapter-shape check against KJV. The tN, tQ and tW references use the same map.

### 5.2 Verse ids

A verse id is `book*1000000 + chapter*1000 + verse`. `books.py` maps the USFM codes (`GEN`..`REV`), lowercase rc codes
and file prefixes (`65-3JN`) to book numbers 1..66.

### 5.3 Strong's (`strongs.py`), matching the forms SWORD modules use

The dominant SWORD form is a letter plus 4 zero-padded digits (`G0027`, `H0430`).

| raw | normalized | metadata |
|---|---|---|
| `G00270` (UGNT, 5 digits) | `G0027` | `x_strong` when the 5th digit is not 0 |
| `G4245` (fr_lsg) | `G4245` | |
| `H5921a` | `H5921` | `x_strong` |
| `b:H3117`, `c:H0408` | `H3117` | `prefix: "b"` |
| `l` (prefix only) | none, so no row | |

### 5.4 Arabic normalisation (`arabic_norm.py`)

This applies only to **ar helps** (tW, tN, tQ, OBS). ar_tn 3JN mixes Persian letters (324×U+06CC, 107×U+06BE) with
Arabic ones (302×U+064A, 59×U+0649). The Bible USFM, ar_tq 3JN and ar_obs had no Persian letters.

U+06BE -> U+0647, U+06A9 -> U+0643, Persian digits U+06F0..9 -> U+0660..9, and U+06CC -> U+064A. The exception is word-final U+06CC: when the diacritic-stripped AVD word list (from the 0220
  module) knows the U+0649 spelling and not the U+064A one, it becomes U+0649.
Replacement counts go in `metadata.modifications`.

Importer B only asserts that Persian letters are absent.

## 6. module_info (both importers)

| field | value |
|---|---|
| module_uuid | `deterministic_uuid("<module_type>:<abbr lower>:<lang>")`, the same rule as `apply-module-overrides.py` and `sword_common.cpp` (import it, don't copy it) |
| abbreviation / file | `TW-ES`, `TN-AR`, `TQ-HI`, `OBS-BN`; files `dictionary_TW-ES.db`, `commentary_TN-AR.db`, `quiz_TQ-HI.db`, `book_OBS-BN.db` |
| full_name | manifest `dublin_core.title`, with the English resource name in parentheses: "Palabras de Traducción (translationWords)" |
| language_code | ISO 639-1 (`es`, `pt`); the region code goes in `metadata.door43_language` (`es-419`, `pt-br`). `right_to_left` comes from `language.direction` |
| author / publisher / year_published | `creator` / `publisher` / the year of `issued` |
| content_version | release tag (`v37`) |
| license_spdx / license_url | `CC-BY-SA-4.0` / `https://creativecommons.org/licenses/by-sa/4.0/` |
| copyright | the `Copyright ©` line of LICENSE.md, e.g. "Copyright © 2022 by Fundación Idiomas Puentes" |
| source_url | `https://git.door43.org/<owner>/<repo>/releases/tag/<tag>` |
| versification | `kjv-english` (after mapping) |
| metadata | `derivative: true`; `attribution` (copyright line, plus "Original work available at unfoldingword.org…", the licence's own wording); `modifications` ("Converted from Door43 <repo> <tag> to the Bible app module format: Markdown to HTML, links resolved, verse numbering mapped to KJV, images and translationAcademy links removed [, Persian letters normalised]"); `notices` ("does not imply endorsement"); `door43 {owner, repo, tag, zip_sha256, manifest_version, sources:[...]}` |

Curated corrections go in the existing `module-overrides.json`, under keys `door43:<owner>/<repo>`.
`apply-module-overrides.py --module door43:...` gains two fixes:
its uuid recompute takes `module_type` from the row (today it hard-codes `bible:`), and `forbid_text` checks the
content table of each module type.

## 7. Importer B: aligned USFM -> Bible

**Parse** (`usfm_aligned.py`). For each book, use its `\id` code and ignore the file name: ar_avd ships
`13-1CH`, `13-2CH` and `14-2CH`. Identical duplicates are dropped; differing ones are an error.

- The verse text is the concatenation of plain text and `\w word|…\w*` contents in source order. The `\zaln-s/e`
  milestones and the `\v` and `\c` markers are removed.
- Words are split on whitespace and joined with single spaces, exactly as sword2bible's `flushWord` does. That means
  `L’` + `\w ancien\w*` is one word, and so is `اَلشَّيْخُ،`.
- Each `\w` maps to the index of the word that contains it.
- `\f…\f*` and `\x…\x*` are dropped and counted.
- `\s`, `\d` and `\ms` become `formatting.block.heading`, and `\p`/`\q` become the paragraph and poetry keys that
  sword2bible writes. Copy the exact JSON keys from it.
- `\wj` becomes its red-letter span.

**Rows.** Each `\zaln-s` (attributes `x-strong x-lemma x-morph x-occurrence x-occurrences x-content`) gives one
`interlinear_word` row over the words of the `\w` tokens it encloses. Nested `zaln-s` give one row each, over the same
span. Two groups with the same (x-content, x-occurrence) in one verse become one row; the later spans go in
`extra_word_positions` (`"12-14,18"`).

| column | value (sword2bible parity) |
|---|---|
| strongs_number | normalized (5.3). Rows without one are skipped, as sword2bible does |
| lemma | `strong:<strongs_number>` |
| original_word | `x-content` |
| morphology | Hebrew `He,Ncmsc` becomes `HNcmsc` (OSHM style: `He,`->`H`, `Ar,`->`A`, `:`->`/`). Greek is stored verbatim (`Gr,N,,,,,DMS,`). Robinson conversion is out of scope |
| gloss | the covered target words, space-joined |
| metadata | `{"lemma": x-lemma, "x_strong"?, "prefix"?}` |

- `module_feature` declares `strongs_numbers`, `interlinear`, and `morphology` with metadata
  `{"schemes":{"hbo":"oshm","grc":"uw-greek"}}`.
- Verses are mapped ufw->KJV. Merged verses concatenate their text, shift the second part's word offsets, and record
  `formatting.source_verses` `[{chapter, verse, start, end}]`. This mirrors `mergeVerseResults`.

**Identity.** These texts are the 0220 modules, enriched. ar_avd 3JN 1:1 is byte-identical to 0220 AVD 64001001. So
the output takes **the matching 0220 module's abbreviation and uuid** as its next content revision:

ar_avd -> AVD (`arbVDeb`), ar_nav -> NAV (`arbnav2012eb`; its Biblica override entry applies unchanged),
hi_irv -> IRV-HI, bn_irv -> IRV-BN. The identity is reused only when the comparison passes; otherwise the build
takes the suffix `-ALN` and reports why. `content_version` is `door43-<tag>`.

- Licence follows the CUV precedent: `license_spdx` is `CC-BY-SA-4.0` for the combined file, and `copyright`
  states "Text: <text licence>. Word alignment and Strong's: © BSOJ / BCS, CC BY-SA 4.0".
- fr_lsg has no 0220 counterpart, so it becomes a new module `LSG1910-NT` (uuid `bible:lsg1910-nt:fr`). Only its
  verse counts are compared, against KJV.

**Compare and coverage** (`compare.py new.db ref.db` writes `<work>/reports/<abbr>-compare.json` plus a text
summary). Per book: verses new / ref / both / only-new / only-ref; text equal after NFC and whitespace collapse (first 3
diffs); `formatting.source_verses` equality; `interlinear_word` rows; **alignment coverage** (target words covered
by a Strong's row / all words); verses with zero alignment.

**Pass** requires identical verse sets and at least 99.5 % text equal. hi_irv (partial NT) and bn_irv (NT) are
expected to show low or zero coverage in some books. That is reported, not failed.

## 8. Build reports and validation

Every build writes `<work>/reports/<abbr>.json`: counts (entries, notes, questions, sections, verse_links), skipped
rows by reason, resolved/unresolved links by kind with samples, Persian replacements, Strong's index size, missing
books.

The final step runs the in-process structural check (`check_module.py`: single module_info row, identity columns
set, content table non-empty, all verse ids inside KJV, no dangling `dictionary-link`). It then runs
`node scripts/modules/validate-module.js <db>` when Node `sqlite3` is installed (`npm ci`).
`validate-module.js` does not know `quiz` yet; subtask V2 adds it.

## 9. Subtasks

The steps run in order of their ids, except that D1–D3 can run in parallel. Each step adds its tests to `tests/`.

| id | file(s) | responsibility | inputs -> outputs | tests |
|---|---|---|---|---|
| D1 | `door43lib/books.py`, `refs.py` | USFM/rc book codes to book numbers; parse `front:intro`, `1:intro`, `1:5`, `1:3-5`, `1:20-2:3`, `1:3,5`, rc tn paths, relative tN paths | strings -> `Ref(book, ch, v1, ch2, v2, level)` | table tests for every form, including malformed `:` and `rc: //` |
| D2 | `strongs.py`, `arabic_norm.py` | sec. 5.3 and 5.4 | raw string -> normalized + metadata; text + word list -> text + counts | every row of the 5.3 table; final-ي/ى choice; digits |
| D3 | `versification.py`, `data/versification-ufw-to-kjv.json`, `derive_versification.py` | apply rules and merges; derive and verify rules against the 0220 DBs | (b,c,v) -> KJV verse id, or a merge target | 3JN 15->14 merge, REV 12:18->13:1, identity elsewhere, an unmapped verse raises |
| D4 | `fetch.py`, `manifest_lite.py`, `data/sources.json` | pinned zip download, sha256 pin, safe member access, manifest read (PyYAML or fallback) | source id -> cached zip + manifest dict | zip-slip rejection, sha mismatch, both manifest readers on a fixture |
| D5 | `schema.py`, `identity.py` | `@include` expansion, create an empty DB of a type; module_info and metadata from manifest and LICENSE | type, manifest -> sqlite DB with module_info | uuid parity with `apply-module-overrides.deterministic_uuid`; skips without the schema dir |
| D6 | `markdown_html.py`, `links.py` | Markdown to HTML subset; resolver per sec. 4 with a registry of known keys/verses/stories | md + context -> html + verse_links + report | each row of the sec. 4 table, unresolved -> plain text, escaping |
| A1 | `tw.py` | sec. 3.1, including the `strongs_index` feature | tW zip -> `dictionary_TW-xx.db` | fixture of 3 articles: keys, Strong's, related_words, verse_links |
| A2 | `tn.py` | sec. 3.2: 7-column, 9-column and legacy Markdown | tN zip -> `commentary_TN-xx.db` | grouping per reference, intro levels and scope links, quoted cells, legacy chunk ranges |
| A3 | `tq.py` | sec. 3.3: TSV and legacy Markdown | tQ zip -> `quiz_TQ-xx.db` | keys, primary_passage links, data_source row |
| A4 | `obs.py`, `data/obs-references.json` | sec. 3.4 | OBS zip -> `book_OBS-xx.db` | frames, image drop, front/back, verse_links |
| B1 | `usfm_aligned.py` | sec. 7 parse: words, \w-to-word index, zaln rows, extra spans, notes and headings | USFM text -> verses + interlinear records | fixture with nested zaln, `L’`+\w, discontiguous group, a prefix-only strong, \f |
| B2 | `bible_build.py` | KJV mapping and merges, `formatting.source_verses`, features, identity reuse from the 0220 DB | Bible zip (+ ref DB) -> `bible_<ABBR>.db` | merged 3JN 1:14 offsets match the 0220 shape; dedupe of duplicate `\id` |
| B3 | `compare.py` | sec. 7 compare and coverage report | new.db, ref.db -> JSON + text | two tiny DBs: equal, a diff, a missing verse, coverage math |
| C1 | `door43.py`, `check_module.py`, `README.md` | CLI `fetch/build/compare/report --work-dir`, build reports, structural check | sources.json ids -> DBs + reports | CLI smoke test on fixtures |
| V1 | `tools/import/sword/bible/apply-module-overrides.py`, `module-overrides.json` | uuid from `module_type`, `forbid_text` per type, `door43:` keys | db + key -> db | extend `test_apply_module_overrides.py` |
| V2 | `scripts/modules/validate-module.js`, `package.json` | recognise `quiz`; add `test:py` to `npm test` | - | `npm test` runs every Python suite |
| R1 | (run) | fetch and build every row of sec. 2; compare each B output with 0220; hand the reports to the human | work dir | reports reviewed; no unresolved-link class above 1 % without a note |
