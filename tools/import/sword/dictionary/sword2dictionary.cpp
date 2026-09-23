/**
 * sword2dictionary.cpp
 *
 * Converts SWORD dictionary/lexicon module ZIP files into SQLite database
 * format (see packages/core/docs/features/module-format.md and
 * packages/core/sql/schemas/initial/Dictionary.sql in the Bible repo).
 *
 * Notes:
 *   - Scripture references live in the shared `verse_link` table, like every
 *     other module type's do.
 *   - module_info carries the full identity/provenance block.
 *   - FTS5 triggers use the external-content 'delete' command form.
 *   - dictionary_type is an open, extensible set validated at the repository
 *     boundary, not pinned by a CHECK enum.
 *
 * Usage:
 *   ./sword2dictionary --input <module.zip> --output <dictionary_xxx.db>
 *
 * Example:
 *   ./sword2dictionary --input Webster1828.zip --output dictionary_webster.db
 */

#include <iostream>
#include <sstream>
#include <string>
#include <map>
#include <filesystem>
#include <cstdlib>
#include <cstring>
#include <algorithm>

// SWORD library
#include <swmgr.h>
#include <swmodule.h>

// SQLite
#include <sqlite3.h>

// Common library
#include "sword_common.h"
#include "schema_bridge.h"
#include "compression.h"
#include "content_digest.h"

namespace fs = std::filesystem;
using namespace sword;

// Global database handle
static sqlite3* db = nullptr;

// --codec/--compress and --uuid (task 0035 requirements 3 and 5).
static std::string g_codecOverride;
static std::string g_uuidOverride;
static SwordCommon::CompressionOutcome g_compression;

// The identity block, kept so finalisation can rebuild metadata without needing
// SQLite's JSON1 extension.
static SwordCommon::ModuleIdentity g_identity;

// Forward declarations
bool createDatabase(const std::string& dbPath);
bool parseModuleConfig(const fs::path& tempDir, const std::string& moduleName, std::string& dictionaryType);
bool insertModuleInfo(const SwordCommon::ModuleIdentity& identity,
                      const std::string& dictionaryType,
                      const std::string& languageFrom,
                      const std::string& languageTo);
bool convertEntries(SWModule* module);
bool insertEntry(const std::string& entryKey, const std::string& word,
                 const std::string& definition);
bool finalizeModuleInfo();
std::string detectDictionaryType(const std::string& moduleName, const std::string& description,
                                  const std::string& category);
void printUsage(const char* programName);

// Module filter for dictionary/lexicon modules
bool isDictionaryModule(sword::SWModule* mod, const fs::path& modulePath);

/**
 * Main entry point
 */
