/**
 * sword2bible - Convert SWORD Bible modules to SQLite format
 *
 * Converts CrossWire SWORD Bible module ZIP files into our SQLite database
 * format. The format is defined by the Bible repo: see
 * packages/core/docs/features/module-format.md and
 * packages/core/sql/schemas/initial/BibleTranslation.sql.
 *
 * Highlights:
 *   - Canon enforcement: books are resolved through their OSIS name to a
 *     canonical 1..66 number. Anything outside the Protestant canon is
 *     SKIPPED WITH A WARNING rather than silently renumbering everything
 *     that follows.
 *   - bible_verse.text is clean canonical UTF-8 — no markup, no pilcrows,
 *     no leading whitespace. Presentation lives in bible_verse.formatting
 *     as structured spans.
 *   - verse_link is the single content->verse linking table.
 *   - module_info carries the full identity/provenance block.
 *   - All word offsets are 0-based and inclusive.
 *   - FTS5 external-content triggers use the 'delete' command form.
 *
 * Usage: sword2bible --input <module.zip> --output <bible_xxx.db>
 *
 * Compilation: See Makefile
 * Dependencies: libsword, libsqlite3, libzip
 */

#include <iostream>
#include <sstream>
#include <fstream>
#include <cctype>
#include <system_error>
#include <string>
#include <vector>
#include <map>
#include <set>
#include <cstdlib>
#include <cstring>
#include <filesystem>

// SWORD library
#include <swmgr.h>
#include <swmodule.h>
#include <versekey.h>
#include <versificationmgr.h>

// SQLite
#include <sqlite3.h>

// Common library
#include "sword_common.h"
#include "schema_bridge.h"
#include "compression.h"
#include "content_digest.h"

namespace fs = std::filesystem;
using namespace sword;

/**
 * Module filter for Bible modules
 */
bool isBibleModule(sword::SWModule* mod, const fs::path& modulePath) {
    std::string moduleType = mod->getType();
    std::string dataPath = mod->getConfigEntry("DataPath") ? mod->getConfigEntry("DataPath") : "";

    // Check if module data actually exists in our temp directory
    bool dataExists = SwordCommon::moduleDataExists(modulePath, dataPath);

    // Accept Biblical Text modules
    return dataExists && moduleType == "Biblical Texts";
}

/**
 * Module metadata extracted from the .conf file (identity block).
 */
struct ModuleInfo {
    std::string uuid;
    std::string abbreviation;
    std::string fullName;
    std::string languageCode;          // bare ISO 639 ("ar")
    std::string languageTag;           // the SWORD Lang tag as given ("ar-Arab-EG")
    int  yearPublished = 0;
    std::string copyright;
    std::string licenseSpdx;
    std::string licenseUrl;
    std::string sourceUrl;
    std::string description;
    std::string publisher;
    bool isOriginalLanguage = false;
    std::string textDirection = "ltr";
    std::string contentVersion;
    std::string sourceVersification;   // what the SWORD module claims
    std::string metadataJson;
};

// ===========================================================================
// Two DISTINCT mechanisms, deliberately kept apart
// ===========================================================================
//
// A module can disagree with the canonical reference space in two unrelated
// ways, and treating them with one mechanism is wrong in both directions:
//
//  (1) VARIANT VERSE DIVISION — a per-verse disagreement about where one verse
//      ends and the next begins. The text is genuine scripture that simply
//      carries a different number: Rev 12:18 in the Nestle-Aland tradition is
//      the sentence the KJV numbers Rev 13:1a; the critical text splits KJV
//      3 John 14 into vv. 14-15. Dropping these loses real text; admitting them
//      under their own number mints a verse_id with no address in the shared
//      reference space, so a note anchored there resolves to nothing.
//      => FOLD into the canonical host verse, recording the source's own
//         numbering in `formatting.source_verses`. Handled by
//         VARIANT_VERSE_MAP below.
//
//  (2) WHOLESALE RENUMBERING — a systematic remap of the whole module: JPS's
//      Hebrew book ordering, CPDV/DRC's deuterocanon inserted at book 17,
//      KJVA's apocrypha at book 40, SPE's Masoretic verse division offset by a
//      whole chapter. These are not per-verse merges and must NOT be papered
//      over with a mapping table — a table would silently launder shifted
//      numbering into the canonical space.
//      => Book-level canon restriction (canonicalBookNumberFromOsis) plus the
//         versification gate in parseModuleInfo(), which refuses a module whose
//         source versification is not KJV-compatible unless the operator
//         explicitly opts in.
//
// Keep these separate. Conflating them either drops legitimate scripture or
// admits misnumbered verses.

/**
 * One variant-verse-division mapping: a verse that the source numbers
 * differently from the canonical (KJV English) versification.
 */
struct VariantVerseMapping {
    int bookNumber;       // canonical book 1..66
    int sourceChapter;    // chapter as the SOURCE numbers it
    int sourceVerse;      // verse as the SOURCE numbers it
    int hostChapter;      // canonical chapter this text belongs to
    int hostVerse;        // canonical verse this text belongs to
    bool prefix;          // true: fragment precedes the host text; false: follows
    const char* note;
};

/**
 * The mapping table. Small, explicit and data-driven on purpose — new
 * divisions get a row here, never a special case in the parsing code.
 */
static const VariantVerseMapping VARIANT_VERSE_MAP[] = {
    // NA/UBS Rev 12:18 ("He stood on the sand of the sea") is KJV Rev 13:1a.
    // Ships in geneva1599, nheb, nhebje, nhebme.
    { 66, 12, 18, 13,  1, true,  "NA/UBS Rev 12:18 = KJV Rev 13:1a" },

    // The critical text splits KJV 3 John 14 into vv. 14-15. Ships in worsley.
    { 64,  1, 15,  1, 14, false, "critical-text 3 John 15 = second half of KJV 3 John 14" },
};

/**
 * Concatenate two parsed verses, shifting every offset in `second` past the
 * words contributed by `first`. Used only by the variant-verse-division path.
 */
static SwordCommon::OsisVerseResult mergeVerseResults(const SwordCommon::OsisVerseResult& first,
                                                      const SwordCommon::OsisVerseResult& second) {
    SwordCommon::OsisVerseResult merged;

    if (first.text.empty())       merged.text = second.text;
    else if (second.text.empty()) merged.text = first.text;
    else                          merged.text = first.text + " " + second.text;

    merged.wordCount = first.wordCount + second.wordCount;

    // Block-level: the first fragment opens the block; poetry takes the deeper
    // level; the first non-empty heading wins.
    merged.block.paragraphStart = first.block.paragraphStart;
    merged.block.poetryLevel = (first.block.poetryLevel > second.block.poetryLevel)
        ? first.block.poetryLevel : second.block.poetryLevel;
    merged.block.heading = first.block.heading.empty() ? second.block.heading : first.block.heading;
    merged.block.selah = first.block.selah || second.block.selah;

    // Only a marker after the LAST word still points at the next verse.
    merged.trailingParagraphMarker = second.trailingParagraphMarker;

    const int shift = first.wordCount;

    merged.spans = first.spans;
    for (SwordCommon::FormatSpan span : second.spans) {
        span.start += shift;
        span.end += shift;
        merged.spans.push_back(span);
    }

    merged.interlinear = first.interlinear;
    for (SwordCommon::InterlinearRecord rec : second.interlinear) {
        rec.startWord += shift;
        rec.endWord += shift;
        merged.interlinear.push_back(rec);
    }

    merged.sourceVerses = first.sourceVerses;
    for (SwordCommon::SourceVerseRecord rec : second.sourceVerses) {
        rec.startWord += shift;
        rec.endWord += shift;
        merged.sourceVerses.push_back(rec);
    }

    return merged;
}

/**
 * Main converter class
 */
class BibleConverter {
private:
    std::string inputZip;
    std::string outputDb;
    fs::path tempDir;
    sqlite3* db = nullptr;
    SWMgr* swordMgr = nullptr;
    SWModule* module = nullptr;
    ModuleInfo moduleInfo;
    std::map<std::string, std::string> swordConfig;

    // Optional book range filter: pairs of (startBook, endBook) inclusive.
    // Empty means convert all books.
    std::vector<std::pair<int,int>> bookRanges;

    // Mechanism 2: books rejected because they are outside the 66-book canon,
    // and the books we did import — keyed by the OSIS name the SOURCE used, which
    // is information no later stage of the pipeline has.
    std::map<std::string, int> skippedBooks;
    std::map<std::string, int> importedBooks;

    // Mechanism 2: refuse a non-KJV versification unless told otherwise.
    bool allowForeignVersification = false;

    // True when the module declares SourceType=OSIS, in which case the raw
    // entry is richer than the rendered output. GBF/ThML modules only yield
    // usable markup after the filter chain runs, so those are parsed from
    // renderText() instead.
    bool preferRawOsis = true;

    // Encoding=UTF-8 in the .conf. Anything else is Latin-1 to SWORD, and
    // entries that are not valid UTF-8 are transcoded (see convertVerses()).
    bool sourceIsUtf8 = true;
    int latin1TranscodedCount = 0;

    // Verses linked to the previous key (one text for a range): not stored again.
    int linkedSkipCount = 0;

    // --versification: the operator's correction when a module's .conf declares
    // one system but its text is numbered in another (engmsb2024eb declares KJV
    // and is numbered like MT). Empty = trust the .conf.
    std::string versificationOverride;
    std::string declaredVersification;

    // Mechanism 1: variant verse divisions awaiting their host verse.
    //
    // `prefix` and `note` are held directly rather than reached through
    // `mapping`, because fragments now arrive from two sources: the hand-written
    // VARIANT_VERSE_MAP, and the libsword av11n translation below. Only the
    // former has a VariantVerseMapping to point at.
    struct VariantFragment {
        SwordCommon::OsisVerseResult parsed;
        bool prefix = false;
        std::string note;
    };
    std::map<int64_t, VariantFragment> pendingFragments;              // host not yet seen
    std::map<int64_t, SwordCommon::OsisVerseResult> hostCache;        // host already written
    int variantMergeCount = 0;

