# Bible Scripts

_Note: This is still a work in progress; I hope to verify the setup procedure so that a fresh clone will be able to test and run quickly, but I haven't verified that yet, so stay tuned for updates!_

Development scripts for administering the Bible app's data: converting SWORD modules to the Bible repo's SQLite module format, then validating and querying the resulting module databases.

The module format itself is specified in the Bible repo (`packages/core/docs/features/module-format.md` and the schemas in `packages/core/sql/schemas/`). This repo only builds and checks modules against it; the one format document kept here is `docs/CrossWire-SWORD-Module-Format.md`, notes on the SWORD side of the conversion.

## Layout

```
scripts/            Node scripts, grouped by function
  modules/          work on module files
    validate-module.js        conformance checker for module databases
    import-tsk.js             TSK commentary -> cross-reference database
    check-redletter.js        red-letter (words of Christ) checks
  query/            db-cli: developer CLI for querying any module database (bible,
                    commentary, dictionary, topical, book, devotional), with raw SQL
                    and schema introspection
    db-cli.js       entry point: argument parsing, help, dispatch
    commands/       one file per command
    lib/            helpers shared by the commands
  lib/              shared code: module-identity.js, paths.js, schema.js
                    (loads the Bible repo's SQL schemas)
  data/             kjv-versification.json (used by the validator)
tools/
  import/sword/     C++ converters from SWORD modules to SQLite (bible, commentary,
                    dictionary, book, devotional, common), plus versification maps
                    in data/. Needs Linux and the SWORD library; see bible/README.md.
    verify/         bulk conversion from the module catalog, verifying every result
                    against libsword's own reading of the source; see verify/README.md
    probe/          look at any verse in both the source module and the converted one
  debug/            small queries against module and user databases
docs/               notes on the SWORD module format
```

## Running

```bash
npm install
npm test
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

## Known gap: converter schemas are copies

The Node importers (`scripts/modules/import-tsk.js`) build modules from the Bible repo's SQL schemas via `scripts/lib/schema.js`. The C++ converters do not: they carry hand-maintained copies of `module_info` and `verse_link` (in `tools/import/sword/common/sword_common.cpp`) and of each type's content tables. The `module_info` copies match the Bible repo's schema today, but they will drift again. The converters should load the Bible repo's schemas directly, as `schema.js` does.

## License

GPL-3.0-or-later; see `LICENSE`.

## Technical Notes
  * Written using Claude Code
