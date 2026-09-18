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

namespace fs = std::filesystem;
using namespace sword;

// Global database handle
static sqlite3* db = nullptr;

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

    // Create schema
    const char* schema = R"SQL(
        -- Type-specific columns, as in the Bible repo's Dictionary.sql.
        -- dictionary_type is an open set: no CHECK, validated at the repository boundary.
        ALTER TABLE module_info ADD COLUMN dictionary_type TEXT NOT NULL DEFAULT 'bible_dictionary';
        ALTER TABLE module_info ADD COLUMN language_from TEXT;
        ALTER TABLE module_info ADD COLUMN language_to TEXT DEFAULT 'en';

        -- Dictionary entries.
        -- Scripture references belong in verse_link, where they are queryable, alongside every other
        -- module type's.
        CREATE TABLE dictionary_entry (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_key TEXT NOT NULL UNIQUE,
            word TEXT,
            transliteration TEXT,
            pronunciation TEXT,
            part_of_speech TEXT,
            definition TEXT NOT NULL,
            etymology TEXT,
            usage_notes TEXT,
            semantic_range TEXT,
            related_words TEXT,
            content_file TEXT,
            metadata TEXT
        );

        CREATE INDEX idx_entry_key ON dictionary_entry(entry_key);
        CREATE INDEX idx_entry_word ON dictionary_entry(word);

        -- Where a lexicon entry is actually used in a given translation. SWORD
        -- dictionaries carry no occurrence data, so this ships empty; it is part
        -- of the published contract and is populated by Strong's-mapping tooling.
        CREATE TABLE word_occurrence (
            occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_key TEXT NOT NULL,
            verse_id INTEGER NOT NULL,
            bible_module_uuid TEXT,
            translation_word TEXT,
            metadata TEXT,

            FOREIGN KEY (entry_key) REFERENCES dictionary_entry(entry_key) ON DELETE CASCADE
        );

        CREATE INDEX idx_occurrence_entry ON word_occurrence(entry_key);
        CREATE INDEX idx_occurrence_verse ON word_occurrence(verse_id);

        -- Full-text search
        -- Columns 0-1 are the keys (UNINDEXED); searchable columns follow. That
        -- ordering is load-bearing for positional highlight()/snippet() calls.
        CREATE VIRTUAL TABLE dictionary_entry_fts USING fts5(
            entry_id UNINDEXED,
            entry_key UNINDEXED,
            word,
            definition,
            usage_notes,
            content='dictionary_entry',
            content_rowid='entry_id',
            tokenize='porter unicode61'
        );

        -- FTS triggers (an external-content table needs the 'delete'
        -- command form; a plain UPDATE/DELETE leaves stale terms in the index)
        CREATE TRIGGER dictionary_entry_fts_insert AFTER INSERT ON dictionary_entry BEGIN
            INSERT INTO dictionary_entry_fts(rowid, entry_id, entry_key, word, definition, usage_notes)
            VALUES (new.entry_id, new.entry_id, new.entry_key, new.word, new.definition, new.usage_notes);
        END;

        CREATE TRIGGER dictionary_entry_fts_delete AFTER DELETE ON dictionary_entry BEGIN
            INSERT INTO dictionary_entry_fts(dictionary_entry_fts, rowid, entry_id, entry_key, word, definition, usage_notes)
            VALUES('delete', old.entry_id, old.entry_id, old.entry_key, old.word, old.definition, old.usage_notes);
        END;

        CREATE TRIGGER dictionary_entry_fts_update AFTER UPDATE ON dictionary_entry BEGIN
            INSERT INTO dictionary_entry_fts(dictionary_entry_fts, rowid, entry_id, entry_key, word, definition, usage_notes)
            VALUES('delete', old.entry_id, old.entry_id, old.entry_key, old.word, old.definition, old.usage_notes);
            INSERT INTO dictionary_entry_fts(rowid, entry_id, entry_key, word, definition, usage_notes)
            VALUES (new.entry_id, new.entry_id, new.entry_key, new.word, new.definition, new.usage_notes);
        END;

        -- Schema version
        CREATE TABLE schema_version (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_number TEXT NOT NULL,
            applied_date TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            metadata TEXT
        );

        INSERT INTO schema_version (version_number, notes)
        VALUES ('0.1.0', 'Dictionary module schema');
    )SQL";

    char* errMsg = nullptr;
    if (sqlite3_exec(db, SwordCommon::MODULE_INFO_SCHEMA_SQL, nullptr, nullptr, &errMsg) != SQLITE_OK) {
        std::cerr << "Failed to create module_info: " << (errMsg ? errMsg : "(unknown)") << "\n";
        sqlite3_free(errMsg);
        return false;
    }

    if (sqlite3_exec(db, schema, nullptr, nullptr, &errMsg) != SQLITE_OK) {
        std::cerr << "Failed to create schema: " << (errMsg ? errMsg : "(unknown)") << "\n";
        sqlite3_free(errMsg);
        return false;
    }

    // One verse_link table, identical in every module database.
    if (sqlite3_exec(db, SwordCommon::VERSE_LINK_SCHEMA_SQL, nullptr, nullptr, &errMsg) != SQLITE_OK) {
        std::cerr << "Failed to create verse_link: " << (errMsg ? errMsg : "(unknown)") << "\n";
        sqlite3_free(errMsg);
        return false;
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
            content_version, metadata
        ) VALUES (1, ?, 'dictionary', 'dictionary-module', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    )SQL";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        std::cerr << "Failed to prepare module_info insert: " << sqlite3_errmsg(db) << "\n";
        return false;
    }

    int i = 1;
    sqlite3_bind_text(stmt, i++, identity.uuid.c_str(), -1, SQLITE_TRANSIENT);
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
    std::string content;
    content.reserve(4u * 1024u * 1024u);

    sqlite3_stmt* select = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT entry_key, definition FROM dictionary_entry ORDER BY entry_id",
            -1, &select, nullptr) != SQLITE_OK) {
        return false;
    }
    while (sqlite3_step(select) == SQLITE_ROW) {
        const unsigned char* key = sqlite3_column_text(select, 0);
        const unsigned char* def = sqlite3_column_text(select, 1);
        if (key) content += reinterpret_cast<const char*>(key);
        content += '\t';
        if (def) content += reinterpret_cast<const char*>(def);
        content += '\n';
    }
    sqlite3_finalize(select);

    const std::string hash = SwordCommon::sha256Hex(content);

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
    std::cout << "  --help, -h    Show this help message\n\n";
    std::cout << "Examples:\n";
    std::cout << "  " << programName << " --input Webster1828.zip --output dictionary_webster.db\n";
    std::cout << "  " << programName << " --input br_en.zip --output dictionary_breton_english.db\n";
}
