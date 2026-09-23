# Bible Scripts

_Note: This is still a work in progress; I hope to verify the setup procedure so that a fresh clone will be able to test and run quickly, but I haven't verified that yet, so stay tuned for updates!_

Development scripts for administering the Bible app's data: converting SWORD modules to the Bible repo's SQLite module format, then validating and querying the resulting module databases.

The module format itself is specified in the Bible repo (`packages/core/docs/features/module-format.md` and the schemas in `packages/core/sql/schemas/`). This repo only builds and checks modules against it; the one format document kept here is `docs/CrossWire-SWORD-Module-Format.md`, notes on the SWORD side of the conversion.

## Layout

```
scripts/            Node scripts, grouped by function
  modules/          work on module files
    validate-module.js        conformance checker for module databases (v0.1 and v0.2)
    validate-module.test.js   its tests (v0.2 rules: fts5, verse_link indexes,
                               dictionary-iff-compressed, digest recompute)
    import-tsk.js             TSK commentary -> cross-reference database
    check-redletter.js        red-letter (words of Christ) checks
  query/            db-cli: developer CLI for querying any module database (bible,
                    commentary, dictionary, topical, book, devotional), with raw SQL
                    and schema introspection
    db-cli.js       entry point: argument parsing, help, dispatch
    commands/       one file per command
    lib/            helpers shared by the commands
  lib/              shared code
    module-identity.js   identity/UUID helpers; also the name -> module_uuid
                         reconversion mapping (loadUuidMap/saveUuidMap/resolveModuleUuid)
    paths.js             where the Bible app's data lives
    schema.js            loads the Bible repo's SQL schemas; also a CLI
                         (`node scripts/lib/schema.js <Name.sql>`) the C++
                         converters shell out to instead of hand-copying DDL
    codec.js              module format v0.2 content codecs (design §3):
                          full deflate encode/decode, zstd decode
    content-digest.js     the canonical content_sha256 (design §2.7)
  data/             kjv-versification.json (used by the validator);
                    module-uuid-map.json (name -> module_uuid, task 0035 requirement 5)
  run-tests.js      runs every scripts/**/*.test.js file (what `npm test` calls)
tools/
  import/sword/     C++ converters from SWORD modules to SQLite (bible, commentary,
                    dictionary, book, devotional, common), plus versification maps
                    in data/. Needs Linux and the SWORD library; see bible/README.md.
    common/         shared C++: sword_common.{h,cpp} (OSIS parsing, identity),
                    schema_bridge.{h,cpp} (loads schema DDL via scripts/lib/schema.js,
                    same as the Node importers), compression.{h,cpp} (deflate/zstd,
                    dictionary training), content_digest.{h,cpp} (the same §2.7
                    digest as content-digest.js). `make test` runs selftest.cpp.
    verify/         bulk conversion from the module catalog, verifying every result
                    against libsword's own reading of the source; see verify/README.md
    probe/          look at any verse in both the source module and the converted one
  debug/            small queries against module and user databases
docs/               notes on the SWORD module format
```

## Running

```bash
npm install
npm test                                  # JS: scripts/**/*.test.js
make -C tools/import/sword/common test    # C++: compression/digest/schema-bridge selftests
node scripts/query/db-cli.js --help
node scripts/modules/validate-module.js --all
```

## Where the data lives

These scripts work on the Bible app's databases but live outside its repo. Defaults assume the Bible repo is checked out next to this one (`../bible`) and use its `apps/desktop/data/` directory. Override with environment variables:

| Variable | Default |
| --- | --- |
| `BIBLE_REPO` | `../bible` |
| `BIBLE_DATA_DIR` | `<BIBLE_REPO>/apps/desktop/data` |
| `BIBLE_MODULES_DIR` | `<BIBLE_DATA_DIR>/modules` |
| `BIBLE_MAIN_DB` | `<BIBLE_DATA_DIR>/main.db` |

Scripts that take explicit paths (`--modules-dir`, `--input`, `--output`, ...) use those in preference. All defaults are defined in `scripts/lib/paths.js`.

## Module format v0.2 (task 0035, Phase 1 / F10)

The five C++ converters (`tools/import/sword/{bible,commentary,dictionary,book,devotional}`)
and this repo's verification tooling now emit and check **format v0.2**
(the design in `bible` task 0027; a copy of the design doc is in that task's
archive). What changed, mechanically:

- **No more hand-copied DDL.** The converters used to carry their own copies
  of `module_info`, `verse_link` and each type's content tables — the exact
  gap this section used to describe. They now call
  `SwordCommon::loadRepoSchema("BibleTranslation.sql")` (etc.), which shells out to
  `node scripts/lib/schema.js <Name.sql>` — the same `loadSchema()` the Node
  importers already used — so a C++ and a Node converter can never see
  different DDL for the same schema file. This needs a real `bible` repo
  checkout at `BIBLE_REPO` (default `../bible`) to run against; it cannot be
  exercised in a sandbox with no such checkout, which is why
  `tools/import/sword/common/selftest.cpp` tests the bridge mechanism itself
  against a small fixture schema tree instead of the real one.
- **Compression** (design §3): commentary/dictionary/book/devotional modules
  with ≥8MB of decoded prose are deflate-compressed by default, with a
  zstd-trained dictionary; `--codec=none|deflate|zstd` (or `--compress`)
  overrides per module. Bible verse text, cross-references, topical indexes
  and tag graphs are never compressed. `tools/import/sword/common/compression.{h,cpp}`
  implements both codecs (zstd via the runtime library only — no
  `libzstd-dev` package was available to build against, so the handful of
  stable public API functions used are declared by hand; see that file's
  doc comment) and the per-row keep-only-if-smaller rule.
- **The canonical digest** (design §2.7) is implemented identically in THREE
  places — `scripts/lib/content-digest.js`, `tools/import/sword/common/content_digest.{h,cpp}`,
  and `tools/import/sword/verify/swordcheck.py`'s `compute_content_sha256()` —
  because none of them can import a shared implementation from the `bible`
  repo (out of scope for this repo to touch). All three were cross-checked
  against the same synthetic fixture during development and produce a
  byte-identical digest.
- **Reconversion identity** (design §6.2, requirement 5): `--uuid=<uuid>` on
  every converter overrides the minted `module_uuid`. The name → uuid
  mapping itself lives in `scripts/data/module-uuid-map.json`
  (`scripts/lib/module-identity.js`'s `loadUuidMap`/`saveUuidMap`/
  `resolveModuleUuid`/`recordModuleUuid`); nothing populates it yet — that
  is Phase 2 (F13)'s batch-conversion job, deliberately not started by this
  task (see task 0035's spec).
- **`scripts/modules/validate-module.js`** gates on `module_info.format_version`:
  a `'0.2'` file gets the new rules (no FTS5 table; the 3-index `verse_link`
  contract, without `idx_verse_link_start`; decode-aware content scanning;
  `compression_dictionary` present iff `compression != 'none'`; a
  `content_sha256` recompute); anything else keeps the pre-v0.2 rules, so a
  `0.1` module already on disk isn't newly failed. Whether the second copy
  of this validator (the other lives in the `bible` repo, out of this repo's
  reach) should keep existing at all was task 0035's requirement 7 to decide
  — recorded in that task's thread: yes, for now, because this repo's own
  verify harness (`swordcheck.py`) needs a standalone checker it can shell
  out to without an Electron/app checkout; unifying the two copies (e.g. a
  shared npm package) is future work, not this task's to do.
- **`tools/import/sword/verify/swordcheck.py`** additionally checks, on top
  of what it already verified: `format_version == '0.2'`, no FTS5 table,
  `compression_dictionary` iff compressed, the recomputed digest, and (when
  the uuid map has an entry for the module) that the uuid matches it. A
  `verify --check-codecs` flag reconverts the same module with each codec
  and asserts `content_sha256` is identical across all of them — opt-in,
  since it converts the module up to 3x.

**Not done here, by design:** reconverting and republishing the 147-module
library (F13/Phase 2) — the spec for this task is explicit that Phase 2 does
not start until a human confirms Phase 1 is accepted AND the app-side
prerequisites are in a released `bible` build (an old build silently returns
no search results against a v0.2 module, which has no FTS table). Also out
of scope: a converter for topical-index or tag-graph modules (neither exists
in this repo today; `topical_nave` is in the dev catalog and stays on
whatever ad hoc path it is on until that gap is worked separately — see task
0035's spec, "Out of scope").

## License

GPL-3.0-or-later; see `LICENSE`.

## Technical Notes
  * Written using Claude Code
