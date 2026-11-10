# Open non-English Bibles converted with sword2bible

Task 0220 (step 3 of the 0217 language-study plan). Eighteen openly licensed Bibles in
seven languages were converted with `tools/import/sword/bible/sword2bible`, corrected with
`apply-module-overrides.py` (licence, attribution, trademark, identity), and verified. One
module (`fraLSG1910eb`) was skipped and one (`SpaRV`) was superseded; see below.

All output is `bible_<SwordModule>.db`, kept in the task work dir. Nothing was published.

## Reproduce

```bash
cd tools/import/sword
make -C common && make -C bible && make -C probe        # converter + libsword probe
# download: eBible  https://ebible.org/sword/zip/<id>.zip
#           CrossWire https://crosswire.org/ftpmirror/pub/sword/packages/rawzip/<Id>.zip
#           Xiphos    http://ftp.xiphos.org/pub/xiphos/  (mods.d/ + modules/ files, zipped by hand)
bible/sword2bible -i <Module>.zip -o bible_<Module>.db
bible/apply-module-overrides.py bible_<Module>.db --module <Module>   # data/module-overrides.json
node ../../../scripts/modules/validate-module.js bible_<Module>.db
python3 verify/swordcheck.py verify --zip <Module>.zip --db bible_<Module>.db \
        --log <convert.log> --reference bible_KJV.db --out <report dir>
```

`data/module-overrides.json` holds, per module, everything the `.conf` gets wrong or leaves out
(see "Licence and attribution rules" below).

## Modules

Versification: every module here is read through libsword's own map to KJV English (`NRSV`,
`German`) or is already KJV. No new map was written and the converter refused none. `Verses`
is the number of canonical verses stored; Strong's is the number of `interlinear_word` rows.
Validator = `validate-module.js`; Verify = `swordcheck.py` (PASS/WARN, no FAIL except the skip).

| Lang | Module (abbr.) | Source | Licence (`license_spdx`) | Declared v11n | Verses | Strong's | Validator / Verify |
|---|---|---|---|---|---|---|---|
| es | `spaRV1909eb` (RV1909) **chosen** | eBible | PD | KJV | 31,084 | 390,758 | pass / WARN |
| es | `SpaRV1865` (RV1865) | CrossWire | CC0-1.0 (text of 1865 is PD; the 2018 digital text is CC0) | KJV | 31,098 | 0 | pass / WARN |
| fr | `FreJND` (FreJND), Darby | CrossWire | PD | German | 31,093 | 421,904 | pass / WARN |
| fr | `fraFOB1744eb` (FOB), Ostervald | eBible | PD | NRSV | 31,100 | 691,588 | pass / WARN |
| zh | `ChiUn` (CUV-T), CUV Traditional | CrossWire | GFDL-1.1-or-later (text PD, tags FDL) | NRSV | 31,101 | 389,008 | pass / WARN |
| zh | `ChiUns` (CUV-S), CUV Simplified | CrossWire | GFDL-1.1-or-later | NRSV | 31,101 | 389,008 | pass / WARN |
| zh | `cmncbt2023eb` (OCCB-T) | eBible | CC-BY-SA-4.0 | NRSV | 30,959 | 0 | pass / WARN |
| zh | `cmncbs2022eb` (OCCB-S) | eBible | CC-BY-SA-4.0 | NRSV | 30,959 | 0 | pass / WARN |
| ar | `arbVDeb` (AVD), Smith-Van Dyck, RTL | eBible | PD | NRSV | 31,102 | 0 | pass / PASS |
| ar | `arbnav2012eb` (NAV), RTL | eBible | CC-BY-SA-4.0 | NRSV | 31,101 | 0 | pass / WARN |
| hi | `hin2017eb` (IRV-HI) | eBible | CC-BY-SA-4.0 | NRSV | 31,102 | 0 | pass / WARN |
| hi | `hincv2019eb` (OHCV) | eBible | CC-BY-SA-4.0 | NRSV | 31,086 | 0 | pass / WARN |
| bn | `benirv2019eb` (IRV-BN) | eBible | CC-BY-SA-4.0 | NRSV | 31,094 | 0 | pass / WARN |
| bn | `benobcv2019eb` (OBCV) | eBible | CC-BY-SA-4.0 | NRSV | 31,099 | 0 | pass / PASS |
| pt | `PorAlmeida1911` (ALMEIDA1911) | CrossWire | PD | KJV | 31,101 | 0 | pass / WARN |
| pt | `porbr2018eb` (BLIVRE), Bíblia Livre | eBible | CC-BY-4.0 | KJV | 31,102 | 0 | pass / WARN |
| pt | `PorRecebida` (AR), Almeida Recebida | Xiphos | PD | KJV | 31,102 | 0 | pass / PASS |
| pt | `PorNVA` (NVA) | CrossWire | CC-BY-SA-4.0 | NRSV | 31,090 | 0 | pass / PASS |