int main(int argc, char* argv[]) {
    std::string inputZip;
    std::string outputDb;

    // Parse command-line arguments
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--input") == 0 || strcmp(argv[i], "-i") == 0) {
            if (i + 1 < argc) inputZip = argv[++i];
        } else if (strcmp(argv[i], "--output") == 0 || strcmp(argv[i], "-o") == 0) {
            if (i + 1 < argc) outputDb = argv[++i];
        } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            printUsage(argv[0]);
            return 0;
        } else if ((strcmp(argv[i], "--codec") == 0 || strcmp(argv[i], "--compress") == 0) && i + 1 < argc) {
            g_codecOverride = argv[++i];
        } else if (strcmp(argv[i], "--uuid") == 0 && i + 1 < argc) {
            g_uuidOverride = argv[++i];
        }
    }

    if (inputZip.empty() || outputDb.empty()) {
        printUsage(argv[0]);
        return 1;
    }

    std::cout << "SWORD Dictionary to SQLite Converter (format version "
              << SwordCommon::FORMAT_VERSION << ")\n";
    std::cout << "=====================================\n\n";
    std::cout << "Input:  " << inputZip << "\n";
    std::cout << "Output: " << outputDb << "\n\n";

    // Create temporary directory for extraction
    fs::path tempDir = SwordCommon::createTempDirectory("sword_dictionary_");

    try {
        // Step 1: Extract ZIP
        std::cout << "[1/5] Extracting ZIP file...\n";
        if (!SwordCommon::extractZipFile(inputZip, tempDir)) {
            std::cerr << "Failed to extract ZIP file\n";
            return 1;
        }

        // Step 2: Initialize SWORD
        std::cout << "[2/5] Initializing SWORD library...\n";
        SWMgr* mgr = nullptr;
        SWModule* module = nullptr;
        if (!SwordCommon::initializeSWORD(tempDir, &mgr, &module, isDictionaryModule)) {
            std::cerr << "Failed to initialize SWORD module\n";
            fs::remove_all(tempDir);
            return 1;
        }

        std::string moduleName = module->getName();
        std::cout << "  Module: " << moduleName << "\n";
        std::cout << "  Description: " << module->getDescription() << "\n";

        // Step 3: Create database
        std::cout << "[3/5] Creating SQLite database...\n";
        if (!createDatabase(outputDb)) {
            std::cerr << "Failed to create database\n";
            delete mgr;
            fs::remove_all(tempDir);
            return 1;
        }

        // Step 4: Parse module config
        std::cout << "[4/5] Parsing module metadata...\n";
        std::string dictionaryType;
        if (!parseModuleConfig(tempDir, moduleName, dictionaryType)) {
            std::cerr << "Warning: Could not parse all module metadata\n";
        }

        // Step 5: Convert entries
        std::cout << "[5/5] Converting dictionary entries...\n";
        if (!convertEntries(module)) {
            std::cerr << "Failed to convert entries\n";
            sqlite3_close(db);
            delete mgr;
            fs::remove_all(tempDir);
            return 1;
        }

        finalizeModuleInfo();

        // Cleanup
        sqlite3_close(db);
        delete mgr;
        fs::remove_all(tempDir);

        std::cout << "\nConversion complete!\n";
        std::cout << "Output database: " << outputDb << "\n";

        return 0;

    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << "\n";
        if (db) sqlite3_close(db);
        fs::remove_all(tempDir);
        return 1;
    }
}

/**
 * Module filter for dictionary/lexicon/glossary modules
 */
bool isDictionaryModule(sword::SWModule* mod, const fs::path& modulePath) {
    std::string moduleType = mod->getType();
    std::string dataPath = mod->getConfigEntry("DataPath") ? mod->getConfigEntry("DataPath") : "";
    std::string category = mod->getConfigEntry("Category") ? mod->getConfigEntry("Category") : "";
    std::string feature = mod->getConfigEntry("Feature") ? mod->getConfigEntry("Feature") : "";

    // Check if module data actually exists in our temp directory
    bool dataExists = SwordCommon::moduleDataExists(modulePath, dataPath);

    // Accept dictionary/lexicon/glossary modules
    return dataExists &&
           (moduleType == "Lexicons / Dictionaries" ||
            moduleType == "Lexicons" ||
            moduleType == "Lexicon" ||
            moduleType == "Dictionaries" ||
            moduleType == "Generic Books" ||  // Some dictionaries are categorized as generic books
            category == "Glossaries" ||
            feature == "Glossary");
}

/**
 * Create the SQLite database schema
 */
bool createDatabase(const std::string& dbPath) {
    if (sqlite3_open(dbPath.c_str(), &db) != SQLITE_OK) {
        std::cerr << "Cannot create database: " << sqlite3_errmsg(db) << "\n";
        return false;
    }

    // Enable pragmas using common library
    if (!SwordCommon::setSQLitePragmas(db, 16)) { // 16MB cache
        return false;
    }

    // Schema (module_info + dictionary_type/language_from/language_to,
    // dictionary_entry, word_occurrence, module_feature,
    // compression_dictionary, verse_link — no FTS, no redundant indexes) is
    // loaded from the Bible repo, not hand-copied (task 0035 / design §6.1 —
    // see schema_bridge.h).
    char* errMsg = nullptr;
    std::string schema = SwordCommon::loadRepoSchema("Dictionary.sql");
    if (sqlite3_exec(db, schema.c_str(), nullptr, nullptr, &errMsg) != SQLITE_OK) {
        std::cerr << "Failed to create schema from Dictionary.sql: " << (errMsg ? errMsg : "(unknown)") << "\n";
        sqlite3_free(errMsg);
        return false;
    }

    // schema_version is this repo's own build-provenance bookkeeping, not
    // part of the module format schema — IF NOT EXISTS so it is harmless
    // whether or not Dictionary.sql also declares one.
    if (sqlite3_exec(db, R"SQL(
        CREATE TABLE IF NOT EXISTS schema_version (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_number TEXT NOT NULL,
            applied_date TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            metadata TEXT
        );
    )SQL", nullptr, nullptr, &errMsg) != SQLITE_OK) {
        std::cerr << "Failed to create schema_version: " << (errMsg ? errMsg : "(unknown)") << "\n";
        sqlite3_free(errMsg);
        return false;
    }
    {
        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db, "INSERT INTO schema_version (version_number, notes) VALUES (?, ?)",
                               -1, &stmt, nullptr) == SQLITE_OK) {
            sqlite3_bind_text(stmt, 1, SwordCommon::FORMAT_VERSION, -1, SQLITE_STATIC);
            sqlite3_bind_text(stmt, 2, "Dictionary module schema", -1, SQLITE_STATIC);
            sqlite3_step(stmt);
            sqlite3_finalize(stmt);
        }
    }

    return true;
}

