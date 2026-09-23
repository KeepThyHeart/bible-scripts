/**
 * sword_common.h
 *
 * Shared functionality for SWORD module converters.
 * Contains common functions used by sword2bible, sword2commentary, sword2dictionary,
 * sword2book and sword2devotional.
 *
 * MODULE FORMAT
 * -------------
 * These helpers implement the pieces of the module format that every converter
 * needs. The format is defined by the Bible repo: see
 * packages/core/docs/features/module-format.md and packages/core/sql/schemas/.
 *
 *  - Canonical book mapping: OSIS book abbreviation -> 1..66, with
 *    non-canonical books rejected rather than silently renumbered.
 *  - Text representation: OSIS -> clean UTF-8 text + structured spans.
 *  - Identity / provenance: module_uuid, content_sha256, SPDX licence
 *    mapping, RTF artefact stripping.
 *  - Word offsets: 0-based, inclusive, everywhere.
 */

#ifndef SWORD_COMMON_H
#define SWORD_COMMON_H

#include <cstdint>
#include <string>
#include <vector>
#include <filesystem>
#include <map>
#include <functional>

// Forward declarations
namespace sword {
    class SWMgr;
    class SWModule;
}

namespace fs = std::filesystem;

namespace SwordCommon {

    /** The module format version emitted by these converters. */
    extern const char* const FORMAT_VERSION;   // "0.2"
    /** The canon every module produced here conforms to. */
    extern const char* const CANON;            // "protestant-66"
    /** The versification every module produced here conforms to. */
    extern const char* const VERSIFICATION;    // "kjv-english"

    /**
     * Module filter callback type
     * Returns true if the module should be selected
     */
    using ModuleFilterFunc = std::function<bool(sword::SWModule*, const fs::path&)>;

    /**
     * Extract ZIP file to directory
     */
    bool extractZipFile(const std::string& zipPath, const fs::path& outputDir);

    /**
     * Create a temporary directory for extraction
     */
    fs::path createTempDirectory(const std::string& prefix);

    /**
     * Initialize SWORD library and load module matching filter
     *
     * @param modulePath Path to extracted module files
     * @param mgr Output: SWORD manager instance
     * @param module Output: Selected module
     * @param filter Callback to filter which module to select
     * @return true if module found and loaded
     */
    bool initializeSWORD(const fs::path& modulePath,
                         sword::SWMgr** mgr,
                         sword::SWModule** module,
                         ModuleFilterFunc filter);

    /**
     * Parse module configuration file (.conf) and extract key-value pairs
     *
     * @param tempDir Directory containing extracted module
     * @param moduleName Name of the module to find config for
     * @return Map of config keys to values
     */
    std::map<std::string, std::string> parseModuleConfig(const fs::path& tempDir,
                                                          const std::string& moduleName);

    /**
     * Set standard SQLite pragmas for performance
     */
    bool setSQLitePragmas(void* db, int cacheSizeKB = 32);

    // ------------------------------------------------------------------
    // Text representation
    // ------------------------------------------------------------------

    /**
     * A structured span.
     *
     * A span is a DATA RECORD naming a range of words — not markup. `start` and
     * `end` are word indices into the clean text, **0-based and inclusive**
     * (word-offset convention).
     *
     * Recognised types and their USFM equivalents:
     *   divine_name     \nd   YHWH rendered LORD/GOD
     *   supplied        \add  translator-supplied words
     *   words_of_christ \wj   red letter
     *   emphasis        \em   genuine emphasis in the source
     *   quotation       \qt   OT quotation in NT; `ref` carries the source verse_id
     *   transliteration \tl
     */
    struct FormatSpan {
        std::string type;
        int start = 0;
        int end = 0;
        int64_t ref = 0;   // source verse_id for 'quotation'; 0 = absent
    };

    /**
     * Block-level formatting for a verse.
     *
     *   paragraph_start  \p
     *   poetry_level     \q1..\q3   (0 = prose)
     *   heading          \d / \s    (e.g. Psalm superscriptions)
     *   selah            \qs
     */
    struct BlockInfo {
        bool paragraphStart = false;
        int  poetryLevel = 0;
        std::string heading;
        bool selah = false;