    // ------------------------------------------------------------------
    // Mechanism 3: whole-module versification translation.
    //
    // Mechanisms 1 and 2 handle a handful of known verse-division quirks in
    // otherwise-KJV modules. This handles a module numbered under a DIFFERENT
    // system throughout (NRSV, Vulgate), by mapping every reference into KJV at
    // import so the stored verse ids mean the same thing as every other module's.
    //
    // The mapping data is libsword's own (canon_nrsv.h, canon_vulg.h ...), so
    // this adds no new versification knowledge to this codebase -- it just stops
    // discarding what SWORD already ships.
    // ------------------------------------------------------------------
    const sword::VersificationMgr::System* srcVersification = nullptr;
    const sword::VersificationMgr::System* kjvVersification = nullptr;
    bool translateVersification = false;
    bool useExternalMap = false;   // external JSON map instead of libsword's tables
    int versificationRemapCount = 0;
    int versificationDropCount = 0;

    // A trailing pilcrow on the previous verse marks THIS verse as starting a
    // paragraph. See OsisVerseResult::trailingParagraphMarker.
    bool carryParagraphStart = false;

    // Bible verse text is never compressed (design §3.4: "Ids and structure"
    // is wrong for bible — it's actually "value doesn't clear the bar": a 132
    // byte verse isn't worth a frame). codecName stays "none" for this
    // converter; the member exists so module_info.compression is written
    // uniformly across all five converters via the same code path.
    std::string codecName = SwordCommon::CODEC_NONE;

    // Requirement 5 (task 0035): reuse an existing module_uuid on
    // reconversion instead of minting a fresh one. Empty = mint one as
    // before (deterministicUuid()).
    std::string uuidOverride;

    int interlinearCount = 0;

public:
    BibleConverter(const std::string& input, const std::string& output)
        : inputZip(input), outputDb(output) {}

    void setBookRanges(const std::vector<std::pair<int,int>>& ranges) {
        bookRanges = ranges;
    }

    void setAllowForeignVersification(bool allow) {
        allowForeignVersification = allow;
    }

    void setVersificationOverride(const std::string& name) {
        versificationOverride = name;
    }

    void setUuidOverride(const std::string& uuid) {
        uuidOverride = uuid;
    }

    bool isBookInRange(int bookNumber) const {
        if (bookRanges.empty()) return true; // No filter = all books
        for (const auto& range : bookRanges) {
            if (bookNumber >= range.first && bookNumber <= range.second) return true;
        }
        return false;
    }

    ~BibleConverter() {
        if (db) sqlite3_close(db);
        if (swordMgr) delete swordMgr;
        cleanup();
    }

    /**
     * Main conversion process
     */
    bool convert() {
        try {
            std::cout << "Converting SWORD Bible module to SQLite (format version "
                      << SwordCommon::FORMAT_VERSION << ")..." << std::endl;
            std::cout << "Input:  " << inputZip << std::endl;
            std::cout << "Output: " << outputDb << std::endl << std::endl;

            if (!extractZip()) {
                std::cerr << "Error: Failed to extract ZIP file" << std::endl;
                return false;
            }

            if (!initializeSword()) {
                std::cerr << "Error: Failed to initialize SWORD module" << std::endl;
                return false;
            }

            if (!createDatabase()) {
                std::cerr << "Error: Failed to create database" << std::endl;
                return false;
            }

            if (!parseModuleInfo()) {
                std::cerr << "Error: Failed to parse module info" << std::endl;
                return false;
            }

            if (!insertModuleInfo()) {
                std::cerr << "Error: Failed to insert module info" << std::endl;
                return false;
            }

            if (!convertVerses()) {
                std::cerr << "Error: Failed to convert verses" << std::endl;
                return false;
            }

            if (!finalizeContentHash()) {
                std::cerr << "Error: Failed to record content hash" << std::endl;
                return false;
            }

            reportSkippedBooks();

            std::cout << std::endl << "Conversion completed successfully!" << std::endl;
            std::cout << "Output database: " << outputDb << std::endl;

            return true;

        } catch (const std::exception& e) {
            std::cerr << "Exception: " << e.what() << std::endl;
            return false;
        }
    }

private:
    /**
     * Extract ZIP file to temporary directory
     */
    bool extractZip() {
        std::cout << "[1/6] Extracting ZIP file..." << std::endl;

        tempDir = SwordCommon::createTempDirectory("sword2bible_");

        if (!SwordCommon::extractZipFile(inputZip, tempDir)) {
            std::cerr << "Failed to extract ZIP file" << std::endl;
            return false;
        }

        std::cout << "    Extracted to " << tempDir << std::endl;
        return true;
    }

    /**
     * Initialize SWORD library and load module
     */
    bool initializeSword() {
        std::cout << "[2/6] Initializing SWORD library..." << std::endl;

        if (!SwordCommon::initializeSWORD(tempDir, &swordMgr, &module, isBibleModule)) {
            std::cerr << "Failed to initialize SWORD module" << std::endl;
            return false;
        }

        std::cout << "    Module: " << module->getName() << std::endl;
        std::cout << "    Description: " << module->getDescription() << std::endl;

        return true;
    }

