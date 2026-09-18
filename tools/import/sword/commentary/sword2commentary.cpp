/**
 * sword2commentary.cpp
 *
 * Converts SWORD commentary module ZIP files into SQLite database format
 * (see packages/core/docs/features/module-format.md and
 * packages/core/sql/schemas/initial/Commentary.sql in the Bible repo).
 *
 * Notes:
 *   - Book numbers come from each entry's OSIS reference, so a commentary
 *     covering deuterocanonical books drops them instead of shifting every
 *     later book by one.
 *   - verse_link is emitted alongside commentary_entry, so every module type
 *     exposes content->verse linking through one table.
 *   - module_info carries the full identity/provenance block.
 *   - FTS5 triggers use the external-content 'delete' command form.
 *   - entry_level is derived and left unconstrained rather than pinned by a
 *     CHECK enum.
 *
 * Usage:
 *   ./sword2commentary --input <module.zip> --output <commentary_xxx.db>
 *
 * Example:
 *   ./sword2commentary --input Wesley.zip --output commentary_wesley.db
 */

#include <iostream>
#include <sstream>
#include <string>
#include <map>
#include <filesystem>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <set>

// SWORD library
#include <swmgr.h>
#include <swmodule.h>
#include <versekey.h>

// SQLite
#include <sqlite3.h>

// Common library
#include "sword_common.h"

namespace fs = std::filesystem;
using namespace sword;

// Global database handle
static sqlite3* db = nullptr;

// Books rejected because they fall outside the 66-book Protestant canon.
static std::map<std::string, int> g_skippedBooks;
// Books actually imported, keyed by the OSIS name the SOURCE used. No later
// stage of the pipeline can see these names, so they are recorded here.
static std::map<std::string, int> g_importedBooks;

// The identity block, kept so the finalisation pass can rebuild the metadata
// object without needing SQLite's JSON1 extension to merge into it.
static SwordCommon::ModuleIdentity g_identity;

// Forward declarations
bool createDatabase(const std::string& dbPath);
bool parseModuleConfig(const fs::path& tempDir, const std::string& moduleName);
bool insertModuleInfo(const SwordCommon::ModuleIdentity& identity);
bool convertEntries(SWModule* module);
int64_t insertEntry(int64_t verseIdStart, int64_t verseIdEnd, const std::string& entryLevel,
                    const std::string& content, int wordCount);
bool finalizeModuleInfo();
void printUsage(const char* programName);

/**
 * Module filter for Commentary modules
 */
