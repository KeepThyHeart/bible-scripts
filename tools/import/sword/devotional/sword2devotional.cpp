/**
 * sword2devotional.cpp
 *
 * Converts SWORD devotional module ZIP files into SQLite database format
 * (see packages/core/docs/features/module-format.md and
 * packages/core/sql/schemas/initial/Devotional.sql in the Bible repo).
 *
 * Notes:
 *   - Scripture references live in the shared `verse_link` table.
 *   - module_info carries the full identity/provenance block.
 *   - FTS5 triggers use the external-content 'delete' command form.
 *   - devotional_entry has `sort_order`, because `day_number` is nullable
 *     for devotional_type='continuous'.
 *   - devotional_type is an open set, not pinned by a CHECK enum.
 *
 * Usage:
 *   ./sword2devotional --input <module.zip> --output <devotional_xxx.db>
 *
 * Example:
 *   ./sword2devotional --input SME.zip --output devotional_spurgeon_morning.db
 */

#include <iostream>
#include <sstream>
#include <string>
#include <filesystem>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <map>

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

// The identity block, kept so finalisation can record the content hash.
static SwordCommon::ModuleIdentity g_identity;

// Forward declarations
bool createDatabase(const std::string& dbPath);
bool parseModuleConfig(const fs::path& tempDir, const std::string& moduleName, std::string& devotionalType);
bool insertModuleInfo(const SwordCommon::ModuleIdentity& identity,
                      const std::string& devotionalType, int totalDays);
bool convertEntries(SWModule* module);
bool insertEntry(int dayNumber, const std::string& dateLabel, const std::string& title,
                 const std::string& content, int sortOrder);
bool finalizeModuleInfo();
std::string detectDevotionalType(const std::string& moduleName, const std::string& description);
std::string extractDateLabel(const std::string& keyText);
void printUsage(const char* programName);

/**
 * Module filter for devotional modules
 */
