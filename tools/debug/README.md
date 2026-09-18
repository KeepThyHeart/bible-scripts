# Debug Utilities

Quick CLI tools for querying the Bible app's databases. Run from this repo's root.

They read the Bible repo's data directory (default: `../bible/apps/desktop/data`). Set `BIBLE_REPO`, `BIBLE_DATA_DIR` or `BIBLE_MODULES_DIR` to point elsewhere; see `scripts/lib/paths.js`.

## query-verse.cjs
Query a specific verse's text, formatting data, and interlinear words.
```bash
node tools/debug/query-verse.cjs 43003016        # John 3:16 (KJV default)
node tools/debug/query-verse.cjs 43003001 kjv    # John 3:1 in KJV
node tools/debug/query-verse.cjs 1001001 asv     # Genesis 1:1 in ASV
```
Verse ID = `(book * 1000000) + (chapter * 1000) + verse`

## query-module.cjs
Module info, stats, and data quality checks.
```bash
node tools/debug/query-module.cjs kjv    # KJV stats, red-letter check, interlinear quality
node tools/debug/query-module.cjs asv    # ASV stats
```

## list-modules.cjs
List all installed module databases with sizes.
```bash
node tools/debug/list-modules.cjs             # All modules
node tools/debug/list-modules.cjs bible       # Bible modules only
node tools/debug/list-modules.cjs dictionary  # Dictionary modules only
```

## query-user-db.cjs
Query the user database (notes, highlights, sessions).
```bash
node tools/debug/query-user-db.cjs              # Stats overview
node tools/debug/query-user-db.cjs tables       # List all tables with row counts
node tools/debug/query-user-db.cjs notes        # Recent notes
node tools/debug/query-user-db.cjs notes 43003016   # Notes for John 3:16
node tools/debug/query-user-db.cjs highlights   # Recent highlights
node tools/debug/query-user-db.cjs sessions     # Study sessions
```