        bool isEmpty() const {
            return !paragraphStart && poetryLevel == 0 && heading.empty() && !selah;
        }
    };

    /**
     * One <w> element from the OSIS source, anchored to real word offsets in
     * the clean text (0-based, inclusive).
     */
    struct InterlinearRecord {
        int startWord = 0;
        int endWord = 0;
        std::string strongsNumber;   // primary Strong's number, e.g. "G2316"
        std::string allStrongs;      // every Strong's number on the element, space separated
        std::string morphology;      // scheme prefixes stripped ("robinson:N-NSM" -> "N-NSM")
        std::string originalWord;    // lemma.TR:/lemma: value when present
        std::string gloss;           // the clean-text words this element covers
        bool hasGloss = false;       // false for self-closing <w/> (no English equivalent)
    };

    /**
     * Provenance for a verse whose text came from a DIFFERENT verse number in
     * the source module.
     *
     * Some translations follow a variant verse division — e.g. Rev 12:18 in the
     * Nestle-Aland tradition is the text the KJV numbers Rev 13:1a, and 3 John
     * 15 is the second half of what the KJV numbers 3 John 14. The converter
     * folds such a verse into its canonical host so no scripture is lost, and
     * records the source's own numbering here so a renderer can still show it.
     *
     * `startWord`/`endWord` are 0-based inclusive offsets into the HOST verse's
     * clean text, delimiting the words that came from this source verse.
     */
    struct SourceVerseRecord {
        int chapter = 0;
        int verse = 0;
        int startWord = 0;
        int endWord = 0;
    };

    /** Result of parsing one verse of OSIS. */
    struct OsisVerseResult {
        std::string text;                  // clean canonical UTF-8, single-spaced, trimmed
        int wordCount = 0;
        BlockInfo block;
        std::vector<FormatSpan> spans;
        std::vector<InterlinearRecord> interlinear;
        std::vector<SourceVerseRecord> sourceVerses;   // empty unless verses were merged

        /**
         * A paragraph marker followed the last word of this verse.
         *
         * AKJV-style sources put the pilcrow at the END of the verse preceding
         * the new paragraph, so it describes the NEXT verse. Only the source
         * document reveals this — the caller is expected to carry the flag
         * forward and set `block.paragraph_start` on the following verse.
         */
        bool trailingParagraphMarker = false;
    };

    /**
     * Parse a single verse of OSIS markup into clean text plus structured spans.
     *
     * Source markers are mapped DIRECTLY to span types; the text never passes
     * through an HTML representation. Footnotes (<note>) are dropped, titles
     * (<title>) are lifted into block.heading, and pilcrows are consumed into
     * block.paragraph_start.
     *
     * The returned text is normalised so that splitting it on single spaces
     * yields exactly `wordCount` words, and word index i of that split is the
     * word addressed by span offset i.
     */
    OsisVerseResult parseOsisVerse(const std::string& osis);

    /**
     * Serialise an OsisVerseResult's block + spans to the `formatting` JSON.
     * Returns an empty string when there is nothing to record (store SQL NULL).
     *
     * Shape: {"v":1,"block":{...},"spans":[{"type":...,"start":..,"end":..}]}
     */
    std::string buildFormattingJson(const OsisVerseResult& result);

    /**
     * Convert an OSIS reference ("Isa.7.14", "Ps.23.1-Ps.23.6") to a verse_id.
     * Returns 0 when the reference cannot be resolved to a canonical verse.
     */
    int64_t osisRefToVerseId(const std::string& osisRef);

    // ------------------------------------------------------------------
    // Canon enforcement
    // ------------------------------------------------------------------

    /**
     * Map an OSIS book abbreviation to its canonical Protestant book number (1..66).
     * Returns 0 for anything outside the 66-book canon (deuterocanon, apocrypha,
     * or an unrecognised abbreviation) so the caller can skip it with a warning
     * instead of silently renumbering every book that follows.
     */
    int canonicalBookNumberFromOsis(const std::string& osisBook);