All 66 books are present in every module, NFC holds everywhere (0 violations in text and
headings), no replacement characters, no raw markup, and Gen 1:1, Ps 23:1, John 3:16 and Rev 22:21
were read in each. The Arabic modules set `right_to_left = 1`; the others 0. `language_code` is the
bare ISO code (`zh` for both scripts; the `zh-Hans`/`zh-Hant` tag stays in `metadata.sword_lang`).

### Attribution carried in each module (`metadata.attribution`, plus `copyright`, `notices`)

- **Biblica CC BY-SA** (`cmncbt2023eb`, `cmncbs2022eb`, `arbnav2012eb`, `hincv2019eb`, `benobcv2019eb`):
  "The original work by Biblica, Inc. is available for free at www.biblica.com and open.bible."
  The ® and ™ marks are gone from the names, descriptions and metadata (the override entry has
  `forbid_text`, so the tool refuses the module if one survives anywhere, verse text included).
  The changes are stated in `metadata.modifications` and the CC BY-SA notice is in `metadata.notices`.
  Copyright holder "Biblica, Inc." stays: the licence requires it.
- **CUV** (`ChiUn`, `ChiUns`): the CUV text is PD; the Strong's tagging is © 信望愛 (FHL, bible.fhl.net)
  under the GNU FDL 1.1 or later. `license_spdx` is `GFDL-1.1-or-later` (so `isPublicDomain()` is
  false, correctly), `license_url` is the FDL 1.1 text, `metadata.component_licenses` is
  `{text: PD, strongs_tags: GFDL-1.1-or-later}`, and the FDL notice and "please do not remove" attribution are in
  `metadata.notices` / `attribution`. An app that ships these must also ship the FDL text.
- **Bíblia Livre**: `Bíblia Livre, fevereiro de 2018 (eBible build 2022-03-08), Diego Santos, Mario Sérgio, Marco Teles`,
  CC BY. The eBible `.conf` says "Free non-commercial distribution", which contradicts the work's copyright
  page (CC BY); `license_spdx` is `CC-BY-4.0` and the contradiction is recorded in `metadata.conf_licence_claim`.
- **IRV Hindi/Bengali**: © Bridge Connectivity Solutions Pvt. Ltd., CC BY-SA 4.0.
- **NVA**: based on the Unlocked Literal Bible, CC BY-SA 4.0. **RV1865**: CC0 per its GitHub README.
- **Almeida 1911**: the CrossWire `.conf` says `GPL` (for its digital markup); the 1911 text is PD. Stored as `PD`,
  the `.conf` claim is in `metadata.conf_licence_claim`. The human should confirm that reading.
- Two abbreviations clashed (`OCCB` for both scripts), which would have given both the same
  `module_uuid`; they are now `OCCB-T` and `OCCB-S` and the UUID is recomputed from the abbreviation.

## Choices and skips

- **Spanish: `spaRV1909eb` over `SpaRV`.** Same text, but CrossWire's `SpaRV` carries 3 Strong's rows
  although its `.conf` advertises them; eBible's has 390,758, plus a heading and slightly richer formatting.
  `SpaRV` (db kept in the work dir under `superseded/`) is not for publishing. Both share 18 holes: the source
  breaks 18 chapter endings the Hebrew way, so KJV Num 12:16, 29:40, 1 Sam 23:29 and similar verses have no row.