bool isCommentaryModule(sword::SWModule* mod, const fs::path& modulePath) {
    std::string moduleType = mod->getType();
    std::string dataPath = mod->getConfigEntry("DataPath") ? mod->getConfigEntry("DataPath") : "";

    // Check if module data actually exists in our temp directory
    bool dataExists = SwordCommon::moduleDataExists(modulePath, dataPath);

    // Accept Commentary modules
    return dataExists && moduleType == "Commentaries";
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

    std::cout << "SWORD Commentary to SQLite Converter (format version "
              << SwordCommon::FORMAT_VERSION << ")\n";
    std::cout << "====================================\n\n";
    std::cout << "Input:  " << inputZip << "\n";
    std::cout << "Output: " << outputDb << "\n\n";

    // Create temporary directory for extraction
    fs::path tempDir = SwordCommon::createTempDirectory("sword_commentary_");

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
        if (!SwordCommon::initializeSWORD(tempDir, &mgr, &module, isCommentaryModule)) {
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
        if (!parseModuleConfig(tempDir, moduleName)) {
            std::cerr << "Warning: Could not parse all module metadata\n";
        }

        // Step 5: Convert entries
        std::cout << "[5/5] Converting commentary entries...\n";
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
 * Create the SQLite database schema
 */
bool createDatabase(const std::string& dbPath) {
    if (sqlite3_open(dbPath.c_str(), &db) != SQLITE_OK) {
        std::cerr << "Cannot create database: " << sqlite3_errmsg(db) << "\n";
        return false;
    }

    // Enable pragmas using common library
    if (!SwordCommon::setSQLitePragmas(db, 32)) { // 32MB cache
        std::cerr << "Failed to set pragmas\n";
        return false;
    }

    // Create schema
    const char* schema = R"SQL(
        -- Commentary entries.
        --
        -- verse_id_start / verse_id_end stay on the row: they are the entry's
        -- own key and give it its ordering (there is no separate
        -- sort_order column). verse_link additionally carries the same anchor
        -- so that generic content->verse queries work identically across every
        -- module type.
        --
        -- entry_level has no CHECK: it is an open, extensible set
        -- validated at the repository boundary, not by the storage layer.
        CREATE TABLE commentary_entry (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            verse_id_start INTEGER,
            verse_id_end INTEGER,
            entry_level TEXT NOT NULL,
            content TEXT NOT NULL,
            content_file TEXT,
            word_count INTEGER,
            metadata TEXT
        );

        CREATE INDEX idx_entry_verse_start ON commentary_entry(verse_id_start);
        CREATE INDEX idx_entry_verse_end ON commentary_entry(verse_id_end);
        CREATE INDEX idx_entry_level ON commentary_entry(entry_level);

        -- Full-text search
        -- Column 0 is the key (UNINDEXED); searchable columns follow. That
        -- ordering is load-bearing for positional highlight()/snippet() calls.
        CREATE VIRTUAL TABLE commentary_entry_fts USING fts5(
            entry_id UNINDEXED,
            content,
            content='commentary_entry',
            content_rowid='entry_id',
            tokenize='porter unicode61'
        );

        -- FTS triggers (an external-content table needs the 'delete'
        -- command form; a plain UPDATE/DELETE leaves stale terms in the index)
        CREATE TRIGGER commentary_entry_fts_insert AFTER INSERT ON commentary_entry BEGIN
            INSERT INTO commentary_entry_fts(rowid, entry_id, content)
            VALUES (new.entry_id, new.entry_id, new.content);
        END;

        CREATE TRIGGER commentary_entry_fts_delete AFTER DELETE ON commentary_entry BEGIN
            INSERT INTO commentary_entry_fts(commentary_entry_fts, rowid, entry_id, content)
            VALUES('delete', old.entry_id, old.entry_id, old.content);
        END;

        CREATE TRIGGER commentary_entry_fts_update AFTER UPDATE ON commentary_entry BEGIN
            INSERT INTO commentary_entry_fts(commentary_entry_fts, rowid, entry_id, content)
            VALUES('delete', old.entry_id, old.entry_id, old.content);
            INSERT INTO commentary_entry_fts(rowid, entry_id, content)
            VALUES (new.entry_id, new.entry_id, new.content);
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
        VALUES ('0.1.0', 'Commentary module schema');
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
 * Parse module configuration file and insert the identity block
 */
bool parseModuleConfig(const fs::path& tempDir, const std::string& moduleName) {
    auto config = SwordCommon::parseModuleConfig(tempDir, moduleName);
    g_identity = SwordCommon::deriveModuleIdentity(config, "commentary", moduleName);

    std::cout << "  Module UUID: " << g_identity.uuid << "\n";
    std::cout << "  Licence: "
              << (g_identity.licenseSpdx.empty() ? "(unknown)" : g_identity.licenseSpdx) << "\n";

    return insertModuleInfo(g_identity);
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
bool insertModuleInfo(const SwordCommon::ModuleIdentity& identity) {
    const char* sql = R"SQL(
        INSERT INTO module_info (
            info_id, module_uuid, module_type, format, format_version,
            abbreviation, full_name, author, publisher, year_published,
            copyright, license_spdx, license_url, source_url, description,
            language_code, versification, content_version, metadata
        ) VALUES (1, ?, 'commentary', 'commentary-module', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
 * Convert all commentary entries.
 *
 * The book number comes from the entry's OSIS reference rather than from
 * SWORD's per-testament book index, so no offset heuristic is needed and
 * non-canonical books are skipped instead of displacing the books after them.
 */
bool convertEntries(SWModule* module) {
    module->setKey("Genesis 1:1");

    VerseKey* vk = dynamic_cast<VerseKey*>(module->getKey());
    if (!vk) {
        std::cerr << "Module does not use VerseKey\n";
        return false;
    }

    sqlite3_exec(db, "BEGIN TRANSACTION", nullptr, nullptr, nullptr);

    int entryCount = 0;
    int dotCount = 0;
    int iterations = 0;
    const int MAX_ITERATIONS = 200000;
    int duplicateCount = 0;
    std::set<int64_t> seenVerses;

    (*vk) = sword::TOP;
    vk->setIntros(false);
    vk->popError();

    do {
        iterations++;
        if (iterations > MAX_ITERATIONS) {
            std::cerr << "WARNING: Hit iteration limit, stopping to prevent infinite loop\n";
            break;
        }

        const char* osisRefRaw = vk->getOSISRef();
        const std::string osisRef = osisRefRaw ? osisRefRaw : "";
        const std::string osisBook = SwordCommon::osisBookOf(osisRef);
        const int bookNumber = SwordCommon::canonicalBookNumberFromOsis(osisBook);

        if (bookNumber == 0) {
            if (!osisBook.empty()) g_skippedBooks[osisBook]++;
            (*vk)++;
            continue;
        }

        // renderText() returns SWBuf BY VALUE; binding it to a `const char*`
        // leaves a dangling pointer once the temporary dies. Hold the buffer.
        const sword::SWBuf renderedBuf = module->renderText();
        const std::string text =
            renderedBuf.length() ? std::string(renderedBuf.c_str()) : std::string();

        // Skip empty entries
        if (!SwordCommon::trim(text).empty()) {
            const int chapter = vk->getChapter();
            const int verse = vk->getVerse();

            const int64_t verseIdStart = SwordCommon::calculateVerseId(bookNumber, chapter, verse);
            const int64_t verseIdEnd = verseIdStart;

            // Derive the level instead of hard-coding 'verse'.
            // SWORD uses verse 0 for a chapter introduction and chapter 0 for a
            // book introduction; anything else is a verse-level comment.
            std::string entryLevel = "verse";
            if (chapter == 0) entryLevel = "book";
            else if (verse == 0) entryLevel = "chapter";

            if (seenVerses.count(verseIdStart) > 0) {
                duplicateCount++;
                if (duplicateCount > 100) {
                    std::cout << "  Detected verse duplication (looping), stopping iteration\n";
                    break;
                }
                (*vk)++;
                continue;
            }
            seenVerses.insert(verseIdStart);
            g_importedBooks[osisBook]++;

            const std::string plainText = SwordCommon::stripMarkupClean(text);
            const int wordCount = SwordCommon::countWords(plainText);

            const int64_t entryId = insertEntry(verseIdStart, verseIdEnd, entryLevel, text, wordCount);
            if (entryId <= 0) {
                std::cerr << "Failed to insert entry: " << sqlite3_errmsg(db) << "\n";
                sqlite3_exec(db, "ROLLBACK", nullptr, nullptr, nullptr);
                return false;
            }

            // The same anchor, expressed through the universal table.
            SwordCommon::insertVerseLink(db, "commentary_entry", entryId,
                                         verseIdStart, verseIdEnd,
                                         "primary_passage", 0);

            entryCount++;

            // Progress indicator
            if (entryCount % 100 == 0) {
                std::cout << ".";
                std::cout.flush();
                dotCount++;
                if (dotCount % 50 == 0) {
                    std::cout << " " << entryCount << " (book " << bookNumber << ")\n";
                }
            }
        }

        (*vk)++;
    } while (!vk->popError());

    sqlite3_exec(db, "COMMIT", nullptr, nullptr, nullptr);

    std::cout << "\n  Total entries: " << entryCount << "\n";

    if (!g_skippedBooks.empty()) {
        std::cerr << "\nWARNING: " << g_skippedBooks.size() << " book(s) outside the "
                  << SwordCommon::CANON << " canon were skipped:\n";
        for (const auto& entry : g_skippedBooks) {
            std::cerr << "    " << entry.first << " (" << entry.second << " entries)\n";
        }
    }

    return true;
}

/**
 * Insert a commentary entry. Returns the new entry_id, or 0 on failure.
 */
int64_t insertEntry(int64_t verseIdStart, int64_t verseIdEnd, const std::string& entryLevel,
                    const std::string& content, int wordCount) {
    const char* sql = R"SQL(
        INSERT INTO commentary_entry (
            verse_id_start, verse_id_end, entry_level, content, word_count
        ) VALUES (?, ?, ?, ?, ?)
    )SQL";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        return 0;
    }

    sqlite3_bind_int64(stmt, 1, verseIdStart);
    sqlite3_bind_int64(stmt, 2, verseIdEnd);
    sqlite3_bind_text(stmt, 3, entryLevel.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, content.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 5, wordCount);

    const bool success = (sqlite3_step(stmt) == SQLITE_DONE);
    sqlite3_finalize(stmt);

    return success ? sqlite3_last_insert_rowid(db) : 0;
}

/**
 * Record the content hash and the source book names.
 *
 * The book lists matter because downstream validation can only detect
 * out-of-canon content by numbering overflow, and this converter is the only
 * stage that sees the source's real book names.
 */
bool finalizeModuleInfo() {
    std::string content;
    content.reserve(4u * 1024u * 1024u);

    sqlite3_stmt* select = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT entry_id, verse_id_start, content FROM commentary_entry ORDER BY entry_id",
            -1, &select, nullptr) != SQLITE_OK) {
        return false;
    }
    while (sqlite3_step(select) == SQLITE_ROW) {
        content += std::to_string(sqlite3_column_int64(select, 1));
        content += '\t';
        const unsigned char* text = sqlite3_column_text(select, 2);
        if (text) content += reinterpret_cast<const char*>(text);
        content += '\n';
    }
    sqlite3_finalize(select);

    const std::string hash = SwordCommon::sha256Hex(content);

    // Rebuild the whole metadata object in C++ rather than merging with
    // json_patch(): the JSON1 extension is not guaranteed to be present in the
    // libsqlite3 this tool links against.
    std::string metadata = g_identity.metadataJson;
    if (metadata.size() >= 2 && metadata[metadata.size() - 1] == '}') {
        metadata.erase(metadata.size() - 1);   // drop the closing brace
        metadata += ",";
    } else {
        metadata = "{";
    }

    std::ostringstream booksJson;
    booksJson << "\"source_books\":[";
    bool first = true;
    for (const auto& entry : g_importedBooks) {
        if (!first) booksJson << ",";
        booksJson << "{\"osis\":\"" << SwordCommon::jsonEscape(entry.first)
                  << "\",\"entries\":" << entry.second << "}";
        first = false;
    }
    booksJson << "],\"skipped_books\":[";
    first = true;
    for (const auto& entry : g_skippedBooks) {
        if (!first) booksJson << ",";
        booksJson << "{\"osis\":\"" << SwordCommon::jsonEscape(entry.first)
                  << "\",\"entries\":" << entry.second << "}";
        first = false;
    }
    booksJson << "]}";

    metadata += booksJson.str();

    const char* sql = "UPDATE module_info SET content_sha256 = ?, metadata = ? WHERE info_id = 1";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        std::cerr << "Failed to prepare module_info finalisation: " << sqlite3_errmsg(db) << "\n";
        return false;
    }

    sqlite3_bind_text(stmt, 1, hash.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, metadata.c_str(), -1, SQLITE_TRANSIENT);

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
    std::cout << "SWORD Commentary to SQLite Converter\n\n";
    std::cout << "Usage:\n";
    std::cout << "  " << programName << " --input <module.zip> --output <commentary_xxx.db>\n\n";
    std::cout << "Options:\n";
    std::cout << "  --input, -i   Input SWORD module ZIP file\n";
    std::cout << "  --output, -o  Output SQLite database file\n";
    std::cout << "  --help, -h    Show this help message\n\n";
    std::cout << "Books outside the 66-book Protestant canon are skipped with a warning.\n\n";
    std::cout << "Example:\n";
    std::cout << "  " << programName << " --input Wesley.zip --output commentary_wesley.db\n";
}