/**
 * Detect dictionary type from module name, description, and category
 */
std::string detectDictionaryType(const std::string& moduleName, const std::string& description,
                                  const std::string& category) {
    std::string lowerName = SwordCommon::toLower(moduleName);
    std::string lowerDesc = SwordCommon::toLower(description);
    std::string lowerCategory = SwordCommon::toLower(category);

    // Glossaries are language-to-language, not topical; dictionary_type is an
    // open set, so they can say what they are.
    if (lowerCategory.find("glossar") != std::string::npos ||
        lowerDesc.find("glossary") != std::string::npos) {
        return "glossary";
    }

    // Check for Strong's
    if (lowerName.find("strong") != std::string::npos ||
        lowerDesc.find("strong") != std::string::npos) {
        return "strongs";
    }

    // Check for Greek lexicon
    if (lowerName.find("greek") != std::string::npos ||
        lowerName.find("thayer") != std::string::npos ||
        lowerName.find("bdag") != std::string::npos ||
        lowerName.find("liddell") != std::string::npos) {
        return "greek_lexicon";
    }

    // Check for Hebrew lexicon
    if (lowerName.find("hebrew") != std::string::npos ||
        lowerName.find("bdb") != std::string::npos ||
        lowerName.find("halot") != std::string::npos) {
        return "hebrew_lexicon";
    }

    // Default to bible dictionary
    return "bible_dictionary";
}

/**
 * Parse module configuration file and insert the identity block
 */
bool parseModuleConfig(const fs::path& tempDir, const std::string& moduleName, std::string& dictionaryType) {
    auto config = SwordCommon::parseModuleConfig(tempDir, moduleName);
    g_identity = SwordCommon::deriveModuleIdentity(config, "dictionary", moduleName);

    auto get = [&config](const std::string& key) -> std::string {
        auto it = config.find(key);
        return (it == config.end()) ? std::string() : it->second;
    };

    const std::string category = get("Category");

    // For glossaries, extract language_from and language_to
    const std::string languageFrom = get("GlossaryFrom");
    std::string languageTo = get("GlossaryTo");
    if (languageTo.empty()) languageTo = g_identity.languageCode;

    dictionaryType = detectDictionaryType(moduleName,
                                          g_identity.fullName + " " + g_identity.description,
                                          category);

    std::cout << "  Module UUID: " << g_identity.uuid << "\n";
    std::cout << "  Dictionary type: " << dictionaryType << "\n";
    std::cout << "  Licence: "
              << (g_identity.licenseSpdx.empty() ? "(unknown)" : g_identity.licenseSpdx) << "\n";

    return insertModuleInfo(g_identity, dictionaryType, languageFrom, languageTo);
}

static void bindTextOrNull(sqlite3_stmt* stmt, int index, const std::string& value) {
    if (value.empty()) {
        sqlite3_bind_null(stmt, index);
    } else {
        sqlite3_bind_text(stmt, index, value.c_str(), -1, SQLITE_TRANSIENT);
    }
}

/**
 * Insert module metadata
 */