bool isDevotionalModule(sword::SWModule* mod, const fs::path& modulePath) {
    std::string moduleType = mod->getType();
    std::string dataPath = mod->getConfigEntry("DataPath") ? mod->getConfigEntry("DataPath") : "";
    std::string category = mod->getConfigEntry("Category") ? mod->getConfigEntry("Category") : "";
    std::string feature = mod->getConfigEntry("Feature") ? mod->getConfigEntry("Feature") : "";

    // Check if module data actually exists in our temp directory
    bool dataExists = SwordCommon::moduleDataExists(modulePath, dataPath);

    // Accept daily devotional modules by type, category, or feature
    return dataExists &&
           (moduleType == "Daily Devotional" ||
            moduleType == "Devotionals" ||
            moduleType == "Generic Books" ||  // Some devotionals are categorized as generic books
            category == "Daily Devotional" ||
            feature.find("DailyDevotion") != std::string::npos);
}

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

    std::cout << "SWORD Devotional to SQLite Converter\n";
    std::cout << "====================================\n\n";
    std::cout << "Input:  " << inputZip << "\n";
    std::cout << "Output: " << outputDb << "\n\n";

    // Create temporary directory for extraction
    fs::path tempDir = SwordCommon::createTempDirectory("sword_devotional_");

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
        if (!SwordCommon::initializeSWORD(tempDir, &mgr, &module, isDevotionalModule)) {
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
        std::string devotionalType;
        if (!parseModuleConfig(tempDir, moduleName, devotionalType)) {
            std::cerr << "Warning: Could not parse all module metadata\n";
        }

        // Step 5: Convert entries
        std::cout << "[5/5] Converting devotional entries...\n";
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
 * Create SQLite database with schema
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
        -- Type-specific columns, as in the Bible repo's Devotional.sql.
        ALTER TABLE module_info ADD COLUMN devotional_type TEXT NOT NULL DEFAULT 'continuous'
            CHECK (devotional_type IN ('day_of_year', 'fixed_length', 'continuous'));
        ALTER TABLE module_info ADD COLUMN total_days INTEGER;

        -- Devotional entries.
        -- Parsed scripture references belong in verse_link.
        -- `scripture_reference` is the display string exactly as the author
        -- wrote it.
        -- sort_order is explicit because day_number is nullable for
        -- devotional_type='continuous'.
        CREATE TABLE devotional_entry (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            day_number INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0,
            date_label TEXT,
            title TEXT,
            content TEXT NOT NULL,
            scripture_reference TEXT,
            scripture_text TEXT,
            author_note TEXT,
            metadata TEXT,
            UNIQUE(day_number)
        );

        CREATE INDEX idx_devotional_day ON devotional_entry(day_number);
        CREATE INDEX idx_devotional_date ON devotional_entry(date_label);
        CREATE INDEX idx_devotional_sort ON devotional_entry(sort_order);

        -- Full-text search
        -- Column 0 is the key (UNINDEXED); searchable columns follow. That
        -- ordering is load-bearing for positional highlight()/snippet() calls.
        CREATE VIRTUAL TABLE devotional_entry_fts USING fts5(
            entry_id UNINDEXED,
            title,
            content,
            content='devotional_entry',
            content_rowid='entry_id',
            tokenize='porter unicode61'
        );

        -- FTS triggers (an external-content table needs the 'delete'
        -- command form; a plain UPDATE/DELETE leaves stale terms in the index)
        CREATE TRIGGER devotional_entry_fts_insert AFTER INSERT ON devotional_entry BEGIN
            INSERT INTO devotional_entry_fts(rowid, entry_id, title, content)
            VALUES (new.entry_id, new.entry_id, new.title, new.content);
        END;

        CREATE TRIGGER devotional_entry_fts_delete AFTER DELETE ON devotional_entry BEGIN
            INSERT INTO devotional_entry_fts(devotional_entry_fts, rowid, entry_id, title, content)
            VALUES('delete', old.entry_id, old.entry_id, old.title, old.content);
        END;

        CREATE TRIGGER devotional_entry_fts_update AFTER UPDATE ON devotional_entry BEGIN
            INSERT INTO devotional_entry_fts(devotional_entry_fts, rowid, entry_id, title, content)
            VALUES('delete', old.entry_id, old.entry_id, old.title, old.content);
            INSERT INTO devotional_entry_fts(rowid, entry_id, title, content)
            VALUES (new.entry_id, new.entry_id, new.title, new.content);
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
        VALUES ('0.1.0', 'Devotional module schema');
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
 * Detect devotional type from module name and description
 */
std::string detectDevotionalType(const std::string& moduleName, const std::string& description) {
    std::string lowerName = SwordCommon::toLower(moduleName);
    std::string lowerDesc = SwordCommon::toLower(description);

    // Check for 365-day devotionals
    if (lowerDesc.find("365") != std::string::npos ||
        lowerDesc.find("daily") != std::string::npos ||
        lowerDesc.find("year") != std::string::npos) {
        return "day_of_year";
    }

    // Check for fixed-length devotionals
    if (lowerDesc.find("40 day") != std::string::npos ||
        lowerDesc.find("90 day") != std::string::npos ||
        lowerDesc.find("30 day") != std::string::npos) {
        return "fixed_length";
    }

    // Default to day_of_year for most devotionals
    return "day_of_year";
}

/**
 * Extract date label from key text (e.g., "01.01" -> "January 1")
 */
std::string extractDateLabel(const std::string& keyText) {
    // For now, just return the key text as-is
    // Could be enhanced to parse month.day format and convert to readable labels
    return keyText;
}

/**
 * Parse module configuration file
 */
bool parseModuleConfig(const fs::path& tempDir, const std::string& moduleName, std::string& devotionalType) {
    auto config = SwordCommon::parseModuleConfig(tempDir, moduleName);
    g_identity = SwordCommon::deriveModuleIdentity(config, "devotional", moduleName);

    devotionalType = detectDevotionalType(moduleName,
                                          g_identity.fullName + " " + g_identity.description);

    // Estimate total days (corrected after conversion from the real entry count)
    int totalDays = (devotionalType == "day_of_year") ? 365 : 0;

    std::cout << "  Module UUID: " << g_identity.uuid << "\n";
    std::cout << "  Devotional type: " << devotionalType << "\n";
    std::cout << "  Licence: "
              << (g_identity.licenseSpdx.empty() ? "(unknown)" : g_identity.licenseSpdx) << "\n";

    return insertModuleInfo(g_identity, devotionalType, totalDays);
}

static void bindTextOrNull(sqlite3_stmt* stmt, int index, const std::string& value) {
    if (value.empty()) {
        sqlite3_bind_null(stmt, index);
    } else {
        sqlite3_bind_text(stmt, index, value.c_str(), -1, SQLITE_TRANSIENT);
    }
}

/**
 * Insert module metadata (identity + provenance)
 */
bool insertModuleInfo(const SwordCommon::ModuleIdentity& identity,
                      const std::string& devotionalType, int totalDays) {
    const char* sql = R"SQL(
        INSERT INTO module_info (
            info_id, module_uuid, module_type, format, format_version,
            abbreviation, full_name, author, publisher, year_published,
            copyright, license_spdx, license_url, source_url, description,
            language_code, devotional_type, total_days,
            versification, content_version, metadata
        ) VALUES (1, ?, 'devotional', 'devotional-module', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    sqlite3_bind_text(stmt, i++, identity.languageCode.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, i++, devotionalType.c_str(), -1, SQLITE_TRANSIENT);

    if (totalDays > 0) {
        sqlite3_bind_int(stmt, i++, totalDays);
    } else {
        sqlite3_bind_null(stmt, i++);
    }

    sqlite3_bind_text(stmt, i++, SwordCommon::VERSIFICATION, -1, SQLITE_STATIC);
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
 * Record the content hash and correct total_days from the real entry
 * count (the value inserted earlier was only an estimate from the description).
 */
bool finalizeModuleInfo() {
    std::string content;
    content.reserve(2u * 1024u * 1024u);
    int entryCount = 0;

    sqlite3_stmt* select = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT day_number, date_label, content FROM devotional_entry ORDER BY sort_order",
            -1, &select, nullptr) != SQLITE_OK) {
        return false;
    }
    while (sqlite3_step(select) == SQLITE_ROW) {
        entryCount++;
        content += std::to_string(sqlite3_column_int(select, 0));
        content += '\t';
        const unsigned char* label = sqlite3_column_text(select, 1);
        if (label) content += reinterpret_cast<const char*>(label);
        content += '\t';
        const unsigned char* body = sqlite3_column_text(select, 2);
        if (body) content += reinterpret_cast<const char*>(body);
        content += '\n';
    }
    sqlite3_finalize(select);

    const std::string hash = SwordCommon::sha256Hex(content);

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db,
            "UPDATE module_info SET content_sha256 = ?, total_days = ? WHERE info_id = 1",
            -1, &stmt, nullptr) != SQLITE_OK) {
        return false;
    }
    sqlite3_bind_text(stmt, 1, hash.c_str(), -1, SQLITE_TRANSIENT);
    if (entryCount > 0) {
        sqlite3_bind_int(stmt, 2, entryCount);
    } else {
        sqlite3_bind_null(stmt, 2);
    }
    const bool success = (sqlite3_step(stmt) == SQLITE_DONE);
    sqlite3_finalize(stmt);

    if (success) {
        std::cout << "  content_sha256: " << hash << "\n";
        std::cout << "  total_days: " << entryCount << "\n";
    }
    return success;
}