- **French: `fraLSG1910eb` SKIPPED.** The `.conf` says NRSV, the converter accepts it, but the text is a Segond hybrid:
  it follows KJV verse numbers in most books (Gen 32, Ex 8, Lev 6, Num 30, 1 Sam 24 are KJV-numbered)
  but counts psalm titles as verses (Ps 51:1-2 is the title, 51:3 the first verse), so 21 or more psalm chapters
  sit one or two verses off KJV. `--versification MT` fixes the psalms but breaks the KJV-numbered chapters
  (190 holes). Neither is acceptable, and a Segond to KJV map is new versification work, which is out of scope.
  Its Strong's tags are also shifted in 741 verses. The conversion was made for evidence and left in `skipped/`.
  French is served by `FreJND` and `fraFOB1744eb`.
- **Chinese:** eBible's 新標點和合本 skipped as instructed (disputed copyright).

## Converter fixes (committed)

1. **Poetry and Selah did not match the module format.** The converter wrote `block.poetry_level` and
   `block.selah`; the schema, `VerseFormatting.ts` and `validate-module.js` want `block.lines`
   (`[{start,end,level}]`, covering every word) and a `musical_direction` span. Every module with poetry failed
   validation (`unknown_block_key`): IRV, OCCB, OBCV, HCV, NVA, Darby. Fixed in `common/sword_common.cpp`;
   KJV output is byte-identical, ASV differs only in that encoding (same verses, same maximum level, every Selah kept).
2. **`<WAHb>` leaked into text** (CUV Joshua 24:14: escaped text in the source). Tag-shaped debris after entity
   decoding is now removed.
3. `swordcheck.py` knows `lines` and `musical_direction`. New tests: `make -C common test`, and
   `python3 -m unittest test_apply_module_overrides` in `bible/`. `npm test` still passes (16/16).

## Known issues in the data (upstream, not fixed here)

- **CUV Simplified Strong's positions.** The format addresses words by whitespace, and Chinese has none. In `ChiUn`
  the source puts spaces between tagged units, so positions are meaningful, but the text carries stray spaces
  ("起初， 神 創造 天 地。"). In `ChiUns` there are no spaces, so every tag lands on word 1 of ~2. Tags are present (389,008
  rows) but not word-anchored; the same applies to `word_count` for all four Chinese modules. The fix is a format or
  app decision (character offsets for CJK), not a converter patch.
- **Strong's tags shifted onto a neighbouring verse** where the tagger used another numbering: `fraFOB1744eb` 342
  verses, `spaRV1909eb` 69, `FreJND` 9, CUV 14 (swordcheck `strongs_tags_offset`). Text is right; the tags are wrong in
  these verses.
- **Missing verses.** `PorNVA` stores 12 verses fewer than KJV (10 have no text in the source, 2 are linked entries), `SpaRV`/`spaRV1909eb` 18,
  `FreJND` 6, `PorAlmeida1911` 1 (2 Cor 13:14), `fraFOB1744eb` 2, `arbnav2012eb` 1. The OCCB modules lack 143
  verses that the source joins into ranges (SWORD linked entries are stored once, on the first verse), and the IRV/HCV
  modules lack a handful for the same reason (8 to 16).
- **`benirv2019eb` has 721 doubled u-signs** (`ুু`, e.g. `সদাপ্রভুু` in Ps 23:1) in the source text.
- **`hin2017eb` has 2,307 verses with inline bold-italic cross-references** in the text itself, e.g. Gen 1:1
  "(इब्रा. 1:10, इब्रा. 11:3)", part of the source verse, not a note.
- **Zero-width joiners and non-joiners** are kept in hi/bn text (58 to 2,328 per module; orthographic). The research report
  says search must strip them; that is app-side work.
- **Darby and Almeida 1911** keep `*` footnote markers in the text (523 and 75 verses); the footnotes are not carried.
- **Rev 12:18 and 3 John 15** are folded into their host verses as designed (70 merges in Darby).
- Quotation spans still carry `ref`, while the schema wants `ref_start`/`ref_end`. The validator does not check it;
  the same drift affects every module with quotation spans, English included. Left alone to keep English output
  unchanged; worth a separate fix.

## Open questions

1. Is `GFDL-1.1-or-later` the right `license_spdx` for CUV, or should the module be `PD` with the FDL layer only in
   metadata? As written the app will not call it public domain.
2. Almeida 1911: PD (text) or GPL (the `.conf` says so)? Stored as PD.
3. Should the French LSG get a Segond to KJV map (separate task)?
4. Should `benirv2019eb` have the doubled u-sign collapsed at conversion?