bool insertModuleInfo(const SwordCommon::ModuleIdentity& identity,
                      const std::string& dictionaryType,
                      const std::string& languageFrom,
                      const std::string& languageTo) {
    const char* sql = R"SQL(
        INSERT INTO module_info (
            info_id, module_uuid, module_type, format, format_version,
            abbreviation, full_name, dictionary_type, language_from, language_to,
            language_code, author, publisher, year_published, copyright,
            license_spdx, license_url, source_url, description,
            content_version, metadata, compression
        ) VALUES (1, ?, 'dictionary', 'dictionary-module', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    )SQL";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        std::cerr << "Failed to prepare module_info insert: " << sqlite3_errmsg(db) << "\n";
        return false;
    }

    // --uuid overrides the minted identity (task 0035 requirement 5).
    const std::string uuid = !g_uuidOverride.empty() ? g_uuidOverride : identity.uuid;

    int i = 1;
    sqlite3_bind_text(stmt, i++, uuid.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, i++, SwordCommon::FORMAT_VERSION, -1, SQLITE_STATIC);
    sqlite3_bind_text(stmt, i++, identity.abbreviation.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, i++, identity.fullName.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, i++, dictionaryType.c_str(), -1, SQLITE_TRANSIENT);
    bindTextOrNull(stmt, i++, languageFrom);
    sqlite3_bind_text(stmt, i++, languageTo.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, i++, identity.languageCode.c_str(), -1, SQLITE_TRANSIENT);
    bindTextOrNull(stmt, i++, identity.author);
    bindTextOrNull(stmt, i++, identity.publisher);

    if (identity.yearPublished > 0) {
        sqlite3_bind_int(stmt, i++, identity.yearPublished);
    } else {
        sqlite3_bind_null(stmt, i++);
    }

    bindTextOrNull(stmt, i++, identity.copyright);
    bindTextOrNull(stmt, i++, identity.licenseSpdx);
    bindTextOrNull(stmt, i++, identity.licenseUrl);
    bindTextOrNull(stmt, i++, identity.sourceUrl);
    bindTextOrNull(stmt, i++, identity.description);
    bindTextOrNull(stmt, i++, identity.contentVersion);
    bindTextOrNull(stmt, i++, identity.metadataJson);
    sqlite3_bind_text(stmt, i++, SwordCommon::CODEC_NONE, -1, SQLITE_STATIC); // compression: set for real by finalizeModuleInfo()

    bool success = (sqlite3_step(stmt) == SQLITE_DONE);
    if (!success) {
        std::cerr << "Failed to insert module_info: " << sqlite3_errmsg(db) << "\n";
    }
    sqlite3_finalize(stmt);

    return success;
}

/**
 * Convert all dictionary entries
 */
bool convertEntries(SWModule* module) {
    // Set to first entry
    module->setPosition(sword::TOP);

    // Begin transaction for performance
    sqlite3_exec(db, "BEGIN TRANSACTION", nullptr, nullptr, nullptr);

    int entryCount = 0;
    int dotCount = 0;
    int duplicateCount = 0;

    // Iterate through all entries
    while (!module->popError()) {
        const char* keyRaw = module->getKeyText();
        // renderText() returns SWBuf BY VALUE; binding it to a `const char*`
        // leaves a dangling pointer once the temporary dies. Hold the buffer.
        const sword::SWBuf renderedBuf = module->renderText();
        const std::string entryKey = keyRaw ? std::string(keyRaw) : std::string();
        const std::string text =
            renderedBuf.length() ? std::string(renderedBuf.c_str()) : std::string();

        // Skip empty entries
        if (!SwordCommon::trim(text).empty() && !entryKey.empty()) {
            // Strip markup and normalise whitespace for the definition.
            const std::string definition = SwordCommon::stripMarkupClean(text);

            if (!definition.empty()) {
                if (!insertEntry(entryKey, entryKey, definition)) {
                    // Check if it's a duplicate key error (SQLITE_CONSTRAINT)
                    int sqliteError = sqlite3_errcode(db);
                    if (sqliteError == SQLITE_CONSTRAINT) {
                        // Skip duplicate entries
                        duplicateCount++;
                    } else {
                        std::cerr << "Failed to insert entry: " << entryKey
                                  << " (error: " << sqlite3_errmsg(db) << ")\n";
                        sqlite3_exec(db, "ROLLBACK", nullptr, nullptr, nullptr);
                        return false;
                    }
                } else {
                    entryCount++;
                }

                // Progress indicator
                if (entryCount % 100 == 0 && entryCount > 0) {
                    std::cout << ".";
                    std::cout.flush();
                    dotCount++;
                    if (dotCount % 50 == 0) {
                        std::cout << " " << entryCount << "\n";
                    }
                }
            }
        }

        (*module)++;
    }

    sqlite3_exec(db, "COMMIT", nullptr, nullptr, nullptr);

    std::cout << "\n  Total entries: " << entryCount << "\n";
    if (duplicateCount > 0) {
        std::cout << "  Skipped duplicates: " << duplicateCount << "\n";
    }
    return true;
}