/**
 * Convert all devotional entries
 */
bool convertEntries(SWModule* module) {
    // Set to first entry
    module->setPosition(sword::TOP);

    // Begin transaction for performance
    sqlite3_exec(db, "BEGIN TRANSACTION", nullptr, nullptr, nullptr);

    int entryCount = 0;
    int dotCount = 0;
    int duplicateCount = 0;
    int dayNumber = 1;
    int sortOrder = 0;

    // Iterate through all entries
    while (!module->popError()) {
        const char* keyRaw = module->getKeyText();
        // renderText() returns SWBuf BY VALUE; binding it to a `const char*`
        // leaves a dangling pointer once the temporary dies. Hold the buffer.
        const sword::SWBuf renderedBuf = module->renderText();
        std::string keyText = keyRaw ? std::string(keyRaw) : std::string();
        std::string text = renderedBuf.length() ? std::string(renderedBuf.c_str()) : std::string();

        // Skip empty entries
        if (!SwordCommon::trim(text).empty() && !keyText.empty()) {
            // Extract date label (could be "01.01" or "January 1" depending on module)
            std::string dateLabel = extractDateLabel(keyText);

            // Strip markup and normalise whitespace for the content
            std::string content = SwordCommon::stripMarkupClean(text);

            sortOrder++;

            // Insert entry (title is empty for most devotionals, it's in the content)
            if (!insertEntry(dayNumber, dateLabel, "", content, sortOrder)) {
                // Check if it's a duplicate key error (SQLITE_CONSTRAINT)
                int sqliteError = sqlite3_errcode(db);
                if (sqliteError == SQLITE_CONSTRAINT) {
                    // Skip duplicate entries
                    duplicateCount++;
                } else {
                    std::cerr << "Failed to insert entry: " << keyText << " (error: " << sqlite3_errmsg(db) << ")\n";
                    sqlite3_exec(db, "ROLLBACK", nullptr, nullptr, nullptr);
                    return false;
                }
            }

            entryCount++;
            dayNumber++;

            // Progress indicator
            if (entryCount % 50 == 0) {
                std::cout << ".";
                std::cout.flush();
                dotCount++;
                if (dotCount % 50 == 0) {
                    std::cout << " " << entryCount << "\n";
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
 * Insert devotional entry
 */
bool insertEntry(int dayNumber, const std::string& dateLabel, const std::string& title,
                 const std::string& content, int sortOrder) {
    const char* sql = R"SQL(
        INSERT INTO devotional_entry (
            day_number, sort_order, date_label, title, content
        ) VALUES (?, ?, ?, ?, ?)
    )SQL";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        return false;
    }

    sqlite3_bind_int(stmt, 1, dayNumber);
    sqlite3_bind_int(stmt, 2, sortOrder);
    sqlite3_bind_text(stmt, 3, dateLabel.c_str(), -1, SQLITE_TRANSIENT);
    bindTextOrNull(stmt, 4, title);
    sqlite3_bind_text(stmt, 5, content.c_str(), -1, SQLITE_TRANSIENT);

    bool success = (sqlite3_step(stmt) == SQLITE_DONE);
    sqlite3_finalize(stmt);

    return success;
}

/**
 * Print usage information
 */
void printUsage(const char* programName) {
    std::cout << "SWORD Devotional to SQLite Converter\n\n";
    std::cout << "Usage:\n";
    std::cout << "  " << programName << " --input <module.zip> --output <devotional_xxx.db>\n\n";
    std::cout << "Options:\n";
    std::cout << "  --input, -i   Input SWORD module ZIP file\n";
    std::cout << "  --output, -o  Output SQLite database file\n";
    std::cout << "  --help, -h    Show this help message\n\n";
    std::cout << "Example:\n";
    std::cout << "  " << programName << " --input SME.zip --output devotional_spurgeon_morning.db\n";
}