    /**
     * Create the SQLite database schema
     */
    bool createDatabase() {
        std::cout << "[3/6] Creating SQLite database..." << std::endl;

        if (fs::exists(outputDb)) {
            fs::remove(outputDb);
        }

        int rc = sqlite3_open(outputDb.c_str(), &db);
        if (rc != SQLITE_OK) {
            std::cerr << "Cannot open database: " << sqlite3_errmsg(db) << std::endl;
            return false;
        }

        if (!SwordCommon::setSQLitePragmas(db, 32)) { // 32MB cache
            std::cerr << "Failed to set SQLite pragmas" << std::endl;
            return false;
        }

        // Schema (module_info, bible_verse, interlinear_word, module_feature,
        // compression_dictionary, verse_link — no FTS, no text_plain, no
        // redundant indexes: none of that is written here any more, it comes
        // from whatever the Bible repo's own BibleTranslation.sql currently says) is
        // loaded from the Bible repo, not hand-copied (task 0035 / design
        // §6.1 — see schema_bridge.h). The live search index lives in
        // main.db, never in the module file.
        if (!executeSql(SwordCommon::loadRepoSchema("BibleTranslation.sql"))) {
            std::cerr << "Failed to create schema from BibleTranslation.sql" << std::endl;
            return false;
        }

        // schema_version is this repo's own build-provenance bookkeeping,
        // not part of the module format schema — IF NOT EXISTS so it is
        // harmless whether or not BibleTranslation.sql also declares one.
        if (!executeSql(R"SQL(
            CREATE TABLE IF NOT EXISTS schema_version (
                version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_number TEXT NOT NULL,
                applied_date TEXT DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                metadata TEXT
            );
        )SQL")) {
            return false;
        }
        {
            sqlite3_stmt* stmt = nullptr;
            const char* sql = "INSERT INTO schema_version (version_number, notes) VALUES (?, ?)";
            if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) == SQLITE_OK) {
                sqlite3_bind_text(stmt, 1, SwordCommon::FORMAT_VERSION, -1, SQLITE_STATIC);
                sqlite3_bind_text(stmt, 2, "Bible translation module schema", -1, SQLITE_STATIC);
                sqlite3_step(stmt);
                sqlite3_finalize(stmt);
            }
        }

        std::cout << "    Database schema created" << std::endl;
        return true;
    }

    /**
     * Read a value from the parsed .conf, falling back to SWORD's own lookup.
     */
    std::string conf(const std::string& key, const std::string& defaultValue = "") const {
        // .conf files are not reliably UTF-8 (THOT's Copyright carries a stray
        // Latin-1 byte), and module_info must be: transcode what is not.
        auto clean = [](std::string v) {
            return SwordCommon::isValidUtf8(v) ? v : SwordCommon::latin1ToUtf8(v);
        };
        auto it = swordConfig.find(key);
        if (it != swordConfig.end() && !it->second.empty()) return clean(it->second);
        const char* value = module->getConfigEntry(key.c_str());
        if (value && *value) return clean(std::string(value));
        return defaultValue;
    }

    /**
     * Parse module metadata from the .conf file.
     */
    bool parseModuleInfo() {
        std::cout << "[4/6] Parsing module metadata..." << std::endl;

        moduleInfo.abbreviation = module->getName();
        moduleInfo.fullName = module->getDescription();

        // Prefer the raw .conf so that repeated keys (About=, in particular)
        // are joined rather than truncated to their first fragment.
        swordConfig = SwordCommon::parseModuleConfig(tempDir, moduleInfo.abbreviation);

        const std::string confAbbrev = conf("Abbreviation");
        if (!confAbbrev.empty()) moduleInfo.abbreviation = confAbbrev;

        const std::string confDescription = conf("Description");
        if (!confDescription.empty()) moduleInfo.fullName = confDescription;

        // language_code is the bare ISO 639 code; the full
        // SWORD tag (script/region, e.g. "ar-Arab-EG") is kept in metadata.
        moduleInfo.languageTag = conf("Lang", "en");
        moduleInfo.languageCode = SwordCommon::primaryLanguage(moduleInfo.languageTag);
        if (moduleInfo.languageCode.empty()) moduleInfo.languageCode = "en";
        moduleInfo.contentVersion = conf("Version", "1.0");
        moduleInfo.publisher = conf("CopyrightHolder");

        // RTF artefacts in About/Copyright are stripped rather than shipped
        // (the real KJV module's description contains literal "\par" escapes).
        moduleInfo.description = SwordCommon::stripRtfArtifacts(conf("About"));

        std::string copyrightText = conf("ShortCopyright");
        if (copyrightText.empty()) copyrightText = conf("Copyright");
        if (copyrightText.empty()) copyrightText = conf("CopyrightNotes");
        if (copyrightText.empty()) copyrightText = conf("DistributionLicense");
        moduleInfo.copyright = SwordCommon::stripRtfArtifacts(copyrightText);

        const std::string distributionLicense = conf("DistributionLicense");
        moduleInfo.licenseSpdx = SwordCommon::licenseToSpdx(distributionLicense);
        moduleInfo.licenseUrl = SwordCommon::licenseUrlForSpdx(moduleInfo.licenseSpdx);

        // Provenance: TextSource is usually a name, occasionally a URL.
        const std::string textSource = conf("TextSource");
        if (textSource.rfind("http://", 0) == 0 || textSource.rfind("https://", 0) == 0) {
            moduleInfo.sourceUrl = textSource;
        }

        // Year: CopyrightDate is frequently a range ("1611-2011") or free text,
        // so extract the first plausible four-digit year instead of stoi()ing
        // the whole string (which threw on every non-numeric value).
        moduleInfo.yearPublished = SwordCommon::extractYear(conf("CopyrightDate"));
        if (moduleInfo.yearPublished == 0) {
            moduleInfo.yearPublished = SwordCommon::extractYear(moduleInfo.copyright);
        }

        const std::string lang = moduleInfo.languageCode;
        moduleInfo.isOriginalLanguage = (lang == "he" || lang == "grc" || lang == "hbo");
        // The .conf Direction key first, then language and script subtags: an
        // exact match on "ar" missed "ar-Arab-EG" and "ku-Arab-IQ".
        if (SwordCommon::isRightToLeft(moduleInfo.languageTag, conf("Direction"))) {
            moduleInfo.textDirection = "rtl";
        }

        declaredVersification = conf("Versification", "KJV");
        moduleInfo.sourceVersification = declaredVersification;
        if (!versificationOverride.empty()) {
            moduleInfo.sourceVersification = versificationOverride;
            std::cout << "    Versification override: .conf says '" << declaredVersification
                      << "', converting as '" << versificationOverride << "'" << std::endl;
        }

        const std::string encoding = SwordCommon::toLower(conf("Encoding"));
        sourceIsUtf8 = (encoding == "utf-8" || encoding == "utf8");

        const std::string sourceType = SwordCommon::toLower(conf("SourceType"));
        preferRawOsis = (sourceType == "osis" || sourceType.empty());

        // Identity that survives content revisions — never include
        // Version or the content hash here. --uuid overrides this (task 0035
        // requirement 5): a reconverted module reuses its existing uuid
        // instead of minting a fresh one, via the name -> uuid mapping in
        // scripts/data/module-uuid-map.json.
        moduleInfo.uuid = !uuidOverride.empty() ? uuidOverride : SwordCommon::deterministicUuid(
            "bible:" + SwordCommon::toLower(moduleInfo.abbreviation) + ":" +
            SwordCommon::toLower(moduleInfo.languageCode));

        // Everything else the .conf told us, preserved for round-tripping.
        std::ostringstream metaJson;
        metaJson << "{"
                 << "\"sword_module\":\"" << SwordCommon::jsonEscape(module->getName()) << "\","
                 << "\"sword_lang\":\"" << SwordCommon::jsonEscape(moduleInfo.languageTag) << "\","
                 << "\"sword_source_type\":\"" << SwordCommon::jsonEscape(conf("SourceType")) << "\","
                 << "\"sword_versification\":\"" << SwordCommon::jsonEscape(moduleInfo.sourceVersification) << "\","
                 << "\"sword_distribution_license\":\"" << SwordCommon::jsonEscape(distributionLicense) << "\","
                 << "\"sword_text_source\":\"" << SwordCommon::jsonEscape(textSource) << "\""
                 << "}";
        moduleInfo.metadataJson = metaJson.str();

        std::cout << "    Abbreviation: " << moduleInfo.abbreviation << std::endl;
        std::cout << "    Full Name: " << moduleInfo.fullName << std::endl;
        std::cout << "    Language: " << moduleInfo.languageCode << std::endl;
        std::cout << "    Content version: " << moduleInfo.contentVersion << std::endl;
        std::cout << "    Module UUID: " << moduleInfo.uuid << std::endl;
        std::cout << "    Licence: "
                  << (moduleInfo.licenseSpdx.empty() ? "(unknown)" : moduleInfo.licenseSpdx)
                  << std::endl;

        // ---------------------------------------------------------------
        // Mechanism 2: versification gate.
        //
        // Book-level canon restriction cannot see a WHOLE-MODULE renumbering
        // that stays inside the canonical envelope. SPE, for example, ships
        // Gen 32:33 / Exod 7:29 / Exod 21:37 under the Masoretic division —
        // every book name is canonical, every book number is <= 66, and the
        // verse numbers are still shifted. The only signal available at
        // conversion time is the source's declared versification, and this is
        // the only stage of the pipeline that can see it.
        //
        // So: refuse by default rather than silently emitting verse ids that
        // address the wrong text. KJVA is accepted because its extra books are
        // dropped by the canon filter while its 66 canonical books use KJV
        // numbering.
        // ---------------------------------------------------------------
        const std::string vers = SwordCommon::toLower(moduleInfo.sourceVersification);
        const bool kjvCompatible = vers.empty() || vers == "kjv" || vers == "kjva";
        // A system identical to a mapped one in all 66 canonical books is
        // translated with that system's map (see versificationAlias()).
        const std::string alias = versificationAlias(vers);
        const std::string mapName = alias.empty() ? moduleInfo.sourceVersification : alias;
        const std::string mapLower = SwordCommon::toLower(mapName);
        if (!kjvCompatible) {
            if (!alias.empty()) {
                std::cout << "    Versification '" << moduleInfo.sourceVersification
                          << "' is identical to '" << alias
                          << "' in the 66 canonical books; using its map" << std::endl;
            }
            if (isMappableVersification(mapLower)) {
                // libsword ships a verse map for this system: translate rather
                // than refuse. Every reference is rewritten into KJV below.
                sword::VersificationMgr* vm = sword::VersificationMgr::getSystemVersificationMgr();
                srcVersification = vm->getVersificationSystem(mapName.c_str());
                kjvVersification = vm->getVersificationSystem("KJV");
                if (srcVersification && kjvVersification) {
                    translateVersification = true;
                    std::cout << "    Versification: '" << moduleInfo.sourceVersification
                              << "' -> '" << SwordCommon::VERSIFICATION
                              << "' (mapped per-verse using libsword's tables)" << std::endl;
                    return true;
                }
                std::cerr << "ERROR: versification '" << moduleInfo.sourceVersification
                          << "' is listed as mappable but libsword did not return it."
                          << std::endl;
                return false;
            }

            // No libsword table, but an external map may cover it (see
            // tools/import/sword/data/). Only used when it loads cleanly —
            // a missing or unusable map falls through to the refusal below,
            // never to an unmapped conversion.
            if (loadExternalMap(mapLower)) {
                translateVersification = true;
                useExternalMap = true;
                kjvVersification =
                    sword::VersificationMgr::getSystemVersificationMgr()->getVersificationSystem("KJV");
                std::cout << "    Versification: '" << moduleInfo.sourceVersification
                          << "' -> '" << SwordCommon::VERSIFICATION
                          << "' (external map; libsword ships none for this system)" << std::endl;
                return true;
            }

            if (!allowForeignVersification) {
                std::cerr << std::endl
                          << "ERROR: this module declares versification '"
                          << moduleInfo.sourceVersification << "', not KJV, and libsword"
                          << " ships no verse map for it." << std::endl
                          << "  The module format stores only '" << SwordCommon::VERSIFICATION
                          << "'. Converting anyway would emit verse ids that address"
                          << " different text than every other module." << std::endl
                          << "  Book-level canon restriction cannot detect this: the book"
                          << " names and numbers are canonical, only the verse division"
                          << " differs." << std::endl
                          << "  Mappable systems (translated automatically): "
                          << mappableVersificationList() << "." << std::endl
                          << "  Re-run with --allow-versification if you have confirmed the"
                          << " numbering actually matches KJV English." << std::endl;
                return false;
            }
            std::cerr << "    WARNING: source versification is '" << moduleInfo.sourceVersification
                      << "' and --allow-versification was given. Verse numbering may not"
                      << " align with other modules." << std::endl;
        }

        return true;
    }

    /**
     * Versification systems libsword ships a KJV verse map for.
     *
     * This list is NOT "systems libsword knows about" -- it registers many more
     * (MT, Leningrad, LXX, German, Luther, Orthodox ...) but only supplies
     * `mappings_*` data for these. For the rest, translateVerse() silently
     * returns its input unchanged, which would look like a successful
     * conversion while emitting misaligned verse ids. So the list is explicit,
     * and anything outside it is still refused.
     *
     * Derived from which canon_*.h headers define a `mappings_*` array
     * (libsword 1.9: canon_nrsv.h, canon_vulg.h, canon_synodal.h).
     */
    static const std::vector<std::string>& mappableVersifications() {
        static const std::vector<std::string> systems = { "nrsv", "vulg", "synodal" };
        return systems;
    }

    // ------------------------------------------------------------------
    // Externally-supplied verse maps, for systems libsword does NOT map.
    //
    // `tools/import/sword/data/versification-<name>-to-kjv.json` supplies rules
    // of the form: MT book B, chapter C, verses [from..to] land in KJV chapter
    // `dc` with `off` added to the verse number. Anything not covered maps to
    // itself.
    //
    // These maps are DERIVED FROM TEXT, not from canon shapes -- canon shapes
    // cannot distinguish a chapter-boundary shift from a verse merge. See the
    // header of the JSON file.
    // ------------------------------------------------------------------
    struct ExternalRule {
        int book;
        int srcChapter;
        int fromVerse;
        int toVerse;
        int dstChapter;
        int offset;
    };
    std::vector<ExternalRule> externalRules;
    bool externalMapLoaded = false;

    /** Minimal extractor for the flat, machine-generated rule objects. */
    static bool readIntField(const std::string& obj, const std::string& key, int& out) {
        const std::string needle = "\"" + key + "\"";
        size_t p = obj.find(needle);
        if (p == std::string::npos) return false;
        p = obj.find(':', p + needle.size());
        if (p == std::string::npos) return false;
        p++;
        while (p < obj.size() && (obj[p] == ' ' || obj[p] == '\t')) p++;
        bool neg = false;
        if (p < obj.size() && (obj[p] == '-' || obj[p] == '+')) { neg = (obj[p] == '-'); p++; }
        if (p >= obj.size() || !isdigit(static_cast<unsigned char>(obj[p]))) return false;
        int v = 0;
        while (p < obj.size() && isdigit(static_cast<unsigned char>(obj[p]))) { v = v * 10 + (obj[p] - '0'); p++; }
        out = neg ? -v : v;
        return true;
    }

    /**
     * Load an external map. Returns false when the file is absent or unusable —
     * the caller then refuses the module rather than converting it unmapped.
     */
    bool loadExternalMap(const std::string& versLower) {
        const fs::path exeDir = executableDirectory();
        const std::string fileName = "versification-" + versLower + "-to-kjv.json";
        const fs::path candidates[] = {
            exeDir / ".." / "data" / fileName,
            exeDir / "data" / fileName,
            fs::path("tools/import/sword/data") / fileName
        };

        std::string content;
        std::string usedPath;
        for (const auto& c : candidates) {
            std::ifstream in(c);
            if (!in) continue;
            std::stringstream ss;
            ss << in.rdbuf();
            content = ss.str();
            usedPath = c.string();
            break;
        }
        if (content.empty()) {
            std::cerr << "ERROR: no verse map found for versification '" << versLower
                      << "'. Looked for " << fileName << " under tools/import/sword/data/."
                      << std::endl;
            return false;
        }

        // Rules live in the "rules" array; each entry is a flat object.
        size_t arrayStart = content.find("\"rules\"");
        if (arrayStart == std::string::npos) {
            std::cerr << "ERROR: " << usedPath << " has no \"rules\" array." << std::endl;
            return false;
        }
        arrayStart = content.find('[', arrayStart);
        if (arrayStart == std::string::npos) return false;

        size_t pos = arrayStart;
        while (true) {
            size_t objStart = content.find('{', pos);
            if (objStart == std::string::npos) break;
            size_t objEnd = content.find('}', objStart);
            if (objEnd == std::string::npos) break;
            const std::string obj = content.substr(objStart, objEnd - objStart + 1);

            ExternalRule r{};
            if (readIntField(obj, "b", r.book) &&
                readIntField(obj, "c", r.srcChapter) &&
                readIntField(obj, "from", r.fromVerse) &&
                readIntField(obj, "to", r.toVerse) &&
                readIntField(obj, "dc", r.dstChapter) &&
                readIntField(obj, "off", r.offset)) {
                externalRules.push_back(r);
            }
            pos = objEnd + 1;
        }

        // "extends": "<name>" appends that map's rules AFTER this file's own,
        // so this file's rules take precedence (the first matching rule wins).
        // The German map is the MT map plus a few German-specific rules.
        const size_t ext = content.find("\"extends\"");
        const size_t rulesKey = content.find("\"rules\"");
        if (ext != std::string::npos && ext < rulesKey) {
            const size_t colon = content.find(':', ext);
            const size_t q1 = (colon == std::string::npos) ? colon : content.find('"', colon + 1);
            const size_t q2 = (q1 == std::string::npos) ? q1 : content.find('"', q1 + 1);
            if (q1 != std::string::npos && q2 != std::string::npos) {
                const std::string base = SwordCommon::toLower(content.substr(q1 + 1, q2 - q1 - 1));
                std::cout << "    Loaded " << externalRules.size() << " rule(s) from " << usedPath
                          << "; extending '" << base << "'" << std::endl;
                if (base == versLower || !loadExternalMap(base)) {
                    std::cerr << "ERROR: " << usedPath << " extends '" << base
                              << "', which could not be loaded." << std::endl;
                    return false;
                }
                return true;
            }
        }

        if (externalRules.empty()) {
            std::cerr << "ERROR: " << usedPath << " yielded no usable rules." << std::endl;
            return false;
        }

        std::cout << "    Loaded " << externalRules.size() << " verse-map rule(s) from "
                  << usedPath << std::endl;
        externalMapLoaded = true;
        return true;
    }

    /** Where this executable lives, so the data directory can be found beside it. */
    static fs::path executableDirectory() {
        std::error_code ec;
        fs::path p = fs::read_symlink("/proc/self/exe", ec);
        if (!ec) return p.parent_path();
        return fs::current_path();
    }

    /** Apply the external map. Falls through to identity when no rule matches. */
    bool translateViaExternalMap(int book, int chapter, int verse,
                                 int& outChapter, int& outVerse) const {
        for (const auto& r : externalRules) {
            if (r.book == book && r.srcChapter == chapter &&
                verse >= r.fromVerse && verse <= r.toVerse) {
                outChapter = r.dstChapter;
                outVerse = verse + r.offset;
                return true;
            }
        }
        outChapter = chapter;
        outVerse = verse;
        return true;
    }

    /**
     * Versification systems that are IDENTICAL to a mapped system in every
     * chapter of the 66 canonical books (compared chapter by chapter with
     * libsword 1.9's own tables), so that system's map applies unchanged:
     *   NRSVA       = NRSV plus the deuterocanon (whose books are skipped anyway)
     *   SynodalProt = Synodal without the LXX additions at chapter ends
     *   Leningrad   = MT in the Leningrad Codex's book order (the external map
     *                 is keyed by canonical book number, so order is irrelevant)
     *   Luther      = German (versification-german-to-kjv.json, which extends MT)
     * Returns the mapped system's name, or "" when there is no alias.
     */
    static std::string versificationAlias(const std::string& lowerName) {
        if (lowerName == "nrsva") return "NRSV";
        if (lowerName == "synodalprot") return "Synodal";
        if (lowerName == "leningrad") return "MT";
        if (lowerName == "luther") return "German";     // identical in all 66 books
        return "";
    }

    static bool isMappableVersification(const std::string& lowerName) {
        for (const auto& s : mappableVersifications()) {
            if (s == lowerName) return true;
        }
        return false;
    }

    static std::string mappableVersificationList() {
        std::string out;
        for (const auto& s : mappableVersifications()) {
            if (!out.empty()) out += ", ";
            out += s;
        }
        return out;
    }

    /**
     * Mechanism 3: translate one reference from the source versification
     * into KJV, using libsword's own mapping tables.
     *
     * Returns false when the reference has no KJV counterpart at all (the
     * mapping tables do encode "this verse does not exist over there"), in
     * which case the caller drops the verse and counts it. `outBook` is
     * resolved back to a canonical 1..66 book number because a mapping may
     * cross a book boundary.
     */
    bool translateReferenceToKjv(const std::string& osisBook, int chapter, int verse,
                                 int& outBook, int& outChapter, int& outVerse,
                                 std::string& reason) const {
        const char* bookName = osisBook.c_str();
        int ch = chapter;
        int vs = verse;
        int vsEnd = verse;

        // How far the SOURCE chapter runs; used below to tell a split verse
        // (fold it) from an addition inside a canonical book (drop it).
        int srcChapterMax = 0;
        if (!useExternalMap && srcVersification) {
            const int srcBookNo = srcVersification->getBookNumberByOSISName(bookName);   // 1-based
            if (srcBookNo > 0) {
                const sword::VersificationMgr::Book* srcBook = srcVersification->getBook(srcBookNo - 1);
                if (srcBook && chapter <= srcBook->getChapterMax()) {
                    srcChapterMax = srcBook->getVerseMax(chapter);
                }
            }
        }

        if (useExternalMap) {
            // An external map is keyed by canonical book number, and never moves
            // a verse across a book boundary — so the book is unchanged here and
            // only chapter/verse are rewritten.
            const int bookNo = SwordCommon::canonicalBookNumberFromOsis(osisBook);
            if (bookNo == 0) { reason = "book outside the 66-book canon"; return false; }
            translateViaExternalMap(bookNo, chapter, verse, ch, vs);
        } else {
            srcVersification->translateVerse(kjvVersification, &bookName, &ch, &vs, &vsEnd);
        }

        // A psalm superscription is numbered as verse(s) 1..N in MT but folded
        // into the title of verse 1 in KJV, so it maps to "verse 0" — not a
        // verse. Merge it into verse 1 rather than dropping the text; the merge
        // path records the original numbering in formatting.source_verses.
        if (vs <= 0 && ch > 0) vs = 1;

        if (!bookName || ch <= 0 || vs <= 0) {
            reason = "no mapping";
            return false;
        }

        // A mapping may land the verse in a different book (Vulgate Psalm
        // numbering shifts, Malachi/Joel chapter splits), so re-resolve it.
        const int book = SwordCommon::canonicalBookNumberFromOsis(bookName);
        if (book == 0) {
            reason = "maps outside the 66-book canon";
            return false;
        }

        // ------------------------------------------------------------------
        // Clamp to the KJV canon.
        //
        // libsword's verse maps are PARTIAL. The Vulgate map, for instance,
        // renumbers most of the Psalms correctly but leaves references that
        // have no KJV home at all: Esther 11-16 (the Greek additions, which are
        // deuterocanonical text sitting INSIDE a canonical book, so book-level
        // filtering cannot see them), and single-verse overhangs like Numbers
        // 30:17 or 1 Kings 22:54 where the Vulgate splits a verse the KJV does
        // not. Left alone these produce verse ids outside the canonical space.
        //
        // The canon bounds come from libsword's own KJV system, so this stays a
        // consumer of SWORD's data rather than a second source of truth.
        // ------------------------------------------------------------------
        // NOTE: getBookNumberByOSISName() is 1-based; getBook() is 0-based. Passing
        // the number straight through returns the FOLLOWING book (Gen -> Exod,
        // Esth -> Job) and silently clamps against the wrong canon.
        const int kjvBookNo = kjvVersification->getBookNumberByOSISName(bookName);
        if (kjvBookNo > 0) {
            const sword::VersificationMgr::Book* kjvBook = kjvVersification->getBook(kjvBookNo - 1);
            if (kjvBook) {
                if (ch > kjvBook->getChapterMax()) {
                    // Beyond the book's canonical length: deuterocanonical
                    // addition (Esther 11-16, Daniel 13-14). Drop it, as the
                    // 66-book canon decision requires.
                    reason = "chapter beyond canonical book length (deuterocanonical addition)";
                    return false;
                }
                const int verseMax = kjvBook->getVerseMax(ch);
                // A source chapter running 3+ verses past the KJV chapter, on a
                // verse the map left where it was, is carrying an ADDITION, not
                // a split verse: Vulgate Esther 10:4-13 (Additions to Esther),
                // Synodal Joshua 24:34-36 (LXX plus). Folding those onto the
                // last canonical verse leaked deuterocanonical text into it.
                // Drop them, as the 66-book canon requires.
                const bool unmoved = (ch == chapter && vs == verse
                                      && SwordCommon::canonicalBookNumberFromOsis(bookName)
                                         == SwordCommon::canonicalBookNumberFromOsis(osisBook));
                if (verseMax > 0 && vs > verseMax && unmoved && srcChapterMax - verseMax >= 3) {
                    reason = "verse past the canonical chapter end (addition)";
                    return false;
                }
                if (verseMax > 0 && vs > verseMax) {
                    // Source splits a verse the KJV keeps whole. Fold it onto
                    // the chapter's last verse; the merge path appends the text
                    // and records the original numbering in source_verses.
                    vs = verseMax;
                }
            }
        }

        outBook = book;
        outChapter = ch;
        outVerse = vs;
        return true;
    }

    /**
     * Mechanism 1: look up a variant verse division.
     * Returns nullptr when the verse is numbered canonically.
     */
    const VariantVerseMapping* findVariantMapping(int book, int chapter, int verse) const {
        for (const auto& m : VARIANT_VERSE_MAP) {
            if (m.bookNumber == book && m.sourceChapter == chapter && m.sourceVerse == verse) {
                return &m;
            }
        }
        return nullptr;
    }

    /** True when this verse is the canonical host of some variant mapping. */
    static bool isVariantHost(int book, int chapter, int verse) {
        for (const auto& m : VARIANT_VERSE_MAP) {
            if (m.bookNumber == book && m.hostChapter == chapter && m.hostVerse == verse) {
                return true;
            }
        }
        return false;
    }

    /**
     * Insert module info into database
     */
    bool insertModuleInfo() {
        std::cout << "[5/6] Inserting module metadata..." << std::endl;

        const char* sql = R"SQL(
            INSERT INTO module_info (
                info_id, module_uuid, module_type, format, format_version,
                abbreviation, full_name, language_code, year_published,
                copyright, license_spdx, license_url, source_url,
                description, publisher, is_original_language, right_to_left,
                versification, content_version, metadata, compression
            ) VALUES (1, ?, 'bible', 'bible-module', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        )SQL";

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            std::cerr << "Failed to prepare statement: " << sqlite3_errmsg(db) << std::endl;
            return false;
        }

        int i = 1;
        bindText(stmt, i++, moduleInfo.uuid);                       // module_uuid
        bindText(stmt, i++, SwordCommon::FORMAT_VERSION);           // format_version
        bindText(stmt, i++, moduleInfo.abbreviation);               // abbreviation
        bindText(stmt, i++, moduleInfo.fullName);                   // full_name
        bindText(stmt, i++, moduleInfo.languageCode);               // language_code

        if (moduleInfo.yearPublished > 0) {                         // year_published
            sqlite3_bind_int(stmt, i++, moduleInfo.yearPublished);
        } else {
            sqlite3_bind_null(stmt, i++);
        }

        bindTextOrNull(stmt, i++, moduleInfo.copyright);            // copyright
        bindTextOrNull(stmt, i++, moduleInfo.licenseSpdx);          // license_spdx
        bindTextOrNull(stmt, i++, moduleInfo.licenseUrl);           // license_url
        bindTextOrNull(stmt, i++, moduleInfo.sourceUrl);            // source_url
        bindTextOrNull(stmt, i++, moduleInfo.description);          // description
        bindTextOrNull(stmt, i++, moduleInfo.publisher);            // publisher
        sqlite3_bind_int(stmt, i++, moduleInfo.isOriginalLanguage ? 1 : 0);
        sqlite3_bind_int(stmt, i++, moduleInfo.textDirection == "rtl" ? 1 : 0);  // right_to_left
        bindText(stmt, i++, SwordCommon::VERSIFICATION);            // versification
        bindTextOrNull(stmt, i++, moduleInfo.contentVersion);       // content_version
        bindTextOrNull(stmt, i++, moduleInfo.metadataJson);         // metadata
        bindText(stmt, i++, codecName);                             // compression (always 'none' for bible — §3.4)

        bool success = (sqlite3_step(stmt) == SQLITE_DONE);
        if (!success) {
            std::cerr << "Failed to insert module_info: " << sqlite3_errmsg(db) << std::endl;
        }
        sqlite3_finalize(stmt);

        return success;
    }

    /**
     * Convert all verses from the SWORD module.
     *
     * The book number comes from the verse's OSIS reference, so a module
     * whose internal ordering differs from the Christian canon (JPS) still
     * lands on the right book, and a module carrying deuterocanonical books
     * (CPDV, DRC, KJVA) has those books dropped instead of shifting every
     * later book by one.
     */
    bool convertVerses() {
        std::cout << "[6/6] Converting verses..." << std::endl;
        if (!bookRanges.empty()) {
            std::cout << "    Book range filter: ";
            for (size_t i = 0; i < bookRanges.size(); i++) {
                if (i > 0) std::cout << ", ";
                std::cout << bookRanges[i].first << "-" << bookRanges[i].second;
            }
            std::cout << std::endl;
        }

        executeSql("BEGIN TRANSACTION");

        module->setKey("Genesis 1:1");
        VerseKey* vk = dynamic_cast<VerseKey*>(module->getKey());
        if (!vk) {
            std::cerr << "Module doesn't support VerseKey" << std::endl;
            executeSql("ROLLBACK");
            return false;
        }

        int verseCount = 0;
        int duplicateCount = 0;
        interlinearCount = 0; // class member: read after convertVerses() to decide whether to keep interlinear_word
        std::set<int64_t> seenVerses;

        (*vk) = sword::TOP;
        vk->setIntros(false);
        vk->popError();

        int iterations = 0;
        const int MAX_ITERATIONS = 200000;   // generous: apocryphal modules iterate further

        // SWORD "links" a verse to the previous one when the source gives one
        // text for a verse range (e.g. Gen 13:3-4 in cmncbt2023eb): both keys
        // return the same entry. Storing it under every verse printed the text
        // twice in a row, so it is stored once, on the first verse.
        VerseKey prevKey(*vk);
        std::string prevRawForLink;
        bool havePrev = false;

        do {
            iterations++;
            if (iterations > MAX_ITERATIONS) {
                std::cerr << "WARNING: Hit iteration limit, stopping to prevent infinite loop" << std::endl;
                break;
            }

            // isLinked() alone is not enough: the empty entries of a partial
            // module share an index position, and the verses after them looked
            // "linked" too (kld1839eb lost all 23,212 verses). Require the same
            // non-empty text as the previous key as well.
            const std::string rawForLink = module->getRawEntry();
            const bool linkedToPrevious = havePrev && !rawForLink.empty()
                && rawForLink == prevRawForLink && module->isLinked(&prevKey, vk);
            prevKey = *vk;
            prevRawForLink = rawForLink;
            havePrev = true;

            // Resolve the book through its OSIS name. This replaces the old
            // "book number went down, so we must be in the New Testament,
            // add 39" heuristic, which was the root cause of the four corrupt
            // shipped modules.
            const char* osisRefRaw = vk->getOSISRef();
            const std::string osisRef = osisRefRaw ? osisRefRaw : "";
            const std::string osisBook = SwordCommon::osisBookOf(osisRef);
            const int bookNumber = SwordCommon::canonicalBookNumberFromOsis(osisBook);

            if (bookNumber == 0) {
                // Mechanism 2: outside the 66-book canon (or unrecognised).
                // Skipping it is what keeps every LATER book on its correct
                // number — the renumbering bug in jps/cpdv/drc/kjva came from
                // admitting these and shifting everything after them.
                if (!osisBook.empty()) skippedBooks[osisBook]++;
                (*vk)++;
                continue;
            }

            if (!isBookInRange(bookNumber)) {
                (*vk)++;
                continue;
            }

            const int chapter = vk->getChapter();
            const int verse = vk->getVerse();
            if (chapter <= 0 || verse <= 0) {
                (*vk)++;
                continue;
            }

            if (linkedToPrevious) {
                linkedSkipCount++;
                (*vk)++;
                continue;
            }

            // Mechanism 3: rewrite the reference into KJV before it becomes
            // a verse id. Everything downstream — dedup, variant merging,
            // storage — then operates in one versification.
            int targetBook = bookNumber;
            int targetChapter = chapter;
            int targetVerse = verse;
            bool remapped = false;
            if (translateVersification) {
                std::string dropReason;
                if (!translateReferenceToKjv(osisBook, chapter, verse,
                                             targetBook, targetChapter, targetVerse, dropReason)) {
                    // No KJV counterpart. Dropping is correct — there is no id
                    // that would address this text — but it must be visible.
                    versificationDropCount++;
                    if (versificationDropCount <= 10) {
                        std::cout << "    Versification: " << osisBook << " " << chapter << ":"
                                  << verse << " skipped (" << dropReason << ")" << std::endl;
                    }
                    (*vk)++;
                    continue;
                }
                remapped = (targetBook != bookNumber || targetChapter != chapter
                            || targetVerse != verse);
                if (remapped) versificationRemapCount++;
            }

            const int64_t verseId =
                SwordCommon::calculateVerseId(targetBook, targetChapter, targetVerse);

            // Under a translation, N source verses legitimately collapse onto one
            // KJV verse (that is what a verse map expresses). Those are merges,
            // not duplicates: fall through, parse the text, and append it to the
            // verse already written. Without this the second and subsequent
            // source verses would be silently dropped.
            bool mergeIntoExisting = seenVerses.count(verseId) > 0 && translateVersification;

            if (seenVerses.count(verseId) > 0 && !mergeIntoExisting) {
                duplicateCount++;
                if (duplicateCount == 1) {
                    std::cout << "    Duplicate verse encountered at " << osisRef
                              << " (verse_id " << verseId << ")" << std::endl;
                }
                if (duplicateCount > 100) {
                    std::cout << "    Detected verse duplication (looping), stopping iteration" << std::endl;
                    break;
                }
                (*vk)++;
                continue;
            }
            seenVerses.insert(verseId);

            // Map source markers straight to span types. Going through
            // rendered HTML is what put <font size="-1"> and <i> into
            // bible_verse.text in the first place; here the markup is consumed
            // into `formatting` and never reaches `text`.
            const char* rawEntry = module->getRawEntry();
            std::string rawOsis = rawEntry ? std::string(rawEntry) : std::string();
            // renderText() returns SWBuf BY VALUE; binding it to a `const char*`
            // leaves a dangling pointer once the temporary dies. Hold the buffer.
            const sword::SWBuf renderedBuf = module->renderText();
            std::string renderedText =
                renderedBuf.length() ? std::string(renderedBuf.c_str()) : std::string();

            // SWORD's default encoding is Latin-1: a module without
            // Encoding=UTF-8 stores Latin-1 bytes, which went straight into
            // bible_verse.text as invalid UTF-8 (DanDetteBiblen). Transcode
            // whatever is not already valid UTF-8.
            // A module declaring UTF-8 can still carry stray Latin-1 bytes
            // (DanDetteBiblen): repair just those bytes.
            if (!SwordCommon::isValidUtf8(rawOsis)) {
                rawOsis = sourceIsUtf8 ? SwordCommon::repairUtf8(rawOsis)
                                       : SwordCommon::latin1ToUtf8(rawOsis);
                latin1TranscodedCount++;
            }
            if (!SwordCommon::isValidUtf8(renderedText)) {
                renderedText = sourceIsUtf8 ? SwordCommon::repairUtf8(renderedText)
                                            : SwordCommon::latin1ToUtf8(renderedText);
            }

            const std::string& primary = preferRawOsis ? rawOsis : renderedText;
            const std::string& fallback = preferRawOsis ? renderedText : rawOsis;

            SwordCommon::OsisVerseResult parsed = SwordCommon::parseOsisVerse(primary);
            if (parsed.text.empty() && !fallback.empty()) {
                parsed = SwordCommon::parseOsisVerse(fallback);
            }

            if (parsed.text.empty()) {
                // Nothing will be stored, so this id must not count as seen:
                // otherwise the next source verse mapped here takes the merge
                // path, finds nothing cached, and is dropped.
                if (!mergeIntoExisting) seenVerses.erase(verseId);
                (*vk)++;
                continue;
            }

            // A pilcrow at the end of the PREVIOUS verse describes this one
            // (AKJV-style sources place the marker there). Only the source
            // document makes this recoverable, so it is resolved here.
            if (carryParagraphStart) {
                parsed.block.paragraphStart = true;
                carryParagraphStart = false;
            }
            if (parsed.trailingParagraphMarker) {
                carryParagraphStart = true;
            }

            importedBooks[osisBook]++;

            // --- Mechanism 3: remapped verse bookkeeping --------------
            // Record where this text came from in the SOURCE numbering, so the
            // remapping is auditable and reversible from the module alone.
            if (remapped) {
                SwordCommon::SourceVerseRecord provenance;
                provenance.chapter = chapter;
                provenance.verse = verse;
                provenance.startWord = 0;
                provenance.endWord = parsed.wordCount - 1;
                parsed.sourceVerses.push_back(provenance);
            }

            // Several source verses mapping onto one KJV verse: append this
            // text to what is already stored rather than dropping it.
            if (mergeIntoExisting) {
                auto cached = hostCache.find(verseId);
                if (cached != hostCache.end()) {
                    SwordCommon::OsisVerseResult merged =
                        mergeVerseResults(cached->second, parsed);
                    if (rewriteVerse(verseId, merged)) {
                        cached->second = merged;
                        variantMergeCount++;
                    }
                    (*vk)++;
                    continue;
                }
                // Nothing is stored under this id yet (the earlier source verse
                // mapped here had no text). Store this text as the verse itself
                // instead of dropping it: that lost Rev 13:1 in Ckb_KSS/Hrv_KOK.
                mergeIntoExisting = false;
            }

            // --- Mechanism 1: variant verse division ------------------
            // This verse's text is genuine scripture carrying a non-canonical
            // number. Fold it into its host rather than dropping it (loses
            // text) or emitting it under its own id (unaddressable).
            //
            // Skipped when mechanism 3 already moved this verse: libsword's NRSV
            // map sends Rev 12:18 to Rev 13:1 itself, and applying the table on
            // top of that held the fragment for a host that had already been
            // marked seen, so Rev 13:1 then took the merge path, found nothing
            // cached, and BOTH verses were lost (Geneva1599).
            const VariantVerseMapping* mapping =
                remapped ? nullptr : findVariantMapping(bookNumber, chapter, verse);
            if (mapping) {
                const int64_t hostId = SwordCommon::calculateVerseId(
                    bookNumber, mapping->hostChapter, mapping->hostVerse);

                SwordCommon::SourceVerseRecord provenance;
                provenance.chapter = chapter;
                provenance.verse = verse;
                provenance.startWord = 0;
                provenance.endWord = parsed.wordCount - 1;
                parsed.sourceVerses.push_back(provenance);

                auto cached = hostCache.find(hostId);
                if (cached != hostCache.end()) {
                    // Host already written (the fragment follows it, e.g. 3 John 15).
                    SwordCommon::OsisVerseResult merged =
                        mergeVerseResults(cached->second, parsed);
                    if (rewriteVerse(hostId, merged)) {
                        cached->second = merged;
                        variantMergeCount++;
                        std::cout << "    Variant verse division: merged " << osisBook << " "
                                  << chapter << ":" << verse << " into verse_id " << hostId
                                  << " (" << mapping->note << ")" << std::endl;
                    }
                } else if (mapping->prefix) {
                    // Host has not been reached yet (the fragment precedes it,
                    // e.g. Rev 12:18 before Rev 13:1). Hold it.
                    VariantFragment fragment;
                    fragment.parsed = parsed;
                    fragment.prefix = mapping->prefix;
                    fragment.note = mapping->note ? mapping->note : "";
                    pendingFragments[hostId] = fragment;
                } else {
                    std::cerr << "    WARNING: host verse " << hostId << " for " << osisBook
                              << " " << chapter << ":" << verse
                              << " has not been written; holding fragment" << std::endl;
                    VariantFragment fragment;
                    fragment.parsed = parsed;
                    fragment.prefix = mapping->prefix;
                    fragment.note = mapping->note ? mapping->note : "";
                    pendingFragments[hostId] = fragment;
                }

                (*vk)++;
                continue;
            }

            // A canonical verse that hosts a variant fragment records its own
            // numbering too, so formatting.source_verses is complete.
            const bool hostsVariant = isVariantHost(bookNumber, chapter, verse);
            auto pending = pendingFragments.find(verseId);
            if (pending != pendingFragments.end()) {
                SwordCommon::SourceVerseRecord own;
                own.chapter = chapter;
                own.verse = verse;
                own.startWord = 0;
                own.endWord = parsed.wordCount - 1;
                parsed.sourceVerses.push_back(own);

                parsed = pending->second.prefix
                    ? mergeVerseResults(pending->second.parsed, parsed)
                    : mergeVerseResults(parsed, pending->second.parsed);

                variantMergeCount++;
                std::cout << "    Variant verse division: merged held fragment"
                          << " into verse_id " << verseId
                          << " (" << pending->second.note << ")" << std::endl;

                pendingFragments.erase(pending);
            }

            const std::string formattingJson = SwordCommon::buildFormattingJson(parsed);

            if (!insertVerse(verseId, parsed.text, parsed.wordCount, formattingJson)) {
                std::cerr << "    Failed to insert verse " << verseId << ": "
                          << sqlite3_errmsg(db) << std::endl;
                (*vk)++;
                continue;
            }

            for (const auto& word : parsed.interlinear) {
                if (insertInterlinearWord(verseId, word)) interlinearCount++;
            }

            // Remember hosts so a later fragment can be appended to them. Under a
            // versification translation ANY verse can become a merge target, so
            // every verse is cached rather than only the known variant hosts.
            if (hostsVariant || translateVersification) hostCache[verseId] = parsed;

            verseCount++;
            if (verseCount % 1000 == 0) {
                std::cout << "    Processed " << verseCount << " verses (book "
                          << bookNumber << ")..." << std::endl;
            }

            (*vk)++;
        } while (!vk->popError());

        // Any fragment still held has no host in this module — that means the
        // module ships the variant verse but not its canonical neighbour, which
        // is a data problem the operator needs to see.
        for (const auto& entry : pendingFragments) {
            std::cerr << "    WARNING: variant fragment for verse_id " << entry.first
                      << " (" << entry.second.note
                      << ") had no host verse in this module; text NOT imported" << std::endl;
        }

        executeSql("COMMIT");

        std::cout << "    Total verses converted: " << verseCount << std::endl;
        if (latin1TranscodedCount > 0) {
            std::cout << "    Latin-1 entries transcoded to UTF-8: " << latin1TranscodedCount << std::endl;
        }
        if (linkedSkipCount > 0) {
            std::cout << "    Linked verses (one text for a verse range, stored once): "
                      << linkedSkipCount << std::endl;
        }
        if (interlinearCount > 0) {
            std::cout << "    Interlinear words: " << interlinearCount << std::endl;
        }
        if (translateVersification) {
            std::cout << "    Versification remapped: " << versificationRemapCount
                      << " verse(s) renumbered into " << SwordCommon::VERSIFICATION << std::endl;
            if (versificationDropCount > 0) {
                std::cout << "    Versification dropped: " << versificationDropCount
                          << " verse(s) with no KJV counterpart" << std::endl;
            }
        }
        return verseCount > 0;
    }

    /**
     * Report every book we refused to import, so a caller can tell the
     * difference between "canonical subset" and "we silently lost content".
     */
    void reportSkippedBooks() {
        if (skippedBooks.empty()) return;

        std::cerr << std::endl
                  << "WARNING: " << skippedBooks.size()
                  << " book(s) outside the " << SwordCommon::CANON
                  << " canon were skipped:" << std::endl;
        for (const auto& entry : skippedBooks) {
            std::cerr << "    " << entry.first << " (" << entry.second << " verses)" << std::endl;
        }
        std::cerr << "  These are excluded by design; the canonical books keep their"
                  << " correct numbering." << std::endl;
    }

    /**
     * Record the SHA-256 of the canonical content we stored, plus the
     * source book names.
     *
     * The hash is computed by reading the rows back rather than accumulating
     * during the loop, so that verses rewritten by a variant-division merge are
     * hashed in their final form.
     *
     * The book lists matter because downstream validation can only detect
     * out-of-canon content by numbering overflow, and no module overflows (kjva
     * maxes out at book 53). A book substituted inside the canonical envelope is
     * invisible to it. This converter is the only stage that sees the source's
     * real book names, so it writes them down.
     */
    /**
     * Drop interlinear_word when the source had none (task 0035 requirement
     * 2: "no interlinear_word table unless the source has interlinear
     * data"), and write module_feature rows from what the data actually
     * shows (requirement 4; design §2.4).
     */
    bool finalizeFeatures() {
        if (interlinearCount == 0) {
            if (!executeSql("DROP TABLE IF EXISTS interlinear_word;")) return false;
        } else {
            insertModuleFeature("interlinear");

            sqlite3_stmt* stmt = nullptr;
            if (sqlite3_prepare_v2(db,
                    "SELECT COUNT(*) FROM interlinear_word WHERE strongs_number IS NOT NULL AND strongs_number != ''",
                    -1, &stmt, nullptr) == SQLITE_OK) {
                if (sqlite3_step(stmt) == SQLITE_ROW && sqlite3_column_int64(stmt, 0) > 0) {
                    insertModuleFeature("strongs_numbers");
                }
                sqlite3_finalize(stmt);
            }

            stmt = nullptr;
            if (sqlite3_prepare_v2(db,
                    "SELECT COUNT(*) FROM interlinear_word WHERE morphology IS NOT NULL AND morphology != ''",
                    -1, &stmt, nullptr) == SQLITE_OK) {
                if (sqlite3_step(stmt) == SQLITE_ROW && sqlite3_column_int64(stmt, 0) > 0) {
                    insertModuleFeature("morphology");
                }
                sqlite3_finalize(stmt);
            }
        }

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db,
                // The formatting JSON span type is "words_of_christ" (module_info's
                // own vocabulary, see bible_verse.formatting in BibleTranslation.sql);
                // the module_feature.feature_name for this is the schema's separate
                // "red_letter" (module_feature.sql's documented vocabulary).
                "SELECT COUNT(*) FROM bible_verse WHERE formatting LIKE '%\"words_of_christ\"%'",
                -1, &stmt, nullptr) == SQLITE_OK) {
            if (sqlite3_step(stmt) == SQLITE_ROW && sqlite3_column_int64(stmt, 0) > 0) {
                insertModuleFeature("red_letter");
            }
            sqlite3_finalize(stmt);
        }

        return true;
    }

    /** INSERT OR IGNORE so a feature can safely be checked/inserted more than once. */
    bool insertModuleFeature(const std::string& featureName) {
        sqlite3_stmt* stmt = nullptr;
        const char* sql = "INSERT OR IGNORE INTO module_feature (feature_name) VALUES (?)";
        if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) return false;
        sqlite3_bind_text(stmt, 1, featureName.c_str(), -1, SQLITE_TRANSIENT);
        bool ok = (sqlite3_step(stmt) == SQLITE_DONE);
        sqlite3_finalize(stmt);
        return ok;
    }

    bool finalizeContentHash() {
        if (!finalizeFeatures()) {
            std::cerr << "Failed to finalize module_feature / interlinear_word" << std::endl;
            return false;
        }

        // Canonical, codec-invariant digest (design §2.7). bible.prose is
        // empty — `text` is never compressed (§3.4) — so this is a plain
        // read, but goes through the shared helper anyway so every converter
        // computes content_sha256 the same way.
        const std::string hash = SwordCommon::computeContentSha256(
            db, "bible_verse", "verse_id", {"text"}, /*proseColumns=*/{}, codecName, {});

        // Rebuild the metadata blob now that we know which books were present.
        std::ostringstream metaJson;
        metaJson << "{"
                 << "\"sword_module\":\"" << SwordCommon::jsonEscape(module->getName()) << "\","
                 << "\"sword_lang\":\"" << SwordCommon::jsonEscape(moduleInfo.languageTag) << "\","
                 << "\"sword_source_type\":\"" << SwordCommon::jsonEscape(conf("SourceType")) << "\","
                 << "\"sword_versification\":\"" << SwordCommon::jsonEscape(moduleInfo.sourceVersification) << "\","
                 << "\"sword_distribution_license\":\"" << SwordCommon::jsonEscape(conf("DistributionLicense")) << "\","
                 << "\"sword_text_source\":\"" << SwordCommon::jsonEscape(conf("TextSource")) << "\","
                 << "\"variant_verse_merges\":" << variantMergeCount << ","
                 << "\"linked_verses_skipped\":" << linkedSkipCount << ","
                 << "\"latin1_entries_transcoded\":" << latin1TranscodedCount << ","
                 << "\"sword_declared_versification\":\"" << SwordCommon::jsonEscape(declaredVersification) << "\","
                 << "\"versification_override\":\"" << SwordCommon::jsonEscape(versificationOverride) << "\",";

        metaJson << "\"source_books\":[";
        bool first = true;
        for (const auto& entry : importedBooks) {
            if (!first) metaJson << ",";
            metaJson << "{\"osis\":\"" << SwordCommon::jsonEscape(entry.first)
                     << "\",\"verses\":" << entry.second << "}";
            first = false;
        }
        metaJson << "],";

        metaJson << "\"skipped_books\":[";
        first = true;
        for (const auto& entry : skippedBooks) {
            if (!first) metaJson << ",";
            metaJson << "{\"osis\":\"" << SwordCommon::jsonEscape(entry.first)
                     << "\",\"verses\":" << entry.second << "}";
            first = false;
        }
        metaJson << "]}";

        const std::string metadata = metaJson.str();

        const char* sql = "UPDATE module_info SET content_sha256 = ?, metadata = ? WHERE info_id = 1";
        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            return false;
        }
        sqlite3_bind_text(stmt, 1, hash.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 2, metadata.c_str(), -1, SQLITE_TRANSIENT);
        bool success = (sqlite3_step(stmt) == SQLITE_DONE);
        sqlite3_finalize(stmt);

        if (success) {
            std::cout << "    content_sha256: " << hash << std::endl;
            std::cout << "    Books imported: " << importedBooks.size() << std::endl;
            if (variantMergeCount > 0) {
                std::cout << "    Variant verse divisions merged: " << variantMergeCount << std::endl;
            }
        }
        return success;
    }

    /**
     * Insert a verse (clean text + formatting spans).
     */
    bool insertVerse(int64_t verseId, const std::string& text, int wordCount,
                     const std::string& formatting) {
        const char* sql = R"SQL(
            INSERT INTO bible_verse (verse_id, text, formatting, word_count)
            VALUES (?, ?, ?, ?)
        )SQL";

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            return false;
        }

        sqlite3_bind_int64(stmt, 1, verseId);
        sqlite3_bind_text(stmt, 2, text.c_str(), -1, SQLITE_TRANSIENT);
        bindTextOrNull(stmt, 3, formatting);
        sqlite3_bind_int(stmt, 4, wordCount);

        bool success = (sqlite3_step(stmt) == SQLITE_DONE);
        sqlite3_finalize(stmt);

        return success;
    }

    /**
     * Replace an already-written verse with a merged version.
     *
     * Used only by the variant-verse-division path, when the fragment follows
     * its host in iteration order (3 John 15 after 3 John 14). Interlinear rows
     * are rebuilt because every word offset past the join point moves.
     */
    bool rewriteVerse(int64_t verseId, const SwordCommon::OsisVerseResult& merged) {
        const std::string formattingJson = SwordCommon::buildFormattingJson(merged);

        const char* sql = R"SQL(
            UPDATE bible_verse
            SET text = ?, formatting = ?, word_count = ?
            WHERE verse_id = ?
        )SQL";

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            std::cerr << "    Failed to prepare verse rewrite: " << sqlite3_errmsg(db) << std::endl;
            return false;
        }

        sqlite3_bind_text(stmt, 1, merged.text.c_str(), -1, SQLITE_TRANSIENT);
        bindTextOrNull(stmt, 2, formattingJson);
        sqlite3_bind_int(stmt, 3, merged.wordCount);
        sqlite3_bind_int64(stmt, 4, verseId);

        const bool updated = (sqlite3_step(stmt) == SQLITE_DONE);
        sqlite3_finalize(stmt);

        if (!updated) {
            std::cerr << "    Failed to rewrite verse " << verseId << ": "
                      << sqlite3_errmsg(db) << std::endl;
            return false;
        }

        // Rebuild interlinear rows at their new offsets.
        sqlite3_stmt* del = nullptr;
        if (sqlite3_prepare_v2(db, "DELETE FROM interlinear_word WHERE verse_id = ?",
                               -1, &del, nullptr) == SQLITE_OK) {
            sqlite3_bind_int64(del, 1, verseId);
            sqlite3_step(del);
            sqlite3_finalize(del);
        }
        for (const auto& word : merged.interlinear) {
            insertInterlinearWord(verseId, word);
        }

        return true;
    }

    /**
     * Insert interlinear word data (0-based, inclusive word offsets).
     */
    bool insertInterlinearWord(int64_t verseId, const SwordCommon::InterlinearRecord& word) {
        const char* sql = R"SQL(
            INSERT INTO interlinear_word (
                verse_id, word_position_start, word_position_end,
                original_word, strongs_number, morphology, lemma, gloss, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        )SQL";

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            return false;
        }

        std::string metadata;
        if (!word.allStrongs.empty() && word.allStrongs != word.strongsNumber) {
            metadata = "{\"strongs\":\"" + SwordCommon::jsonEscape(word.allStrongs) + "\"}";
        }

        sqlite3_bind_int64(stmt, 1, verseId);
        sqlite3_bind_int(stmt, 2, word.startWord);
        sqlite3_bind_int(stmt, 3, word.endWord);
        bindTextOrNull(stmt, 4, word.originalWord);
        bindTextOrNull(stmt, 5, word.strongsNumber);
        bindTextOrNull(stmt, 6, word.morphology);
        bindTextOrNull(stmt, 7, word.strongsNumber.empty() ? "" : ("strong:" + word.strongsNumber));
        bindTextOrNull(stmt, 8, word.gloss);
        bindTextOrNull(stmt, 9, metadata);

        bool success = (sqlite3_step(stmt) == SQLITE_DONE);
        sqlite3_finalize(stmt);

        return success;
    }

    static void bindText(sqlite3_stmt* stmt, int index, const std::string& value) {
        sqlite3_bind_text(stmt, index, value.c_str(), -1, SQLITE_TRANSIENT);
    }

    static void bindTextOrNull(sqlite3_stmt* stmt, int index, const std::string& value) {
        if (value.empty()) {
            sqlite3_bind_null(stmt, index);
        } else {
            sqlite3_bind_text(stmt, index, value.c_str(), -1, SQLITE_TRANSIENT);
        }
    }

    /**
     * Execute SQL statement
     */
    bool executeSql(const std::string& sql) {
        char* errMsg = nullptr;
        int rc = sqlite3_exec(db, sql.c_str(), nullptr, nullptr, &errMsg);

        if (rc != SQLITE_OK) {
            std::cerr << "SQL error: " << (errMsg ? errMsg : "(unknown)") << std::endl;
            sqlite3_free(errMsg);
            return false;
        }

        return true;
    }

    /**
     * Cleanup temporary files
     */
    void cleanup() {
        if (!tempDir.empty() && fs::exists(tempDir)) {
            try {
                fs::remove_all(tempDir);
            } catch (const std::exception& e) {
                std::cerr << "Warning: Failed to cleanup temp directory: " << e.what() << std::endl;
            }
        }
    }
};

