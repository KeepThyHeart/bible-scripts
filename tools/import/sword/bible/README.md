# sword2bible - SWORD Bible Module to SQLite Converter

Converts CrossWire SWORD Bible module ZIP files into our proprietary SQLite database format.

## Overview

This tool takes SWORD Bible module ZIP files (from [CrossWire](https://crosswire.org/sword/modules/)) and converts them to our SQLite database format, defined by the Bible repo (`packages/core/docs/features/module-format.md` and `packages/core/sql/schemas/initial/BibleTranslation.sql`).

**Input:** SWORD module `.zip` file (e.g., `KJV.zip`)
**Output:** SQLite database file (e.g., `bible_kjv.db`)

## Features

- Extracts SWORD module metadata from `.conf` files, including a full
  identity/provenance block: `module_uuid`, `format_version`, `content_sha256`,
  `license_spdx`, `license_url`, `source_url`, `versification`
- **Canon enforcement:** book numbers come from each verse's OSIS reference, so
  books outside the 66-book Protestant canon are skipped with a warning instead
  of renumbering every book after them
- **Clean text:** `bible_verse.text` is canonical UTF-8 — no markup, no
  pilcrows, no leading whitespace
- **Structured spans:** presentation lives in `bible_verse.formatting` as data
  records naming word ranges (`divine_name`, `supplied`, `words_of_christ`,
  `emphasis`, `quotation`, `transliteration`) with **0-based inclusive** offsets
- **Block properties:** `paragraph_start`, `poetry_level`, `heading`, `selah`,
  taken from the source's own markers rather than inferred from presentation
- **Variant verse divisions** (Rev 12:18, 3 John 15) are folded into their
  canonical host verse, with the source numbering preserved in
  `formatting.source_verses`
- Populates `interlinear_word` with Strong's numbers anchored to real word offsets
- Emits the shared `verse_link` table
- Automatically builds a full-text search (FTS5) index over the clean text
- Handles compressed (zText) and uncompressed (RawText) modules
- Uses the official SWORD C++ library

## System Requirements

- **Operating System:** Linux (Ubuntu/Debian recommended)
- **Compiler:** g++ with C++17 support
- **Dependencies:**
  - `libsword-dev` - SWORD library
  - `libsqlite3-dev` - SQLite3 library
  - `libzip-dev` - ZIP extraction library

## Installation

### 1. Install Dependencies

On Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y build-essential libsword-dev libsqlite3-dev libzip-dev
```

Or use the Makefile shortcut:

```bash
make install-deps
```

### 2. Build the Converter

```bash
make
```

This compiles `sword2bible.cpp` into an executable called `sword2bible`.

## Usage

### Basic Usage

```bash
./sword2bible --input <module.zip> --output <bible_xxx.db>
```

**Example:**

```bash
./sword2bible --input KJV.zip --output bible_kjv.db
```

### Command-Line Options

| Option | Short | Description |
|--------|-------|-------------|
| `--input` | `-i` | Input SWORD module ZIP file |
| `--output` | `-o` | Output SQLite database file |
| `--help` | `-h` | Show help message |

### Quick Test

Test the converter with the included KJV module:

```bash
make test
```

This will:
1. Build the converter
2. Convert `KJV.zip` to `bible_kjv.db`
3. Display conversion progress

### Verify the output

After conversion, check that the module conforms to the module format:

```bash
node ../../../../scripts/modules/validate-module.js bible_kjv.db
```

The validator reports identity, structure, text hygiene, formatting-span and interlinear-offset errors. To look at the contents, use `make inspect` or `node ../../../debug/query-module.cjs kjv`.

### Inspect Results

After conversion, inspect the database:

```bash
make inspect
```

This will show:
- Module metadata
- Total verse count
- Sample verses (Genesis 1:1-3)

Or use `sqlite3` directly:

```bash
sqlite3 bible_kjv.db

# List tables
.tables

# View module info
SELECT * FROM module_info;

# Count verses
SELECT COUNT(*) FROM bible_verse;

# Get a specific verse (John 3:16) -- text is clean canonical UTF-8
SELECT text FROM bible_verse WHERE verse_id = 43003016;

# See its structured formatting
SELECT text, formatting FROM bible_verse WHERE verse_id = 19023001;  -- Psalm 23:1

# Search for text (FTS5 indexes `text` directly)
SELECT verse_id, text FROM bible_verse
WHERE verse_id IN (
    SELECT rowid FROM bible_verse_fts WHERE bible_verse_fts MATCH 'love'
)
LIMIT 10;
```

## Conversion Process

The converter performs these steps:

1. **Extract ZIP** - Extracts SWORD module to temporary directory
2. **Initialize SWORD** - Loads module using SWORD library
3. **Create Database** - Creates SQLite database with schema
4. **Parse Metadata** - Extracts module info from `.conf` file
5. **Insert Module Info** - Populates `module_info` table
6. **Convert Verses** - Iterates all verses and inserts into database
   - Calculates verse IDs using: `(book * 1000000) + (chapter * 1000) + verse`
   - Resolves the book from the verse's **OSIS reference**, mapping it to a
     canonical 1-66 number; anything outside the canon is skipped with a warning
   - **Parses the source markup once**, producing clean text plus spans:
     `<divineName>` -> `divine_name`, `<transChange type="added">` -> `supplied`,
     `<q who="Jesus">` -> `words_of_christ`, `<reference osisRef=...>` ->
     `quotation` carrying the source `verse_id` in `ref`
   - Lifts `<title>` into `block.heading`, `<l level>` into `block.poetry_level`,
     and pilcrows into `block.paragraph_start`
   - Drops `<note>` content (footnotes are not verse text)
   - Counts words and inserts interlinear words at real 0-based word offsets
   - Automatically builds the FTS5 index via triggers
7. **Finalize** - Records `content_sha256` and the source book names in
   `module_info.metadata`

## Output Database Schema

The resulting SQLite database contains:

### Tables

- **`module_info`** - Identity and provenance (one row): `module_uuid`, `format`,
  `format_version`, `content_version`, `content_sha256`, `license_spdx`,
  `license_url`, `source_url`, `versification`, plus display metadata.
  Books outside the 66-book canon are not imported.
- **`bible_verse`** - `verse_id`, `text` (clean canonical UTF-8), `formatting`
  (structured spans JSON), `word_count`.
- **`bible_verse_fts`** - FTS5 external-content index over `text`
- **`interlinear_word`** - Strong's numbers at 0-based inclusive word offsets
- **`verse_link`** - The shared content-to-verse linking table
- **`module_feature`** - Module features (future use)
- **`schema_version`** - Database schema version

The dead `book_search_index`, `book_search_metadata` and `verse_positions`
tables are **not** created: the live search index lives in `main.db`.

### Verse ID Format

Verse IDs are calculated as:

```
verse_id = (book_number * 1000000) + (chapter * 1000) + verse
```

**Examples:**
- Genesis 1:1 → `1001001`
- John 3:16 → `43003016`
- Revelation 22:21 → `66022021`

This matches the verse ID format in `main.db` for reference data.

### Querying Strong's Numbers

After conversion, you can query Strong's numbers:

```bash
sqlite3 bible_kjv.db

# Get John 3:16 with Strong's numbers
SELECT
    v.text,
    i.word_position_start,
    i.strongs_number,
    i.original_word
FROM bible_verse v
JOIN interlinear_word i ON v.verse_id = i.verse_id
WHERE v.verse_id = 43003016
ORDER BY i.word_position_start;

# Find all uses of Strong's H0430 (Elohim/God)
SELECT DISTINCT v.verse_id, v.text
FROM bible_verse v
JOIN interlinear_word i ON v.verse_id = i.verse_id
WHERE i.strongs_number = 'H0430'
LIMIT 20;

# Count occurrences of each Strong's number
SELECT
    strongs_number,
    COUNT(*) as occurrences,
    MIN(original_word) as sample_word
FROM interlinear_word
GROUP BY strongs_number
ORDER BY occurrences DESC
LIMIT 20;
```

## Obtaining SWORD Modules

### Official CrossWire Modules

Download modules from: https://crosswire.org/sword/modules/

Common Bible translations:
- **KJV** - King James Version (Public Domain)
- **WEB** - World English Bible (Public Domain)
- **ASV** - American Standard Version (Public Domain)
- **YLT** - Young's Literal Translation (Public Domain)

### Using InstallMgr

Alternatively, use SWORD's `installmgr` to download modules:

```bash
# List available modules
installmgr -r CrossWire

# Install a module
installmgr -ri CrossWire KJV

# Find the installed module
ls ~/.sword/modules/texts/ztext/kjv/

# Create ZIP for conversion
cd ~/.sword
zip -r KJV.zip modules/texts/ztext/kjv/ mods.d/kjv.conf
```

## Batch Conversion

To convert multiple modules:

```bash
#!/bin/bash
# convert-all.sh

for module in *.zip; do
    name=$(basename "$module" .zip)
    output="bible_${name,,}.db"  # Lowercase

    echo "Converting $module -> $output"
    ./sword2bible --input "$module" --output "$output"

    if [ $? -eq 0 ]; then
        echo "✓ $output"
    else
        echo "✗ Failed: $module"
    fi
done
```

## OSIS Parsing Status

### ✅ Working Features

**Strong's Numbers Extraction (COMPLETE)**
- **Status:** ✅ Fully Working
- **Results:** 349,095 interlinear words extracted from KJV
- **Data Captured:**
  - Strong's number (e.g., H07225, G3056)
  - Original word text
  - Word position in verse
  - Lemma reference
- **Technical Note:** SWORD uses `savlm` attribute, not `lemma`

**Structured Formatting**
- **Status:** Working
- **Column:** `bible_verse.formatting`
- **Offsets:** word indices, **0-based and inclusive**, over a whitespace split
  of the clean `text`

**Example** — Psalm 23:1:
```
text: The LORD is my shepherd; I shall not want.
formatting: {"v":1,"spans":[{"type":"divine_name","start":1,"end":1},
                            {"type":"supplied","start":2,"end":2}]}
```

Span types and their USFM equivalents (canonical list in
`packages/core/src/Data/Text/VerseFormatting.ts`):

| Span type | USFM | Source markup |
|---|---|---|
| `divine_name` | `\nd` | `<divineName>`, `<font size="-1">` |
| `supplied` | `\add` | `<transChange type="added">`, `<hi type="italic">`, `<i>` |
| `words_of_christ` | `\wj` | `<q who="Jesus">`, `<font color="red">` |
| `emphasis` | `\em` | `<hi type="emphasis">`, `<b>`, `<em>` |
| `quotation` | `\qt` | `<q>`, `<cite>`, `<reference osisRef=...>` (fills `ref`) |
| `transliteration` | `\tl` | `<foreign>`, `<translit>` |

Block-level properties: `paragraph_start` (`\p`), `poetry_level` 1-3
(`\q1`-`\q3`), `heading` (`\d`/`\s`), `selah` (`\qs`).

### Known limitations

**Mid-verse paragraph breaks** are not representable in a verse-keyed row. A
pilcrow at the *start* of a verse sets `paragraph_start`; one at the *end*
belongs to the following verse and is carried forward; one in the middle is
dropped.

**Non-KJV versification** is translated into KJV English where a verse map
exists: NRSV, Vulg and Synodal with libsword's own maps, MT with
`../data/versification-mt-to-kjv.json`, German with
`../data/versification-german-to-kjv.json` (which extends the MT map). NRSVA,
SynodalProt, Leningrad and Luther are identical to NRSV, Synodal, MT and German
in all 66 canonical books, so they use those maps. Anything else (LXX, Catholic,
Calvin, Segond, DarbyFr, Orthodox, SynodalP) is refused by default: its canonical book names and numbers
are right but its verse numbers are shifted, which no book-level check can
detect. Pass `--allow-versification` only after confirming the numbering really
matches KJV English.

**A .conf that declares the wrong system** (text numbered like the Hebrew Bible
in a module that says KJV) is corrected with `--versification MT`. Evidence and
candidates are kept in `../data/versification-overrides.json`;
`../verify/swordcheck.py` finds them.

**Additions inside canonical books** (Vulgate Esther 10:4-13, Synodal Joshua
24:34-36) are dropped when the source chapter runs 3+ verses past the KJV
chapter. A single verse past the end is a split verse and is folded onto the
chapter's last verse.

**Linked verses** (SWORD's one text for a verse range) are stored once, on the
first verse of the range; the other verses of the range have no row.

**Encoding:** Latin-1 modules (SWORD's default encoding) are transcoded to
UTF-8, and stray Latin-1 bytes in modules declaring UTF-8 are repaired.

### Verifying conversions

`../verify/swordcheck.py` converts modules in bulk and checks every result
against libsword's own reading of the source module: missing verses, text
fidelity, red letter, canon, versification alignment, text hygiene and
metadata. See `../verify/README.md`.

**Deuterocanonical / apocryphal books** are skipped by design (the format's canon is
fixed at the 66-book Protestant canon). Skipped books are listed on stderr and recorded in
`module_info.metadata.skipped_books`.

### Performance

**Current Performance (KJV):**
- Conversion Time: ~40 seconds for 31,102 verses
- Database Size: 31 MB
- Interlinear Inserts: 349,095 rows (~8,700 inserts/second)
- Verses with Formatting: 25,624

## Troubleshooting

### Build Errors

**Error: `swmgr.h: No such file or directory`**

Solution: Install libsword-dev:
```bash
sudo apt-get install libsword-dev
```

**Error: `cannot find -lsword`**

Solution: SWORD library not installed:
```bash
sudo apt-get install libsword-dev
```

### Conversion Errors

**Error: `No SWORD modules found in extracted files`**

Solution: ZIP file may not be a valid SWORD module. Verify it contains:
- `mods.d/*.conf` - Module configuration
- `modules/texts/` - Module data files

**Error: `Module is not a Biblical Text`**

Solution: This tool only converts Bible modules. For commentaries, dictionaries, or other module types, use separate converters:
- `sword2commentary` (future)
- `sword2dictionary` (future)
- `sword2book` (future)

## Docker Usage (Optional)

For reproducible builds and conversions:

```dockerfile
# Dockerfile
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    build-essential \
    libsword-dev \
    libsqlite3-dev \
    libzip-dev

WORKDIR /converter
COPY sword2bible.cpp Makefile ./
RUN make

WORKDIR /data
ENTRYPOINT ["/converter/sword2bible"]
```

Build and run:

```bash
docker build -t sword2bible .
docker run -v $(pwd):/data sword2bible --input /data/KJV.zip --output /data/bible_kjv.db
```

## Contributing

When adding features to the converter:

1. Follow the database schema in the Bible repo (`packages/core/sql/schemas/initial/BibleTranslation.sql`)
2. Update this README with new features
3. Test with multiple module types (compressed/uncompressed, different languages)
4. Handle edge cases gracefully (empty verses, missing metadata, etc.)

## License

This converter tool is part of the Bible Study Application project.

The tool uses:
- **libsword** - SWORD library (GPL)
- **libsqlite3** - SQLite (Public Domain)
- **libzip** - ZIP library (BSD)

SWORD modules have their own licenses (check each module's `.conf` file for `DistributionLicense` field).

## References

- **SWORD Project:** https://crosswire.org/sword/
- **SWORD Format Documentation:** `docs/CrossWire-SWORD-Module-Format.md` (this repo)
- **Module format and database schema:** `packages/core/docs/features/module-format.md` and `packages/core/sql/schemas/initial/` in the Bible repo
