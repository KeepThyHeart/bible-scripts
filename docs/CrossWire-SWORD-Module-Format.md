# CrossWire SWORD Module Format Documentation

## Table of Contents

1. [Introduction](#introduction)
2. [Important Design Philosophy](#important-design-philosophy)
3. [Module Directory Structure](#module-directory-structure)
4. [Module Configuration Files (.conf)](#module-configuration-files-conf)
5. [Module Types and Drivers](#module-types-and-drivers)
6. [Binary File Formats](#binary-file-formats)
7. [Versification Systems](#versification-systems)
8. [Encoding and Localization](#encoding-and-localization)
9. [Compression Methods](#compression-methods)
10. [How Applications Should Work with Modules](#how-applications-should-work-with-modules)
11. [Module Creation Process](#module-creation-process)

---

## Introduction

SWORD (Scripture Organizing and Retrieval of Data) is a Bible software library developed by the CrossWire Bible Society. It provides a standardized system for storing, accessing, and displaying biblical texts, commentaries, dictionaries, lexicons, and other religious study materials.

A SWORD module consists of:
- **Binary data files** in various formats (text content, indexes, compressed data)
- **A configuration (.conf) file** specifying module attributes and location
- **Optional search indexes** for fast searching

---

## Important Design Philosophy

**Critical:** The CrossWire Bible Society has explicitly stated that:

> "Our module file format is proprietary in the sense that we see no need to document it and certainly no need to stick to it. As we change it, we change it, and we therefore do not encourage direct interaction with it, but firmly recommend use of the API (either C++ or Java)."

This means:
- **The binary format is deliberately undocumented** and subject to change
- **Applications should use the SWORD API** (C++ or Java) to access modules
- **Direct parsing of binary files is discouraged** and not future-proof
- The format may change without notice between SWORD versions

That said, this document provides information gathered from:
- Official CrossWire wiki documentation
- .conf file specifications
- Community reverse-engineering efforts
- SWORD library source code analysis
- Xiphos implementation examination

---

## Module Directory Structure

### Standard Installation Locations

The SWORD API searches for modules in the following locations (in order):

1. `./sword.conf` with DataPath setting → looks in `<DataPath>/mods.d/`
2. `./mods.d`
3. `$SWORD_PATH/mods.d`
4. `$HOME/.sword/mods.d/` (Linux/Unix)
5. `%APPDATA%\.sword\mods.d\` (Windows)

### Directory Layout

```
.sword/
├── mods.d/                    # Configuration files directory
│   ├── module1.conf          # One .conf file per module
│   ├── module2.conf
│   └── ...
├── modules/
│   ├── texts/                # Biblical texts
│   │   ├── rawtext/          # Uncompressed text modules
│   │   │   └── modulename/
│   │   │       ├── ot         # Old Testament text
│   │   │       ├── nt         # New Testament text
│   │   │       ├── ot.vss     # Old Testament verse index
│   │   │       └── nt.vss     # New Testament verse index
│   │   └── ztext/            # Compressed text modules
│   │       └── modulename/
│   │           ├── ot.bzs     # Old Testament buffer index
│   │           ├── ot.bzv     # Old Testament verse index
│   │           ├── ot.bzz     # Old Testament compressed data
│   │           ├── nt.bzs     # New Testament buffer index
│   │           ├── nt.bzv     # New Testament verse index
│   │           └── nt.bzz     # New Testament compressed data
│   ├── comments/             # Commentary modules
│   │   ├── rawcom/
│   │   └── zcom/
│   ├── lexdict/              # Lexicons and dictionaries
│   │   ├── rawld/
│   │   │   └── modulename/
│   │   │       ├── dict.dat   # Dictionary data
│   │   │       └── dict.idx   # Dictionary index
│   │   └── zld/
│   └── genbook/              # General books
│       └── rawgenbook/
└── InstallMgr/
    └── InstallMgr.conf       # Module installation manager config
```

---

## Module Configuration Files (.conf)

### Basic Structure

Configuration files are **plain text INI-style files** with the following format:

```ini
[ModuleName]
Key=Value
Key=Value
# Comments start with hash
```

### File Naming Convention

- Filename: `<modulename>.conf` (lowercase recommended)
- Module name must use only: `A-Z`, `a-z`, `0-9`, `_`
- Example: `kjv.conf` for a module named "KJV"

### Required Fields

Every module MUST include:

| Field | Description | Example |
|-------|-------------|---------|
| **DataPath** | Relative path to module data files | `./modules/texts/ztext/kjv/` |
| **ModDrv** | Module driver (see types below) | `zText` |
| **Encoding** | Character encoding | `UTF-8` |
| **Lang** | Language code (BCP 47) | `en` |
| **Description** | Short module description | `King James Version (1769)` |
| **About** | Detailed module information | `The KJV was commissioned...` |
| **DistributionLicense** | License type | `Public Domain` |
| **TextSource** | Source of the text | `CrossWire` |

### Common Optional Fields

| Field | Description | Example Values |
|-------|-------------|----------------|
| **SourceType** | Source markup format | `OSIS`, `TEI`, `ThML`, `GBF`, `Plaintext` |
| **Versification** | Verse numbering system | `KJV`, `NRSV`, `Catholic`, `Catholic2`, `Synodal`, `MT` |
| **BlockType** | Compression block size | `BOOK` (default), `CHAPTER`, `VERSE` |
| **CompressType** | Compression algorithm | `LZSS`, `ZIP` |
| **Direction** | Text direction | `LtoR` (left-to-right), `RtoL` (right-to-left), `BiDi` |
| **Version** | Module version | `2.5` |
| **History** | Version history | `1.0 Initial release...` |
| **Abbreviation** | Short module abbreviation | `KJV` |
| **MinimumVersion** | Minimum SWORD version | `1.5.9` |
| **Category** | Module category | `Biblical Texts`, `Essays` |
| **LCSH** | Library of Congress subject heading | `Bible. English` |
| **Font** | Recommended font | `Arial Unicode MS` |
| **DisplayLevel** | UI display level | `1` (show to all), `3` (advanced) |
| **InstallSize** | Size in bytes | `4567890` |

### Feature Fields

Features indicate special capabilities of a module:

```ini
Feature=StrongsNumbers
Feature=GreekDef
Feature=HebrewDef
Feature=GreekParse
Feature=HebrewParse
Feature=DailyDevotion
Feature=Glossary
Feature=Images
Feature=NoParagraphs
```

### GlobalOptionFilter Fields

These specify filters for optional text features:

```ini
GlobalOptionFilter=OSISStrongs
GlobalOptionFilter=OSISMorph
GlobalOptionFilter=OSISLemma
GlobalOptionFilter=OSISFootnotes
GlobalOptionFilter=OSISScripref
GlobalOptionFilter=OSISHeadings
GlobalOptionFilter=OSISRedLetterWords
GlobalOptionFilter=OSISVariants
GlobalOptionFilter=UTF8Cantillation
GlobalOptionFilter=UTF8GreekAccents
GlobalOptionFilter=UTF8HebrewPoints
GlobalOptionFilter=UTF8ArabicPoints
```

### Localization Support

Any user-facing field can be localized by appending `_<locale>`:

```ini
Description=King James Version
Description_de=König-Jakob-Übersetzung
Description_fr=Version du Roi Jacques
Description_pt-BR=Versão do Rei Tiago (Brasil)
About=The King James Version...
About_de=Die König-Jakob-Übersetzung...
```

### Copyright and Licensing Fields

```ini
Copyright=Copyright Holder Name
CopyrightHolder=Organization Name
CopyrightDate=2024
CopyrightNotes=Additional copyright information
CopyrightContactName=John Doe
CopyrightContactEmail=john@example.org
CopyrightContactAddress=123 Main St, City, Country
DistributionLicense=Public Domain
DistributionNotes=May be freely distributed
ShortPromo=One-line promotional text
ShortCopyright=© 2024 Copyright Holder
```

### Example Complete .conf File

```ini
[KJV]
DataPath=./modules/texts/ztext/kjv/
ModDrv=zText
Encoding=UTF-8
SourceType=OSIS
BlockType=BOOK
CompressType=ZIP
Lang=en
Versification=KJV
Description=King James Version (1769)
About=The King James Version of the Bible was commissioned in 1604\par\
and completed in 1611 by the Church of England. This electronic\par\
edition is the 1769 revision by Benjamin Blayney at Oxford.
DistributionLicense=Public Domain
TextSource=CrossWire Bible Society
Copyright=Public Domain
CopyrightDate=1769
CopyrightHolder=Public Domain
ShortCopyright=Public Domain
Category=Biblical Texts
LCSH=Bible. English
Version=2.5
History_1.0=Initial release (2000-01-15)
History_2.0=Added Strong's numbers (2005-03-20)
History_2.5=Corrected typographical errors (2010-06-12)
Feature=StrongsNumbers
GlobalOptionFilter=OSISStrongs
GlobalOptionFilter=OSISFootnotes
GlobalOptionFilter=OSISHeadings
GlobalOptionFilter=OSISRedLetterWords
MinimumVersion=1.5.9
```

---

## Module Types and Drivers

### Module Type Constants

In SWORD applications, modules are categorized into four main types:

| Type Constant | String Value | Description |
|--------------|--------------|-------------|
| `TEXT_MODS` | `"Biblical Texts"` | Bible translations |
| `COMM_MODS` | `"Commentaries"` | Bible commentaries |
| `DICT_MODS` | `"Lexicons / Dictionaries"` | Dictionaries, lexicons, glossaries |
| `BOOK_MODS` | `"Generic Books"` | General books, devotionals |

### Module Drivers (ModDrv)

The `ModDrv` field specifies the driver that handles the module's binary format.

#### Biblical Texts

| Driver | Description | Compression | Notes |
|--------|-------------|-------------|-------|
| **RawText** | Uncompressed text | None | Fastest access |
| **RawText4** | Uncompressed text (32-bit) | None | Supports larger modules |
| **zText** | Compressed text | Yes | 16-bit offsets |
| **zText4** | Compressed text (32-bit) | Yes | Most common for Bibles |

#### Commentaries

| Driver | Description | Compression | Notes |
|--------|-------------|-------------|-------|
| **RawCom** | Uncompressed commentary | None | |
| **RawCom4** | Uncompressed commentary (32-bit) | None | |
| **zCom** | Compressed commentary | Yes | 16-bit offsets |
| **zCom4** | Compressed commentary (32-bit) | Yes | Recommended |
| **HREFCom** | HTML/HREF commentary | No | Web-based |
| **RawFiles** | Individual files per entry | No | File-based storage |

#### Dictionaries and Lexicons

| Driver | Description | Compression | Notes |
|--------|-------------|-------------|-------|
| **RawLD** | Uncompressed lexicon/dictionary | None | |
| **RawLD4** | Uncompressed lexicon (32-bit) | None | |
| **zLD** | Compressed lexicon/dictionary | Yes | Recommended |

#### General Books

| Driver | Description | Compression | Notes |
|--------|-------------|-------------|-------|
| **RawGenBook** | Uncompressed general book | No | Tree-structured |

---

## Binary File Formats

### IMPORTANT DISCLAIMER

The binary formats described below are **reverse-engineered and unofficial**. The CrossWire Bible Society does not officially document these formats and they **may change without notice**.

### zText Format (Compressed Biblical Texts)

This is the most common format for Bible modules. It consists of three files per testament:

#### Files for Old Testament:
- **ot.bzv** - Verse index
- **ot.bzs** - Buffer (block) index
- **ot.bzz** - Compressed text data

#### Files for New Testament:
- **nt.bzv** - Verse index
- **nt.bzs** - Buffer (block) index
- **nt.bzz** - Compressed text data

#### ot.bzv / nt.bzv (Verse Index)

Maps each verse to its location in compressed buffers.

**Structure (10 bytes per verse):**
```
Offset  Size  Type      Description
------  ----  --------  -----------
0       4     uint32    buffer_num (which compressed block)
4       4     uint32    verse_start (character offset in decompressed block)
8       2     uint16    verse_len (length of verse in characters)
```

**For zText4 variant (12 bytes per verse):**
```
Offset  Size  Type      Description
------  ----  --------  -----------
0       4     uint32    buffer_num
4       4     uint32    verse_start
8       4     uint32    verse_len (32-bit length)
```

**Verse Indexing:**
- Record for verse with index `x` starts at byte `10*x` (or `12*x` for zText4)
- Verses are numbered sequentially following the versification system
- Index includes all verses in the testament

#### ot.bzs / nt.bzs (Buffer Index)

Maps compressed buffers to their location in the .bzz file.

**Structure (12 bytes per buffer):**
```
Offset  Size  Type      Description
------  ----  --------  -----------
0       4     uint32    offset (byte position in .bzz file)
4       4     uint32    size (compressed size in bytes)
8       4     uint32    uc_size (uncompressed size in bytes)
```

**Buffer Indexing:**
- Record for buffer `buffer_num` starts at byte `12*buffer_num`
- Buffers are compression blocks containing multiple verses

#### ot.bzz / nt.bzz (Compressed Data)

Contains the actual compressed text data.

**Access Algorithm:**
1. Read verse record from `.bzv` to get `buffer_num`, `verse_start`, `verse_len`
2. Read buffer record from `.bzs` to get `offset`, `size`, `uc_size`
3. Read `size` bytes from `.bzz` starting at `offset`
4. Decompress the data (using zlib or LZSS)
5. Extract verse text starting at character `verse_start` for `verse_len` characters

### RawText Format (Uncompressed Biblical Texts)

#### Files:
- **ot** - Old Testament text (plain text)
- **nt** - New Testament text (plain text)
- **ot.vss** - Old Testament verse index (6 bytes per verse)
- **nt.vss** - New Testament verse index (6 bytes per verse)

#### Verse Index Structure (6 bytes per verse):
```
Offset  Size  Type      Description
------  ----  --------  -----------
0       4     uint32    verse_start (byte offset in text file)
4       2     uint16    verse_len (length in bytes)
```

**Access Algorithm:**
1. Read verse record from `.vss` to get `verse_start`, `verse_len`
2. Seek to byte `verse_start` in text file (ot or nt)
3. Read `verse_len` bytes

### RawLD Format (Dictionary/Lexicon)

#### Files:
- **dict.dat** - Dictionary entry data
- **dict.idx** - Dictionary entry index

**Note:** The exact binary structure is undocumented. The index maps entry keys (words/headwords) to positions in the .dat file.

### zLD Format (Compressed Dictionary/Lexicon)

Similar to RawLD but with compression. Details are implementation-specific.

### RawGenBook Format (General Books)

Uses a tree-key structure for hierarchical content (books with chapters, sections, subsections). Binary format details are undocumented.

---

## Versification Systems

### Overview

Different Bible traditions number verses differently. SWORD supports multiple versification systems to handle these differences.

### Available Systems

| System | Description | Usage |
|--------|-------------|-------|
| **KJV** | King James Version | Protestant English Bibles, most common |
| **KJVA** | KJV + Apocrypha | Protestant with deuterocanonical books |
| **NRSV** | New Revised Standard Version | Modern Protestant/Ecumenical |
| **NRSVA** | NRSV + Apocrypha | NRSV with deuterocanonical books |
| **Catholic** | Catholic Bible (10 chapters in Esther) | Most Catholic Bibles |
| **Catholic2** | Catholic Bible (16 chapters in Esther) | Some Catholic Bibles |
| **Synodal** | Russian Synodal | Russian Orthodox |
| **MT** | Masoretic Text | Hebrew Bible |
| **Leningrad** | Leningrad Codex | Hebrew scholarly editions |
| **LXX** | Septuagint | Greek Old Testament |
| **Luther** | Luther Bible | German Lutheran tradition |

### Versification Differences

Versification systems differ in:
- **Number of books** (with/without Apocrypha)
- **Book order** (Chronicles placement, deuterocanonical ordering)
- **Chapter counts** (Psalms, Esther)
- **Verse counts per chapter** (verse splitting/merging)
- **Verse numbering** (headings as verse 1, etc.)

### Verse Mapping

SWORD uses **mapping files** to convert references between versification systems:
- Each versification can map to **KJVA as a pivot**
- Applications convert: `Source → KJVA → Target`
- Example: `Catholic:Ps.13:1 → KJVA:Ps.14:1 → NRSV:Ps.14:1`

**Specification in .conf:**
```ini
Versification=Catholic
```

---

## Encoding and Localization

### Character Encoding

**Recommended:** `UTF-8`

**Supported Encodings:**
- **UTF-8** - Universal, preferred for all new modules
- **UTF-16** - 16-bit Unicode
- **Latin-1** - Western European (legacy)
- **CP1252** - Windows Western European (legacy)
- **SCSU** - Standard Compression Scheme for Unicode (rarely used)

**Specification:**
```ini
Encoding=UTF-8
```

### Language Codes

Use **BCP 47** language codes:

**Examples:**
- `en` - English
- `en-US` - English (United States)
- `en-GB` - English (United Kingdom)
- `de` - German
- `fr` - French
- `es` - Spanish
- `pt` - Portuguese
- `pt-BR` - Portuguese (Brazil)
- `zh` - Chinese
- `zh-CN` - Chinese (China)
- `zh-TW` - Chinese (Taiwan)
- `he` - Hebrew
- `el` - Greek (modern)
- `grc` - Greek (ancient)

**Specification:**
```ini
Lang=en
```

### Text Direction

For languages with different text directions:

```ini
Direction=RtoL    # Right-to-left (Hebrew, Arabic)
Direction=LtoR    # Left-to-right (English, most languages)
Direction=BiDi    # Bidirectional (mixed)
```

### RTF Formatting in .conf Files

Limited RTF markup is supported in fields like `About`:

| Tag | Description |
|-----|-------------|
| `\par` | Paragraph break |
| `\pard` | Reset paragraph formatting |
| `\qc` | Center text |
| `\u####?` | Unicode character (#### = decimal code point) |

**Example:**
```ini
About=The King James Version was commissioned in 1604.\par\
\par\
It represents the culmination of earlier English translations.
```

---

## Compression Methods

### BlockType (Compression Granularity)

Determines how text is divided into compression blocks:

| Value | Constant | Description | Use Case |
|-------|----------|-------------|----------|
| **1** | `VERSE` | Each verse compressed separately | Very large commentaries with huge entries |
| **3** | `CHAPTER` | Each chapter compressed as a block | Large commentaries |
| **4** | `BOOK` | Each book compressed as a block | Bibles (default) |

**Tradeoff:**
- **Larger blocks** = Better compression, slower access
- **Smaller blocks** = Worse compression, faster access

**Specification:**
```ini
BlockType=BOOK
```

### CompressType (Compression Algorithm)

| Value | Algorithm | Description |
|-------|-----------|-------------|
| **1** | `LZSS` | Lempel-Ziv-Storer-Szymanski | Default, older method |
| **2** | `ZIP` | zlib/DEFLATE | Preferred, better compression |

**Specification:**
```ini
CompressType=ZIP
```

### Creating Compressed Modules

Use the `mod2zmod` utility:

```bash
mod2zmod <source_module> <output_path> [blockType] [compressType]
```

**Examples:**
```bash
# Compress Bible with book-level blocks using ZIP
mod2zmod kjv ./modules/texts/ztext/kjv/ 4 2

# Compress commentary with chapter-level blocks using ZIP
mod2zmod strongscomm ./modules/comments/zcom/strongscomm/ 3 2

# Compress large commentary with verse-level blocks
mod2zmod huge_commentary ./modules/comments/zcom/huge_commentary/ 1 2
```

---

## How Applications Should Work with Modules

### Recommended Approach: Use the SWORD API

**DO:**
- ✅ Use the official **SWORD C++ library** or **JSword (Java)** API
- ✅ Initialize `SWMgr` (module manager) to load modules
- ✅ Access modules through `SWModule` objects
- ✅ Use `VerseKey` for navigating biblical texts
- ✅ Use module's `getConfigEntry()` for .conf fields
- ✅ Respect module licensing and copyright information

**DON'T:**
- ❌ Parse binary files directly
- ❌ Assume binary format stability
- ❌ Hard-code offsets or structure sizes
- ❌ Ignore versification systems
- ❌ Assume all modules use the same format

### Basic API Usage Pattern (C++)

```cpp
#include <swmgr.h>
#include <swmodule.h>
#include <versekey.h>

using namespace sword;

// 1. Initialize module manager
SWMgr manager;  // Automatically finds modules

// 2. Get a module
SWModule *bible = manager.getModule("KJV");
if (!bible) {
    // Handle module not found
    return;
}

// 3. Set verse key
bible->setKey("John 3:16");

// 4. Get text
const char *text = bible->renderText();

// 5. Get configuration
const char *description = bible->getConfigEntry("Description");
const char *license = bible->getConfigEntry("DistributionLicense");

// 6. Navigate
bible->increment(1);  // Next verse
const char *nextVerse = bible->renderText();
```

### Module Discovery

```cpp
// Enumerate all available modules
ModMap::iterator it;
for (it = manager.Modules.begin(); it != manager.Modules.end(); ++it) {
    SWModule *mod = it->second;
    const char *name = mod->getName();
    const char *type = mod->getType();  // "Biblical Texts", "Commentaries", etc.
    const char *lang = mod->getConfigEntry("Lang");

    // Categorize by type
    if (!strcmp(type, "Biblical Texts")) {
        // Add to Bible list
    } else if (!strcmp(type, "Commentaries")) {
        // Add to commentary list
    }
    // ... etc
}
```

### Module Installation

Applications typically use **InstallMgr** for module installation:

```cpp
#include <installmgr.h>

InstallMgr installMgr;

// 1. Refresh remote sources
installMgr.refreshRemoteSource("CrossWire");

// 2. Install a module
int result = installMgr.installModule(
    manager,           // Module manager
    "~/.sword",        // Install path
    "KJV",            // Module name
    "CrossWire"       // Source name
);
```

### Handling Filters and Options

```cpp
// Enable/disable display options
bible->setOption("Strong's Numbers", "On");
bible->setOption("Morphological Tags", "On");
bible->setOption("Footnotes", "Off");
bible->setOption("Headings", "On");
bible->setOption("Red Letter Words", "On");

// Get filtered/rendered text
const char *renderedText = bible->renderText();
```

### Search Functionality

```cpp
#include <listkey.h>

// Search for a term
ListKey results = bible->search("grace", -2);  // -2 = multiword search

// Iterate results
for (results = TOP; !results.popError(); results++) {
    bible->setKey(results);
    const char *verseText = bible->renderText();
    // Display result
}
```

### Module Storage by Applications

**Configuration Storage:**
- Store user's **module preferences** (active modules, enabled filters)
- Store **module list cache** to avoid repeated filesystem scans
- Store **last used verse/location** per module
- **DO NOT** copy or modify module binary files

**Example Settings Structure (from Xiphos):**
```c
struct SETTINGS {
    char *MainWindowModule;     // Active Bible module
    char *CommWindowModule;     // Active commentary
    char *DictWindowModule;     // Active dictionary
    char *currentverse;         // Current Bible reference

    // Display options
    int strongs;                // Show Strong's numbers
    int showversenum;          // Show verse numbers
    int versestyle;            // Verse vs. paragraph style

    // Module-specific settings
    char *parallel_list[];     // Parallel view modules
    // ... etc
};
```

---

## Module Creation Process

### Overview

Creating a SWORD module involves:
1. Preparing source text
2. Converting to SWORD format
3. Creating configuration file
4. Testing
5. (Optional) Compression
6. (Optional) Submission to CrossWire

### Supported Input Formats

| Format | Type | Description | Tool |
|--------|------|-------------|------|
| **OSIS** | XML | Open Scripture Information Standard (preferred) | `osis2mod` |
| **TEI** | XML | Text Encoding Initiative (for dictionaries) | `tei2mod` |
| **VPL** | Text | Verse-Per-Line format | `vpl2mod` |
| **IMP** | Text | Import format (simple text-based) | `imp2vs`, `imp2ld`, `imp2gbs` |
| **ThML** | XML | Theological Markup Language (legacy) | (varies) |
| **GBF** | Text | General Bible Format (legacy) | (varies) |

### IMP Format (Simple Text-Based)

The IMP format is useful for creating modules from plain text sources.

#### IMP Format for Bibles (imp2vs)

```
$$$Genesis 1:1
In the beginning God created the heaven and the earth.
$$$Genesis 1:2
And the earth was without form, and void...
$$$John 3:16
For God so loved the world...
```

**Structure:**
- Each entry starts with `$$$<reference>`
- Reference format: `<Book> <Chapter>:<Verse>`
- Text follows on subsequent lines
- Blank lines separate entries

#### IMP Format for Dictionaries (imp2ld)

```
$$$Atonement
The act of making amends or reparation...

$$$Baptism
A Christian sacrament of admission...

$$$Church
The body of Christian believers...
```

**Structure:**
- Each entry starts with `$$$<headword>`
- Definition follows on subsequent lines
- Blank lines separate entries

### Creation Tools

#### osis2mod (Recommended for Bibles/Commentaries)

```bash
osis2mod <output_path> <osis_file> [options]

# Options:
# -z : Create compressed module (zText)
# -b <2|3|4> : Block type (2=verse, 3=chapter, 4=book)
# -c <1|2> : Compress type (1=LZSS, 2=ZIP)
# -v <versification> : Versification system

# Example:
osis2mod ./modules/texts/ztext/mybible/ mybible.osis.xml -z -b 4 -c 2 -v KJV
```

#### imp2vs (Bibles/Commentaries from IMP)

```bash
imp2vs <imp_file> [options]

# Options:
# -z : Create compressed module
# -o <output_path> : Output directory

# Example:
imp2vs mybible.imp -z -o ./modules/texts/ztext/mybible/
```

#### imp2ld (Dictionaries from IMP)

```bash
imp2ld <imp_file> [options]

# Options:
# -z : Create compressed module
# -o <output_path> : Output directory

# Example:
imp2ld mydict.imp -z -o ./modules/lexdict/zld/mydict/
```

#### mod2zmod (Compress Existing Module)

```bash
mod2zmod <source_module> <output_path> [blockType] [compressType]

# Example:
mod2zmod mybible ./modules/texts/ztext/mybible/ 4 2
```

#### mkfastmod (Create Search Index)

```bash
mkfastmod <module_name> [options]

# Example:
mkfastmod KJV
```

### Step-by-Step: Creating a Bible Module

**1. Prepare Source Text**
- Convert to UTF-8 encoding
- Mark up with OSIS tags (recommended) or use VPL/IMP format
- Validate XML if using OSIS

**2. Create Module with osis2mod**
```bash
osis2mod ./modules/texts/ztext/mybible/ mybible.osis.xml -z -b 4 -c 2 -v KJV
```

**3. Create .conf File**

Create `~/.sword/mods.d/mybible.conf`:
```ini
[MyBible]
DataPath=./modules/texts/ztext/mybible/
ModDrv=zText4
Encoding=UTF-8
SourceType=OSIS
BlockType=BOOK
CompressType=ZIP
Lang=en
Versification=KJV
Description=My Bible Translation
About=This is my custom Bible translation...
DistributionLicense=Public Domain
TextSource=My Organization
Copyright=Public Domain
CopyrightDate=2024
Version=1.0
```

**4. Test Module**

```bash
# Test with command-line tool
diatheke -b MyBible -k "John 3:16"

# Or test in Xiphos or another SWORD frontend
xiphos
```

**5. Create Search Index**
```bash
mkfastmod MyBible
```

### Step-by-Step: Creating a Dictionary Module

**1. Prepare IMP File** (`mydict.imp`)
```
$$$Atonement
The act of making amends or reparation for sin or wrongdoing.

$$$Baptism
A Christian sacrament of admission and adoption, involving water.
```

**2. Create Module**
```bash
imp2ld mydict.imp -z -o ~/.sword/modules/lexdict/zld/mydict/
```

**3. Create .conf File** (`~/.sword/mods.d/mydict.conf`)
```ini
[MyDict]
DataPath=./modules/lexdict/zld/mydict/
ModDrv=zLD
Encoding=UTF-8
SourceType=Plaintext
CompressType=ZIP
Lang=en
Description=My Custom Dictionary
About=A custom dictionary for Bible study...
DistributionLicense=CC BY-SA 4.0
TextSource=My Organization
Copyright=© 2024 My Organization
Version=1.0
Category=Glossaries
```

**4. Test**
```bash
diatheke -b MyDict -k "Baptism"
```

### Conversion Workflow

```
Source Text
    ↓
[Choose Format]
    ├─→ OSIS XML ──→ osis2mod ──→ Compressed Module
    ├─→ TEI XML ───→ tei2mod ───→ Compressed Module
    ├─→ VPL Text ──→ vpl2mod ───→ Uncompressed → mod2zmod → Compressed
    └─→ IMP Text ──→ imp2vs/ld ─→ Uncompressed → mod2zmod → Compressed
    ↓
Add .conf file
    ↓
Test module
    ↓
Create search index (mkfastmod)
    ↓
(Optional) Submit to CrossWire
```

### Export Existing Modules

```bash
# Export to IMP format for editing
mod2imp <module_name> -o output.imp

# Edit output.imp as needed

# Re-import
imp2vs output.imp -z -o ./modules/texts/ztext/mymodule/
```

---

## Best Practices for Application Developers

1. **Always use the SWORD API** - Never parse binary files directly
2. **Check module licensing** - Respect `DistributionLicense` and copyright
3. **Support multiple versifications** - Don't assume KJV versification
4. **Handle missing modules gracefully** - Check module existence before access
5. **Cache module metadata** - Avoid repeated .conf file parsing
6. **Provide module installation UI** - Use InstallMgr for downloading modules
7. **Test with various module types** - RawText, zText, dictionaries, etc.
8. **Support localization** - Read localized .conf fields when available
9. **Handle encoding properly** - Always use UTF-8 when possible
10. **Implement user preferences** - Save active modules, filters, positions

---

## References and Resources

### Official CrossWire Resources

- **SWORD Project Website:** https://crosswire.org/sword/
- **SWORD API Documentation:** http://www.crosswire.org/sword/apiref/
- **CrossWire Wiki:** https://wiki.crosswire.org/
  - File Formats: https://wiki.crosswire.org/File_Formats
  - DevTools: https://wiki.crosswire.org/DevTools:Modules
  - .conf Files: https://wiki.crosswire.org/DevTools:conf_Files
  - Writing .conf Files: https://wiki.crosswire.org/Tutorial:Writing_Conf_files

### Module Repositories

- **Official CrossWire Repository:** https://crosswire.org/sword/modules/
- **Module Installation Instructions:** https://www.crosswire.org/sword/docs/moduleinstall.jsp

### SWORD Implementations

- **SWORD Library (C++):** https://crosswire.org/sword/develop/
- **JSword (Java):** http://www.crosswire.org/jsword/
- **Xiphos (GTK/C):** https://xiphos.org/
- **BibleTime (Qt/C++):** https://bibletime.info/

---

## Appendix: Common Module Commands

### Listing Modules
```bash
installmgr -l  # List local modules
installmgr -r CrossWire  # Refresh remote source
installmgr -rl CrossWire  # List remote modules
```

### Installing Modules
```bash
installmgr -ri CrossWire KJV  # Install KJV from CrossWire
```

### Testing Modules
```bash
diatheke -b KJV -k "John 3:16"  # Display verse
diatheke -b KJV -k "John 3:16" -o n  # No markup
diatheke -b StrongsGreek -k "3754"  # Dictionary lookup
```

### Module Information
```bash
# View module info
cat ~/.sword/mods.d/kjv.conf

# Search module
diatheke -b KJV -s phrase -k "grace faith"
```

---

## Document Version

- **Version:** 1.0
- **Date:** 2025-10-07
- **Author:** Generated for Xiphos project documentation
- **Status:** Community documentation (unofficial)

**Disclaimer:** This document is based on publicly available information, community reverse-engineering, and examination of the SWORD library source code. The binary format specifications are unofficial and may be incomplete or subject to change. Always use the official SWORD API for production applications.