    /**
     * Extract the book portion of an OSIS reference ("Gen.1.1" -> "Gen").
     */
    std::string osisBookOf(const std::string& osisRef);

    // ------------------------------------------------------------------
    // Identity / provenance
    // ------------------------------------------------------------------

    /** Lowercase hex SHA-256 of the given bytes. */
    std::string sha256Hex(const std::string& data);

    /**
     * Derive a stable module UUID from an identity key.
     *
     * The key must contain only things that identify the WORK, never the
     * revision — so the UUID survives content updates ("stable identity
     * across versions"). Callers pass e.g. "bible:KJV:en".
     *
     * The result is an RFC 9562 version 8 (custom, hash-based) UUID.
     */
    std::string deterministicUuid(const std::string& identityKey);

    /**
     * Strip RTF artefacts that SWORD .conf files embed in About/Copyright
     * fields (\par, \pard, \qc, \line, escaped braces, ...) and normalise
     * the resulting whitespace.
     */
    std::string stripRtfArtifacts(const std::string& text);

    /**
     * Map a SWORD `DistributionLicense` value to an SPDX identifier
     * (or a LicenseRef- id where no SPDX id exists). Returns "" when unknown.
     */
    std::string licenseToSpdx(const std::string& distributionLicense);

    /** Canonical URL for a licence id produced by licenseToSpdx(). "" if none. */
    std::string licenseUrlForSpdx(const std::string& spdxId);

    /**
     * Extract a four-digit year from free text (e.g. "1611-2011" -> 1611).
     * Returns 0 when no plausible year is present.
     */
    int extractYear(const std::string& text);

    /** Primary language subtag of a SWORD/BCP 47 Lang tag, lowercased ("ar-Arab-EG" -> "ar"). */
    std::string primaryLanguage(const std::string& langTag);

    /**
     * Right-to-left text? The .conf Direction key (RtoL/LtoR) decides when set;
     * otherwise the language ("he", "ar", "fa", ...) or a right-to-left script
     * subtag ("ku-Arab-IQ").
     */
    bool isRightToLeft(const std::string& langTag, const std::string& direction);

    /** True when the bytes are well-formed UTF-8. */
    bool isValidUtf8(const std::string& s);

    /**
     * Transcode Latin-1 to UTF-8, reading 0x80-0x9F as Windows-1252 (curly
     * quotes, dashes), which is what SWORD's Latin-1 modules actually carry.
     */
    std::string latin1ToUtf8(const std::string& s);

    /**
     * Keep well-formed UTF-8 sequences and read every stray byte as
     * Windows-1252: for modules that declare UTF-8 but carry some Latin-1
     * bytes (DanDetteBiblen), where transcoding the whole entry would
     * double-encode the characters that are already correct.
     */
    std::string repairUtf8(const std::string& s);

    /**
     * The identity + provenance block, derived once from a SWORD .conf.
     *
     * Every converter fills this the same way so that `module_uuid`,
     * `license_spdx` and friends mean the same thing in every module type.
     * Values are already RTF-stripped — the shipped KJV's `description` full of
     * literal "\par" escapes is exactly what this avoids reproducing.
     */
    struct ModuleIdentity {
        std::string uuid;
        std::string abbreviation;
        std::string fullName;
        std::string author;
        std::string publisher;
        std::string languageCode = "en";
        int yearPublished = 0;
        std::string copyright;
        std::string licenseSpdx;
        std::string licenseUrl;
        std::string sourceUrl;
        std::string description;
        std::string contentVersion;
        std::string metadataJson;
    };

    /**
     * Derive the identity block from a parsed .conf.
     *
     * @param config      output of parseModuleConfig()
     * @param moduleType  'commentary' | 'dictionary' | 'book' | 'devotional' | 'bible'
     * @param moduleName  the SWORD module name (used for the UUID and as a fallback)
     */
    ModuleIdentity deriveModuleIdentity(const std::map<std::string, std::string>& config,
                                        const std::string& moduleType,
                                        const std::string& moduleName);