/**
 * Insert dictionary entry
 */
bool insertEntry(const std::string& entryKey, const std::string& word,
                 const std::string& definition) {
    const char* sql = R"SQL(
        INSERT INTO dictionary_entry (
            entry_key, word, definition
        ) VALUES (?, ?, ?)
    )SQL";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        return false;
    }

    sqlite3_bind_text(stmt, 1, entryKey.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, word.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, definition.c_str(), -1, SQLITE_TRANSIENT);

    bool success = (sqlite3_step(stmt) == SQLITE_DONE);
    sqlite3_finalize(stmt);

    return success;
}

/**
 * Record the SHA-256 of the content we stored.
 */
bool finalizeModuleInfo() {
    // Compress (design §3: eligible at >=8MB decoded prose across
    // definition+usage_notes, or --codec/--compress overrides), THEN hash.
    g_compression = SwordCommon::applyCompression(
        db, "dictionary_entry", {"definition", "usage_notes"}, g_codecOverride);
    std::cout << "  Compression: " << g_compression.codec;
    if (g_compression.codec != SwordCommon::CODEC_NONE) {
        std::cout << " (" << g_compression.rowsCompressed << " rows compressed, "
                  << g_compression.dictionary.size() << "-byte dictionary)";
    }
    std::cout << "\n";

    // Canonical, codec-invariant digest (design §2.7). CONTENT_MAP's indexed
    // set for dictionary is ['word','definition','usage_notes']; 'word' is
    // never compressed (not in the prose set).
    const std::string hash = SwordCommon::computeContentSha256(
        db, "dictionary_entry", "entry_id", {"word", "definition", "usage_notes"},
        {"definition", "usage_notes"}, g_compression.codec, g_compression.dictionary);

    const char* sql = "UPDATE module_info SET content_sha256 = ? WHERE info_id = 1";
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        return false;
    }
    sqlite3_bind_text(stmt, 1, hash.c_str(), -1, SQLITE_TRANSIENT);
    const bool success = (sqlite3_step(stmt) == SQLITE_DONE);
    sqlite3_finalize(stmt);

    if (success) {
        std::cout << "  content_sha256: " << hash << "\n";
    }
    return success;
}

/**
 * Print usage information
 */
void printUsage(const char* programName) {
    std::cout << "SWORD Dictionary/Glossary to SQLite Converter\n\n";
    std::cout << "Usage:\n";
    std::cout << "  " << programName << " --input <module.zip> --output <dictionary_xxx.db>\n\n";
    std::cout << "Options:\n";
    std::cout << "  --input, -i   Input SWORD module ZIP file\n";
    std::cout << "  --output, -o  Output SQLite database file\n";
    std::cout << "  --codec, --compress none|deflate|zstd\n";
    std::cout << "                Override the publisher default (deflate if >=8MB decoded\n";
    std::cout << "                prose, else none; design §3.4).\n";
    std::cout << "  --uuid UUID   Reuse this module_uuid instead of minting a fresh one\n";
    std::cout << "                (reconversion; see scripts/data/module-uuid-map.json).\n";
    std::cout << "  --help, -h    Show this help message\n\n";
    std::cout << "Examples:\n";
    std::cout << "  " << programName << " --input Webster1828.zip --output dictionary_webster.db\n";
    std::cout << "  " << programName << " --input br_en.zip --output dictionary_breton_english.db\n";
}