/**
 * Print usage information
 */
void printUsage(const char* programName) {
    std::cout << "Usage: " << programName << " --input <module.zip> --output <bible_xxx.db> [--range <start>-<end>]" << std::endl;
    std::cout << std::endl;
    std::cout << "Convert SWORD Bible module ZIP files to SQLite." << std::endl;
    std::cout << std::endl;
    std::cout << "Books outside the 66-book Protestant canon are skipped with a warning;" << std::endl;
    std::cout << "canonical books always keep their canonical numbering. Verses that the" << std::endl;
    std::cout << "source numbers differently (Rev 12:18, 3 John 15) are folded into their" << std::endl;
    std::cout << "canonical host verse, with the source numbering kept in formatting." << std::endl;
    std::cout << std::endl;
    std::cout << "Options:" << std::endl;
    std::cout << "  --input, -i    Input SWORD module ZIP file" << std::endl;
    std::cout << "  --output, -o   Output SQLite database file" << std::endl;
    std::cout << "  --range, -r    Optional book range to convert (e.g., 1-1 for Genesis," << std::endl;
    std::cout << "                 43-43 for John, 1-5 for Genesis through Deuteronomy)" << std::endl;
    std::cout << "                 Book numbers: 1=Gen, 43=John, 66=Revelation" << std::endl;
    std::cout << "  --allow-versification" << std::endl;
    std::cout << "                 Convert even when the source declares a non-KJV" << std::endl;
    std::cout << "                 versification. Refused by default because a whole-module" << std::endl;
    std::cout << "                 renumbering (e.g. the Masoretic division in SPE) is" << std::endl;
    std::cout << "                 invisible to book-level canon checks." << std::endl;
    std::cout << "  --versification NAME" << std::endl;
    std::cout << "                 Treat the module as numbered in NAME (e.g. MT) whatever its" << std::endl;
    std::cout << "                 .conf declares. For modules whose text is numbered in a" << std::endl;
    std::cout << "                 different system than declared; see" << std::endl;
    std::cout << "                 tools/import/sword/data/versification-overrides.json." << std::endl;
    std::cout << "  --codec none   Only 'none' is valid here: Bible verse text is never" << std::endl;
    std::cout << "                 compressed (design §3.4). Accepted for a uniform CLI." << std::endl;
    std::cout << "  --uuid UUID    Reuse this module_uuid instead of minting a fresh one" << std::endl;
    std::cout << "                 (reconversion; see scripts/data/module-uuid-map.json)." << std::endl;
    std::cout << "  --help, -h     Show this help message" << std::endl;
    std::cout << std::endl;
    std::cout << "Examples:" << std::endl;
    std::cout << "  " << programName << " --input KJV.zip --output bible_kjv.db" << std::endl;
    std::cout << "  " << programName << " -i KJV.zip -o bible_kjv_test.db --range 1-1     # Genesis only" << std::endl;
    std::cout << "  " << programName << " -i KJV.zip -o bible_kjv_test.db --range 43-43   # John only" << std::endl;
    std::cout << "  " << programName << " -i KJV.zip -o bible_kjv_test.db --range 1-1,43-43  # Genesis + John" << std::endl;
}