    /** Escape a string for embedding in a JSON string literal. */
    std::string jsonEscape(const std::string& str);

    // ------------------------------------------------------------------
    // HTML helpers (for converters that read rendered HTML rather than raw
    // OSIS)
    // ------------------------------------------------------------------

    /**
     * Text range for formatting metadata (WORD-BASED INDICES, 0-based inclusive)
     */
    struct TextRange {
        int start;
        int end;
    };

    /**
     * Result of HTML markup parsing
     */
    struct HtmlParseResult {
        std::string cleanText;                    // Text with all tags stripped
        std::vector<TextRange> wordsOfChrist;     // Ranges of red-letter text
        std::vector<TextRange> addedWords;        // Ranges of translator-supplied words (italic)
        std::vector<TextRange> bold;              // Ranges of bold text
        std::vector<TextRange> underline;         // Ranges of underlined text
    };

    /**
     * Parse HTML/OSIS markup and extract formatting metadata.
     *
     * Not used for Bible text: sword2bible maps OSIS markers directly to spans
     * via parseOsisVerse(). For callers that only have rendered HTML available.
     */
    HtmlParseResult parseHtmlMarkup(const std::string& html);

    /**
     * Strip HTML/OSIS markup from text (simple version)
     */
    std::string stripMarkup(const std::string& text);

    /**
     * Strip markup and normalise whitespace to single spaces, trimmed.
     * This is what "clean canonical UTF-8" means for non-Bible content.
     */
    std::string stripMarkupClean(const std::string& text);

    /**
     * Count words in text
     */
    int countWords(const std::string& text);

    /**
     * Calculate verse ID from book, chapter, verse
     * verse_id = (book * 1000000) + (chapter * 1000) + verse
     */
    int64_t calculateVerseId(int book, int chapter, int verse);

    /**
     * Parse verse ID into book, chapter, verse
     * Returns false if invalid verse ID
     */
    bool parseVerseId(int64_t verseId, int& book, int& chapter, int& verse);

    /**
     * Check if a file or directory exists
     */
    bool fileExists(const fs::path& path);

    /**
     * Trim whitespace from string
     */
    std::string trim(const std::string& str);

    /**
     * Convert string to lowercase
     */
    std::string toLower(const std::string& str);

    /**
     * Check if module data path exists (handles both directory and file-based paths)
     */
    bool moduleDataExists(const fs::path& modulePath, const std::string& dataPath);

    // ------------------------------------------------------------------
    // Unified verse linking
    // ------------------------------------------------------------------

    /**
     * Schema DDL (module_info, verse_link, module_feature,
     * compression_dictionary and each type's own content tables) is no
     * longer hand-copied here — see schema_bridge.h's loadRepoSchema(),
     * which loads it from the Bible repo the same way
     * scripts/lib/schema.js does for the Node importers (task 0035 / design
     * §6.1). Each converter's createDatabase() calls it once with its own
     * type's schema file name ("BibleTranslation.sql", "Commentary.sql", ...).
     */

    /**
     * Insert one verse_link row.
     *
     * @param db            sqlite3* handle (void* to keep sqlite3.h out of this header)
     * @param sourceType    'commentary_entry' | 'book_section' | 'dictionary_entry' | ...
     * @param sourceId      primary key of the owning row
     * @param verseIdStart  inclusive
     * @param verseIdEnd    inclusive; pass 0 for "single verse" (stored as end = start)
     * @param linkType      'reference' | 'annotation' | 'primary_passage' | 'cross_reference'
     * @param sortOrder     ordering within the source row
     * @param context       optional free text (nullable)
     */
    bool insertVerseLink(void* db,
                         const std::string& sourceType,
                         int64_t sourceId,
                         int64_t verseIdStart,
                         int64_t verseIdEnd,
                         const std::string& linkType,
                         int sortOrder,
                         const std::string& context = "");

} // namespace SwordCommon

#endif // SWORD_COMMON_H
