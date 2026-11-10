# Door43 modules converted by task 0221

Converted with `tools/import/door43/door43.py` (design: `door43-importers.md`). Release zips were fetched from git.door43.org, pinned by sha256 in `tools/import/door43/data/sources.json`. All sources are CC BY-SA 4.0 (the aligned Bibles also carry the licence of their base text, see the notes). Every output is a **derivative**: Markdown/TSV/USFM converted to the app module format, links resolved, verse numbering mapped to KJV, images and translationAcademy links removed (Arabic helps: Persian letters normalised). Module `.db` files are in the task work dir; nothing is published.

Validation: `check_module.py` (structural check: one module_info row, identity set, content non-empty, verse ids inside KJV, no dangling prebuilt links, no empty entries) passes for every module. `scripts/modules/validate-module.js` was **not run**: the checkout has no `node_modules` (`sqlite3`) and installing it was not permitted in this session. Run `npm ci && node scripts/modules/validate-module.js <db>` before publishing.

## Helps (tW, tN, tQ, OBS)

| Module | Source (release) | Entries | Verse links | Skipped rows | Coverage notes |
|---|---|---|---|---|---|
| `OBS-AR` (book) | [OBS-Aquifer/ar_obs v1](https://git.door43.org/OBS-Aquifer/ar_obs/releases/tag/v1) | 50 | 166 | - | 50 stories |
| `TN-AR` (commentary) | [BSOJ/ar_tn v6.1](https://git.door43.org/BSOJ/ar_tn/releases/tag/v6.1) | 30121 entries (96849 notes) | 14638 | empty_note 23, malformed_reference 23 | 66 books; Persian letters normalised |
| `TW-AR` (dictionary) | [BSOJ/ar_tw v5](https://git.door43.org/BSOJ/ar_tw/releases/tag/v5) | 962 | 3063 | - | Strong's index in module_feature `strongs_index` |
| `TQ-AR` (quiz) | [BSOJ/ar_tq v3.1](https://git.door43.org/BSOJ/ar_tq/releases/tag/v3.1) | 15213 | 15214 | - |  |
| `OBS-BN` (book) | [Door43-Catalog/bn_obs v4.4](https://git.door43.org/Door43-Catalog/bn_obs/releases/tag/v4.4) | 52 | 166 | - | 50 stories |
| `TN-BN` (commentary) | [translationCore-Create-BCS/bn_tn v6](https://git.door43.org/translationCore-Create-BCS/bn_tn/releases/tag/v6) | 2486 entries (10792 notes) | 1324 | malformed_reference 4 | 16 books |
| `TW-BN` (dictionary) | [translationCore-Create-BCS/bn_tw v4](https://git.door43.org/translationCore-Create-BCS/bn_tw/releases/tag/v4) | 638 | 4114 | - | Strong's index in module_feature `strongs_index` |
| `TQ-BN` (quiz) | [translationCore-Create-BCS/bn_tq v2](https://git.door43.org/translationCore-Create-BCS/bn_tq/releases/tag/v2) | 373 | 373 | - |  |
| `TN-ES` (commentary) | [es-419_gl/es-419_tn v66](https://git.door43.org/es-419_gl/es-419_tn/releases/tag/v66) | 205 entries (922 notes) | 25 | - | 4 books only (Rut, Jon, Tit, 3Jn) |
| `TW-ES` (dictionary) | [es-419_gl/es-419_tw v37](https://git.door43.org/es-419_gl/es-419_tw/releases/tag/v37) | 1055 | 2067 | - | Strong's index in module_feature `strongs_index` |
| `TN-FR` (commentary) | [Xenizo/fr_tn v2.0](https://git.door43.org/Xenizo/fr_tn/releases/tag/v2.0) | 7768 entries (27798 notes) | 4298 | - |  |
| `TW-FR` (dictionary) | [MVHS/fr_tw v0.0.1](https://git.door43.org/MVHS/fr_tw/releases/tag/v0.0.1) | 991 | 199 | - | Strong's index in module_feature `strongs_index` |
| `OBS-HI` (book) | [Door43-Catalog/hi_obs v8.3](https://git.door43.org/Door43-Catalog/hi_obs/releases/tag/v8.3) | 52 | 166 | - | 50 stories |
| `TN-HI` (commentary) | [translationCore-Create-BCS/hi_tn v9](https://git.door43.org/translationCore-Create-BCS/hi_tn/releases/tag/v9) | 1003 entries (3516 notes) | 261 | malformed_reference 6 | 34 books |
| `TW-HI` (dictionary) | [translationCore-Create-BCS/hi_tw v8](https://git.door43.org/translationCore-Create-BCS/hi_tw/releases/tag/v8) | 1048 | 5925 | - | Strong's index in module_feature `strongs_index` |
| `TQ-HI` (quiz) | [translationCore-Create-BCS/hi_tq v8](https://git.door43.org/translationCore-Create-BCS/hi_tq/releases/tag/v8) | 5314 | 5314 | malformed_reference 6, empty_question 1 |  |
| `OBS-PT` (book) | [Door43-Catalog/pt-br_obs v3.2.3](https://git.door43.org/Door43-Catalog/pt-br_obs/releases/tag/v3.2.3) | 52 | 166 | - | 50 stories |
| `TN-PT` (commentary) | [Door43-Catalog/pt-br_tn v7.1](https://git.door43.org/Door43-Catalog/pt-br_tn/releases/tag/v7.1) | 3262 entries (19333 notes) | 4 | empty_note 8 | legacy Markdown, NT |
| `TW-PT` (dictionary) | [Door43-Catalog/pt-br_tw v6.4](https://git.door43.org/Door43-Catalog/pt-br_tw/releases/tag/v6.4) | 1019 | 0 | - | Strong's index in module_feature `strongs_index` |
| `TQ-PT` (quiz) | [Door43-Catalog/pt-br_tq v9.2](https://git.door43.org/Door43-Catalog/pt-br_tq/releases/tag/v9.2) | 15233 | 15233 | - |  |

Attribution carried in each module (`module_info.copyright`, `metadata.attribution`, `metadata.modifications`): the `Copyright ©` line of the repo's LICENSE.md, the CC BY-SA 4.0 notice, and the conversion description. zh: no tW, tN or tQ release exists on Door43 (only `zh_obs` and `zh_cuv`; user forks are not used), so Chinese is skipped. es-419 and fr have no tQ/OBS release in the catalog used; es has no OBS import.

Unresolved links per module are listed in the per-module reports (`reports/<module>.json`, `SUMMARY.md` in the work dir); unresolved targets are kept as plain text. Open app-side item: `CommentaryLinkProcessor` strips `<a>` tags; prebuilt `scripture-link` / `dictionary-link` anchors need a flag (see the design note, section 4).

## Aligned Bibles (Strong's from word alignment)

| Module | Source (release) | Compared with (0220) | Verses | Strong's rows | Alignment coverage | Text equal | Result |
|---|---|---|---|---|---|---|---|
| `AVD-ALN` | [BSOJ/ar_avd v6.9](https://git.door43.org/BSOJ/ar_avd/releases/tag/v6.9) | AVD | 31096 | 436991 | 98.8% | 97.78% (99.32% ignoring spaces) | comparison with the 0220 module failed (verse sets identical: False, text equal 97.784%); using the -ALN suffix |
| `NAV-ALN` | [BSOJ/ar_nav v2](https://git.door43.org/BSOJ/ar_nav/releases/tag/v2) | NAV | 31100 | 8451 | 2.0% | 99.92% (99.92% ignoring spaces) | comparison with the 0220 module failed (verse sets identical: False, text equal 99.916%); using the -ALN suffix |
| `IRV-BN-ALN` | [Door43-Catalog/bn_irv v5](https://git.door43.org/Door43-Catalog/bn_irv/releases/tag/v5) | IRV-BN | 31094 | 63649 | 12.0% | 86.48% (88.17% ignoring spaces) | comparison with the 0220 module failed (verse sets identical: True, text equal 86.480%); using the -ALN suffix |
| `LSG1910-NT` | [MVHS/fr_lsg v0.0.2](https://git.door43.org/MVHS/fr_lsg/releases/tag/v0.0.2) | (none, NT only) | 7955 | 80768 | 51.9% | n/a | new module (no 0220 counterpart) |
| `IRV-HI-ALN` | [Door43-Catalog/hi_irv v12](https://git.door43.org/Door43-Catalog/hi_irv/releases/tag/v12) | IRV-HI | 31102 | 342004 | 66.3% | 65.15% (67.64% ignoring spaces) | comparison with the 0220 module failed (verse sets identical: True, text equal 65.153%); using the -ALN suffix |

Strong's/lemma/morphology are written as `sword2bible` does (`G0027`/`H0430`, `lemma = strong:<n>`; Hebrew morphology `HNcmsc`, Greek verbatim from UGNT). Compared with the 0220 modules the Door43 texts are different editions in places: acrostic headings and punctuation (AVD), 6 verses present in the 0220 text but absent from Door43 (AVD: ACT 13:39, EPH 2:15, PHP 1:17, 1TH 2:12, HEB 6:5-6; NAV: 1CH 24:20), and for hi/bn IRV a different revision. No result passed the 99.5% rule, so none reuses the 0220 identity; all are published-ready candidates under an `-ALN` abbreviation for the human to decide. `LSG1910-NT` (Segond 1910 NT): Door43's file has two impossible verses (Mark 9:51 and 10:53) that are dropped and recorded in the build report; it does not replace the skipped `fraLSG1910eb` (OT psalm numbering), it only covers the NT.

Findings: `AVD-ALN` is aligned across the whole Bible (98.8% of words) and is the only one that could carry the AVD Strong's layer; its text differs from the eBible AVD in 689 verses (mostly acrostic headings such as "بَاءٌ" in 0220 text, and punctuation) and in 6 verses present only in 0220, so it is offered as a separate module, not a replacement. `NAV-ALN` is only aligned in a few books (2.0% of words; see the per-book list), so it adds almost nothing over the 0220 NAV. `IRV-HI-ALN` (partial: 66%) and `IRV-BN-ALN` (NT, 12%) are different revisions of the IRV from the eBible modules (65% and 86% of verses equal). Strong's alignment on the French Segond NT covers about half of the words, as French function words are not aligned.

### Alignment coverage per book (target words covered by a Strong's row / all words)

**AVD-ALN**: GEN 98%, EXO 99%, LEV 98%, NUM 99%, DEU 98%, JOS 99%, JDG 98%, RUT 98%, 1SA 99%, 2SA 99%, 1KI 99%, 2KI 99%, 1CH 99%, 2CH 99%, EZR 99%, NEH 99%, EST 99%, JOB 99%, PSA 98%, PRO 99%, ECC 99%, SNG 98%, ISA 99%, JER 99%, LAM 98%, EZK 98%, DAN 99%, HOS 97%, JOL 99%, AMO 99%, OBA 99%, JON 99%, MIC 99%, NAM 99%, HAB 98%, ZEP 100%, HAG 99%, ZEC 99%, MAL 98%, MAT 99%, MRK 99%, LUK 99%, JHN 99%, ACT 99%, ROM 99%, 1CO 100%, 2CO 99%, GAL 99%, EPH 99%, PHP 99%, COL 99%, 1TH 99%, 2TH 100%, 1TI 99%, 2TI 100%, TIT 100%, PHM 100%, HEB 99%, JAS 100%, 1PE 99%, 2PE 100%, 1JN 98%, 2JN 100%, 3JN 99%, JUD 100%, REV 100%

**NAV-ALN**: GEN 0%, EXO 0%, LEV 0%, NUM 0%, DEU 0%, JOS 0%, JDG 0%, RUT 0%, 1SA 0%, 2SA 0%, 1KI 0%, 2KI 0%, 1CH 0%, 2CH 0%, EZR 0%, NEH 0%, EST 0%, JOB 0%, PSA 0%, PRO 0%, ECC 0%, SNG 0%, ISA 0%, JER 0%, LAM 0%, EZK 0%, DAN 0%, HOS 0%, JOL 0%, AMO 0%, OBA 0%, JON 99%, MIC 0%, NAM 0%, HAB 0%, ZEP 0%, HAG 0%, ZEC 0%, MAL 0%, MAT 0%, MRK 0%, LUK 0%, JHN 0%, ACT 0%, ROM 0%, 1CO 0%, 2CO 0%, GAL 0%, EPH 0%, PHP 0%, COL 0%, 1TH 0%, 2TH 0%, 1TI 100%, 2TI 0%, TIT 100%, PHM 100%, HEB 0%, JAS 100%, 1PE 100%, 2PE 100%, 1JN 0%, 2JN 0%, 3JN 100%, JUD 100%, REV 0%

**IRV-BN-ALN**: GEN 0%, EXO 0%, LEV 0%, NUM 0%, DEU 0%, JOS 0%, JDG 0%, RUT 0%, 1SA 0%, 2SA 0%, 1KI 0%, 2KI 0%, 1CH 0%, 2CH 0%, EZR 0%, NEH 0%, EST 0%, JOB 0%, PSA 0%, PRO 0%, ECC 0%, SNG 0%, ISA 0%, JER 0%, LAM 0%, EZK 0%, DAN 0%, HOS 0%, JOL 0%, AMO 0%, OBA 0%, JON 0%, MIC 0%, NAM 0%, HAB 0%, ZEP 0%, HAG 0%, ZEC 0%, MAL 0%, MAT 9%, MRK 27%, LUK 70%, JHN 42%, ACT 73%, ROM 53%, 1CO 66%, 2CO 55%, GAL 66%, EPH 72%, PHP 71%, COL 64%, 1TH 67%, 2TH 69%, 1TI 24%, 2TI 27%, TIT 51%, PHM 49%, HEB 27%, JAS 42%, 1PE 74%, 2PE 63%, 1JN 56%, 2JN 68%, 3JN 75%, JUD 61%, REV 36%

**LSG1910-NT**: MAT 51%, MRK 49%, LUK 50%, JHN 54%, ACT 53%, ROM 51%, 1CO 49%, 2CO 48%, GAL 50%, EPH 53%, PHP 100%, COL 53%, 1TH 50%, 2TH 53%, 1TI 52%, 2TI 55%, TIT 100%, PHM 48%, HEB 51%, JAS 52%, 1PE 51%, 2PE 51%, 1JN 55%, 2JN 57%, 3JN 47%, JUD 49%, REV 48%

**IRV-HI-ALN**: GEN 89%, EXO 61%, LEV 69%, NUM 59%, DEU 90%, JOS 74%, JDG 55%, RUT 55%, 1SA 59%, 2SA 57%, 1KI 91%, 2KI 87%, 1CH 93%, 2CH 84%, EZR 53%, NEH 68%, EST 61%, JOB 55%, PSA 73%, PRO 77%, ECC 72%, SNG 55%, ISA 52%, JER 54%, LAM 52%, EZK 90%, DAN 72%, HOS 57%, JOL 90%, AMO 57%, OBA 54%, JON 88%, MIC 87%, NAM 85%, HAB 46%, ZEP 46%, HAG 76%, ZEC 78%, MAL 81%, MAT 38%, MRK 17%, LUK 54%, JHN 35%, ACT 57%, ROM 62%, 1CO 58%, 2CO 59%, GAL 47%, EPH 42%, PHP 35%, COL 44%, 1TH 56%, 2TH 45%, 1TI 76%, 2TI 79%, TIT 94%, PHM 83%, HEB 70%, JAS 77%, 1PE 53%, 2PE 91%, 1JN 96%, 2JN 52%, 3JN 37%, JUD 56%, REV 65%