/**
 * Parse a book range string like "1-1,43-43" into pairs
 */
std::vector<std::pair<int,int>> parseBookRanges(const std::string& rangeStr) {
    std::vector<std::pair<int,int>> ranges;
    std::istringstream stream(rangeStr);
    std::string segment;

    while (std::getline(stream, segment, ',')) {
        size_t dash = segment.find('-');
        if (dash != std::string::npos) {
            int start = std::stoi(segment.substr(0, dash));
            int end = std::stoi(segment.substr(dash + 1));
            ranges.push_back({start, end});
        } else {
            int book = std::stoi(segment);
            ranges.push_back({book, book});
        }
    }

    return ranges;
}

int main(int argc, char* argv[]) {
    std::string inputFile;
    std::string outputFile;
    std::string rangeStr;
    bool allowVersification = false;
    std::string versificationOverride;
    std::string codec = SwordCommon::CODEC_NONE;
    std::string uuidOverride;

    // Parse command line arguments
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];

        if (arg == "--codec") {
            if (i + 1 < argc) {
                codec = argv[++i];
            } else {
                std::cerr << "Error: --codec requires a value (none|deflate|zstd)" << std::endl;
                return 1;
            }
        } else if (arg == "--uuid") {
            if (i + 1 < argc) {
                uuidOverride = argv[++i];
            } else {
                std::cerr << "Error: --uuid requires a value" << std::endl;
                return 1;
            }
        } else if (arg == "--allow-versification") {
            allowVersification = true;
        } else if (arg == "--versification") {
            if (i + 1 < argc) {
                versificationOverride = argv[++i];
            } else {
                std::cerr << "Error: --versification requires a system name" << std::endl;
                return 1;
            }
        } else if (arg == "--help" || arg == "-h") {
            printUsage(argv[0]);
            return 0;
        } else if (arg == "--input" || arg == "-i") {
            if (i + 1 < argc) {
                inputFile = argv[++i];
            } else {
                std::cerr << "Error: --input requires an argument" << std::endl;
                return 1;
            }
        } else if (arg == "--output" || arg == "-o") {
            if (i + 1 < argc) {
                outputFile = argv[++i];
            } else {
                std::cerr << "Error: --output requires an argument" << std::endl;
                return 1;
            }
        } else if (arg == "--range" || arg == "-r") {
            if (i + 1 < argc) {
                rangeStr = argv[++i];
            } else {
                std::cerr << "Error: --range requires an argument" << std::endl;
                return 1;
            }
        } else {
            std::cerr << "Error: Unknown option: " << arg << std::endl;
            printUsage(argv[0]);
            return 1;
        }
    }

    // Validate arguments
    if (inputFile.empty() || outputFile.empty()) {
        std::cerr << "Error: Both --input and --output are required" << std::endl;
        printUsage(argv[0]);
        return 1;
    }

    // Check input file exists
    if (!fs::exists(inputFile)) {
        std::cerr << "Error: Input file does not exist: " << inputFile << std::endl;
        return 1;
    }

    // Bible verse text is never compressed (design §3.4: "Ids and structure"
    // — verse text is excluded by TYPE, not by size). --codec is accepted for
    // a uniform CLI across all five converters, but only 'none' is valid here.
    if (codec != SwordCommon::CODEC_NONE) {
        std::cerr << "Error: --codec=" << codec << " is not valid for sword2bible; "
                  << "Bible verse text is never compressed (design §3.4)." << std::endl;
        return 1;
    }

    // Run conversion
    BibleConverter converter(inputFile, outputFile);
    converter.setAllowForeignVersification(allowVersification);
    converter.setVersificationOverride(versificationOverride);
    converter.setUuidOverride(uuidOverride);

    // Set book range filter if specified
    if (!rangeStr.empty()) {
        auto ranges = parseBookRanges(rangeStr);
        converter.setBookRanges(ranges);
        std::cout << "Book range filter: " << rangeStr << std::endl;
    }

    if (!converter.convert()) {
        std::cerr << "Conversion failed!" << std::endl;
        return 1;
    }

    return 0;
}
