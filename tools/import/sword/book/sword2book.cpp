/**
 * sword2book.cpp
 *
 * Converts SWORD book module ZIP files into SQLite database format
 * (see packages/core/docs/features/module-format.md and
 * packages/core/sql/schemas/initial/Book.sql in the Bible repo).
 *
 * Notes:
 *   - Section->verse links live in the shared `verse_link` table.
 *   - module_info carries the full identity/provenance block.
 *   - FTS5 triggers use the external-content 'delete' command form.
 *   - book_section has `sort_order`, because `section_number` is TEXT and
 *     sorts "1.10" before "1.2". Traversal order is the true order, so it is
 *     recorded rather than inferred.
 *
 * Usage:
 *   ./sword2book --input <module.zip> --output <book_xxx.db>
 *
 * Example:
 *   ./sword2book --input Finney.zip --output book_finney.db
 */

#include <iostream>
#include <sstream>
#include <string>
#include <filesystem>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <map>
#include <regex>

// SWORD library
#include <swmgr.h>
#include <swmodule.h>
#include <treekey.h>
#include <treekeyidx.h>

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
bool parseModuleConfig(const fs::path& tempDir, const std::string& moduleName);
bool insertModuleInfo(const SwordCommon::ModuleIdentity& identity);
bool convertSections(SWModule* module);
bool insertSection(int parentSectionId, const std::string& sectionNumber,
                   const std::string& title, const std::string& content,
                   int wordCount, int sortOrder);
bool finalizeModuleInfo();
void traverseBook(SWModule* module, TreeKey* tk, int parentSectionId, const std::string& prefix,
                  int depth = 0);
std::string cleanSwordHtml(const std::string& html);
void printUsage(const char* programName);

/**
 * Module filter for book modules
 */
bool isBookModule(sword::SWModule* mod, const fs::path& modulePath) {
    std::string moduleType = mod->getType();
    std::string dataPath = mod->getConfigEntry("DataPath") ? mod->getConfigEntry("DataPath") : "";

    // Check if module data actually exists in our temp directory
    bool dataExists = SwordCommon::moduleDataExists(modulePath, dataPath);

    // Accept Generic Book modules
    return dataExists && moduleType == "Generic Books";
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

    std::cout << "SWORD Book to SQLite Converter\n";
    std::cout << "==============================\n\n";
    std::cout << "Input:  " << inputZip << "\n";
    std::cout << "Output: " << outputDb << "\n\n";

    // Create temporary directory for extraction
    fs::path tempDir = SwordCommon::createTempDirectory("sword_book_");

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
        if (!SwordCommon::initializeSWORD(tempDir, &mgr, &module, isBookModule)) {
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

        // Step 5: Convert sections
        std::cout << "[5/5] Converting book sections...\n";
        if (!convertSections(module)) {
            std::cerr << "Failed to convert sections\n";
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
    if (!SwordCommon::setSQLitePragmas(db, 32)) { // 32MB cache
        return false;
    }

    // Create schema
    const char* schema = R"SQL(
        -- Book sections (hierarchical).
        -- sort_order is explicit: section_number is TEXT, so ordering by
        -- it puts "1.10" before "1.2". Tree traversal order is the true order and
        -- cannot be recovered later, so it is recorded here.
        CREATE TABLE book_section (
            section_id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_section_id INTEGER,
            section_number TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            content TEXT,
            content_file TEXT,
            word_count INTEGER,
            metadata TEXT,
            FOREIGN KEY (parent_section_id) REFERENCES book_section(section_id) ON DELETE CASCADE
        );

        CREATE INDEX idx_section_parent ON book_section(parent_section_id, sort_order);
        CREATE INDEX idx_section_number ON book_section(section_number);

        -- NOTE: there is no bespoke scripture-reference table.
        -- Section -> verse links are rows in `verse_link` with
        -- source_type = 'book_section', exactly like every other module type's.

        -- Full-text search
        -- Column 0 is the key (UNINDEXED); searchable columns follow. That
        -- ordering is load-bearing for positional highlight()/snippet() calls.
        CREATE VIRTUAL TABLE book_section_fts USING fts5(
            section_id UNINDEXED,
            title,
            content,
            content='book_section',
            content_rowid='section_id',
            tokenize='porter unicode61'
        );

        -- FTS triggers (an external-content table needs the 'delete'
        -- command form; a plain UPDATE/DELETE leaves stale terms in the index)
        CREATE TRIGGER book_section_fts_insert AFTER INSERT ON book_section BEGIN
            INSERT INTO book_section_fts(rowid, section_id, title, content)
            VALUES (new.section_id, new.section_id, new.title, new.content);
        END;

        CREATE TRIGGER book_section_fts_delete AFTER DELETE ON book_section BEGIN
            INSERT INTO book_section_fts(book_section_fts, rowid, section_id, title, content)
            VALUES('delete', old.section_id, old.section_id, old.title, old.content);
        END;

        CREATE TRIGGER book_section_fts_update AFTER UPDATE ON book_section BEGIN
            INSERT INTO book_section_fts(book_section_fts, rowid, section_id, title, content)
            VALUES('delete', old.section_id, old.section_id, old.title, old.content);
            INSERT INTO book_section_fts(rowid, section_id, title, content)
            VALUES (new.section_id, new.section_id, new.title, new.content);
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
        VALUES ('0.1.0', 'Book module schema');
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
 * Parse module configuration file
 */
bool parseModuleConfig(const fs::path& tempDir, const std::string& moduleName) {
    auto config = SwordCommon::parseModuleConfig(tempDir, moduleName);
    g_identity = SwordCommon::deriveModuleIdentity(config, "book", moduleName);

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
 * Insert module metadata (identity + provenance)
 */
bool insertModuleInfo(const SwordCommon::ModuleIdentity& identity) {
    const char* sql = R"SQL(
        INSERT INTO module_info (
            info_id, module_uuid, module_type, format, format_version,
            abbreviation, full_name, author, publisher, year_published,
            copyright, license_spdx, license_url, source_url, description,
            language_code, versification, content_version, metadata
        ) VALUES (1, ?, 'book', 'book-module', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
 * Record the SHA-256 of the content we stored.
 */
bool finalizeModuleInfo() {
    std::string content;
    content.reserve(4u * 1024u * 1024u);

    sqlite3_stmt* select = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT section_id, title, content FROM book_section ORDER BY section_id",
            -1, &select, nullptr) != SQLITE_OK) {
        return false;
    }
    while (sqlite3_step(select) == SQLITE_ROW) {
        content += std::to_string(sqlite3_column_int64(select, 0));
        content += '\t';
        const unsigned char* title = sqlite3_column_text(select, 1);
        if (title) content += reinterpret_cast<const char*>(title);
        content += '\t';
        const unsigned char* body = sqlite3_column_text(select, 2);
        if (body) content += reinterpret_cast<const char*>(body);
        content += '\n';
    }
    sqlite3_finalize(select);

    const std::string hash = SwordCommon::sha256Hex(content);

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, "UPDATE module_info SET content_sha256 = ? WHERE info_id = 1",
                           -1, &stmt, nullptr) != SQLITE_OK) {
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
 * Convert all book sections
 */
bool convertSections(SWModule* module) {
    // Get TreeKey for navigation
    TreeKey* tk = dynamic_cast<TreeKey*>(module->getKey());
    if (!tk) {
        std::cerr << "Module does not use TreeKey\n";
        return false;
    }

    // Begin transaction for performance
    sqlite3_exec(db, "BEGIN TRANSACTION", nullptr, nullptr, nullptr);

    // Start traversal from root
    tk->root();
    traverseBook(module, tk, 0, "");

    sqlite3_exec(db, "COMMIT", nullptr, nullptr, nullptr);

    return true;
}

/**
 * Recursively traverse book structure
 */
void traverseBook(SWModule* module, TreeKey* tk, int parentSectionId, const std::string& prefix,
                  int depth) {
    static int entryCount = 0;
    static int dotCount = 0;

    // A malformed or cyclic tree would otherwise recurse until the stack dies.
    // JEAffections, JOChrist and JOCommGod all segfaulted here; a bound turns an
    // unexplained crash into a reported, skippable module.
    static const int MAX_DEPTH = 64;
    if (depth > MAX_DEPTH) {
        std::cerr << "\n  WARNING: section tree deeper than " << MAX_DEPTH
                  << " levels at '" << prefix << "'; not descending further"
                  << std::endl;
        return;
    }

    // Check if we have children
    if (tk->hasChildren()) {
        tk->firstChild();

        // Position among siblings. Traversal order is the book's real
        // order and is not recoverable from section_number (TEXT), so record it.
        int siblingOrder = 0;

        do {
            // getLocalName()/getText() return const char* and CAN be null.
            // Constructing a std::string from nullptr is undefined behaviour and
            // is what crashed the three books above.
            const char* localNameRaw = tk->getLocalName();
            const char* fullPathRaw = tk->getText();
            std::string keyName = localNameRaw ? std::string(localNameRaw) : std::string();
            std::string fullPath = fullPathRaw ? std::string(fullPathRaw) : std::string();

            // Get section content as HTML (SWORD renders OSIS → HTML via FMT_HTMLHREF)
            // then clean up SWORD-specific artifacts into standard HTML
            // renderText() returns SWBuf BY VALUE. Binding it to a `const char*`
            // destroys the temporary at the end of that statement and leaves the
            // pointer dangling — a use-after-free that usually goes unnoticed
            // because the bytes survive, but crashed outright on JEAffections,
            // JOChrist and JOCommGod. Hold the SWBuf for as long as the text is
            // needed.
            const sword::SWBuf renderedBuf = module->renderText();
            std::string rawHtml = renderedBuf.length() ? std::string(renderedBuf.c_str()) : std::string();
            std::string content = cleanSwordHtml(rawHtml);

            // Count words from stripped text (for metadata), but store the HTML
            std::string plainText = SwordCommon::stripMarkupClean(content);
            int wordCount = SwordCommon::countWords(plainText);

            siblingOrder++;

            // Generate section number (could be enhanced to track chapter/section numbering)
            std::string sectionNumber = prefix.empty() ? std::to_string(siblingOrder) :
                                        prefix + "." + std::to_string(siblingOrder);

            // Insert section
            int sectionId = 0;
            if (insertSection(parentSectionId, sectionNumber, keyName, content, wordCount, siblingOrder)) {
                // Get the last inserted row ID
                sectionId = sqlite3_last_insert_rowid(db);

                entryCount++;

                // Progress indicator
                if (entryCount % 10 == 0) {
                    std::cout << ".";
                    std::cout.flush();
                    dotCount++;
                    if (dotCount % 50 == 0) {
                        std::cout << " " << entryCount << "\n";
                    }
                }
            }

            // Recursively process children
            if (tk->hasChildren() && sectionId > 0) {
                traverseBook(module, tk, sectionId, sectionNumber, depth + 1);
            }

        } while (tk->nextSibling());

        tk->parent();
    }

    // Print final count at the end
    if (parentSectionId == 0) {
        std::cout << "\n  Total sections: " << entryCount << "\n";
    }
}

/**
 * Insert book section
 */
bool insertSection(int parentSectionId, const std::string& sectionNumber,
                   const std::string& title, const std::string& content,
                   int wordCount, int sortOrder) {
    const char* sql = R"SQL(
        INSERT INTO book_section (
            parent_section_id, section_number, sort_order, title, content, word_count
        ) VALUES (?, ?, ?, ?, ?, ?)
    )SQL";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        return false;
    }

    if (parentSectionId > 0) {
        sqlite3_bind_int(stmt, 1, parentSectionId);
    } else {
        sqlite3_bind_null(stmt, 1);
    }

    sqlite3_bind_text(stmt, 2, sectionNumber.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 3, sortOrder);
    sqlite3_bind_text(stmt, 4, title.c_str(), -1, SQLITE_TRANSIENT);
    bindTextOrNull(stmt, 5, content);
    sqlite3_bind_int(stmt, 6, wordCount);

    bool success = (sqlite3_step(stmt) == SQLITE_DONE);
    sqlite3_finalize(stmt);

    return success;
}

/**
 * Clean up SWORD's HTML output for storage.
 *
 * SWORD's FMT_HTMLHREF filter produces non-standard artifacts:
 *   - <!P><br /> and <!/P><br /> as paragraph markers
 *   - <a href="passagestudy.jsp?..."> for scripture references (non-functional outside SWORD)
 *
 * This function converts them to clean, standard HTML:
 *   - Paragraph markers → <p> tags
 *   - SWORD scripture links → plain text (the app's own link processor handles references)
 */
std::string cleanSwordHtml(const std::string& html) {
    std::string result = html;

    // Convert SWORD paragraph markers to <p> tags
    // Pattern: <!/P><br /><!P><br /> → </p><p>
    {
        std::regex parBreak(R"(<!/P>\s*<br\s*/?>\s*<!P>\s*<br\s*/?>)");
        result = std::regex_replace(result, parBreak, "</p>\n<p>");
    }

    // Remove any remaining standalone <!P> or <!/P> markers
    {
        std::regex pOpen(R"(<!P>\s*<br\s*/?>)");
        result = std::regex_replace(result, pOpen, "");
    }
    {
        std::regex pClose(R"(<!/P>\s*<br\s*/?>)");
        result = std::regex_replace(result, pClose, "");
    }
    {
        std::regex pMarker(R"(<!/?P>)");
        result = std::regex_replace(result, pMarker, "");
    }

    // Strip SWORD's passagestudy.jsp links — keep the link text
    // <a href="passagestudy.jsp?...">Luke 14:28</a> → Luke 14:28
    {
        std::regex swordLink(R"(<a\s+href="passagestudy\.jsp\?[^"]*">)");
        result = std::regex_replace(result, swordLink, "");
    }
    {
        // Remove the closing </a> tags that paired with SWORD links
        // (We can't selectively remove only SWORD </a> tags easily, but since
        // all <a> tags in SWORD GenBook output are passagestudy.jsp links,
        // removing all </a> is safe here)
        std::regex closeA(R"(</a>)");
        result = std::regex_replace(result, closeA, "");
    }

    // Wrap content in <p> if it doesn't already start with one
    // (the first paragraph won't have a leading <p> after the above transforms)
    {
        std::string trimmed = result;
        size_t start = trimmed.find_first_not_of(" \t\r\n");
        if (start != std::string::npos) {
            trimmed = trimmed.substr(start);
        }
        if (trimmed.substr(0, 2) != "<p" && trimmed.substr(0, 3) != "<h1" &&
            trimmed.substr(0, 3) != "<h2" && trimmed.substr(0, 3) != "<h3") {
            result = "<p>" + result;
        }
    }

    // Ensure content ends with </p>
    {
        std::string trimmed = result;
        size_t end = trimmed.find_last_not_of(" \t\r\n");
        if (end != std::string::npos) {
            trimmed = trimmed.substr(0, end + 1);
        }
        if (trimmed.size() >= 4 && trimmed.substr(trimmed.size() - 4) != "</p>") {
            result += "</p>";
        }
    }

    // Clean up tab runs (SWORD's internal paragraph markers, now inside <p> tags)
    {
        std::regex tabRuns(R"(\t{2,})");
        result = std::regex_replace(result, tabRuns, "");
    }

    // Collapse runs of spaces (from SWORD's line-wrapping) into single spaces
    {
        std::regex multiSpace(R"( {2,})");
        result = std::regex_replace(result, multiSpace, " ");
    }

    // Clean up excessive whitespace/newlines
    {
        std::regex multiNewline(R"(\n{3,})");
        result = std::regex_replace(result, multiNewline, "\n\n");
    }

    return result;
}

/**
 * Print usage information
 */
void printUsage(const char* programName) {
    std::cout << "SWORD Book to SQLite Converter\n\n";
    std::cout << "Usage:\n";
    std::cout << "  " << programName << " --input <module.zip> --output <book_xxx.db>\n\n";
    std::cout << "Options:\n";
    std::cout << "  --input, -i   Input SWORD module ZIP file\n";
    std::cout << "  --output, -o  Output SQLite database file\n";
    std::cout << "  --help, -h    Show this help message\n\n";
    std::cout << "Example:\n";
    std::cout << "  " << programName << " --input Finney.zip --output book_finney.db\n";
}
