/**
 * selftest.cpp — unit tests for the module format v0.2 additions to
 * sword_common (compression.{h,cpp}, content_digest.{h,cpp},
 * schema_bridge.{h,cpp}). Task 0035 / design §2.7, §3.
 *
 * Deliberately does NOT need libsword, a SWORD module, or a real Bible repo
 * checkout: compression and the digest are pure functions over sqlite3, and
 * the schema bridge is tested against a tiny fixture schema tree built here
 * (this repo has no access to the real one — see schema_bridge.h's doc
 * comment). Full end-to-end conversion is exercised by
 * tools/import/sword/verify/ against a real SWORD module and a real Bible
 * repo checkout, neither of which this binary requires.
 *
 * Run: make -C tools/import/sword/common test
 */

#include "compression.h"
#include "content_digest.h"
#include "schema_bridge.h"

#include <sqlite3.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <unistd.h>

namespace fs = std::filesystem;
using namespace SwordCommon;

static int g_failures = 0;

#define CHECK(cond) \
    do { \
        if (!(cond)) { \
            std::cerr << "  FAIL " << __func__ << ": " << #cond << " (line " << __LINE__ << ")\n"; \
            g_failures++; \
        } \
    } while (0)

#define RUN(fn) \
    do { \
        std::cout << "  running " << #fn << "...\n"; \
        fn(); \
    } while (0)

// ============================================================================
// compression.{h,cpp}
// ============================================================================

void test_deflate_roundtrip_no_dict() {
    std::string text = "For God so loved the world, that he gave his only begotten Son.";
    auto frame = deflateEncode(text, {});
    std::string back = deflateDecode(frame.data(), frame.size(), {});
    CHECK(back == text);
}

void test_deflate_roundtrip_with_dict() {
    std::vector<std::string> samples;
    for (int i = 0; i < 200; i++) {
        samples.push_back("The quick brown fox jumps over the lazy dog, entry number " + std::to_string(i) +
                           ". In the beginning God created the heaven and the earth.");
    }
    auto dict = trainDictionary(samples, DEFLATE_DICT_SIZE);
    CHECK(!dict.empty());
    std::string text = "In the beginning God created the heaven and the earth, entry number 42.";
    auto frame = deflateEncode(text, dict);
    std::string back = deflateDecode(frame.data(), frame.size(), dict);
    CHECK(back == text);
}

void test_zstd_roundtrip_no_dict() {
    std::string text = "For God so loved the world, that he gave his only begotten Son.";
    auto frame = zstdEncode(text, {});
    std::string back = zstdDecode(frame.data(), frame.size(), {});
    CHECK(back == text);
}

void test_zstd_roundtrip_with_dict() {
    std::vector<std::string> samples;
    for (int i = 0; i < 200; i++) {
        samples.push_back("The quick brown fox jumps over the lazy dog, entry number " + std::to_string(i) +
                           ". In the beginning God created the heaven and the earth.");
    }
    auto dict = trainDictionary(samples, ZSTD_DICT_SIZE);
    CHECK(!dict.empty());
    std::string text = "In the beginning God created the heaven and the earth, entry number 42.";
    auto frame = zstdEncode(text, dict);
    std::string back = zstdDecode(frame.data(), frame.size(), dict);
    CHECK(back == text);
    CHECK(zstdDictId(dict) != 0);
}

void test_adler32_known_value() {
    // adler32("Wikipedia") = 0x11E60398 (RFC 1950 worked example).
    std::vector<uint8_t> data = {'W', 'i', 'k', 'i', 'p', 'e', 'd', 'i', 'a'};
    CHECK(adler32Of(data) == 0x11E60398u);
}

void test_encodeCell_keep_only_if_smaller() {
    // Too short to shrink meaningfully -> stays TEXT.
    auto shortCell = encodeCell(CODEC_DEFLATE, "hi", {});
    CHECK(shortCell.stored == false);

    // Long, repetitive text compresses well past the 16-byte margin.
    std::string longText;
    for (int i = 0; i < 50; i++) longText += "the quick brown fox jumps over the lazy dog. ";
    auto longCell = encodeCell(CODEC_DEFLATE, longText, {});
    CHECK(longCell.stored == true);
    CHECK(longCell.frame.size() + 16 < longText.size());
}

void test_applyCompression_and_digest_invariance() {
    sqlite3* db = nullptr;
    sqlite3_open(":memory:", &db);
    sqlite3_exec(db,
        "CREATE TABLE commentary_entry (entry_id INTEGER PRIMARY KEY, content TEXT);"
        "CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, compression TEXT NOT NULL DEFAULT 'none');"
        "CREATE TABLE compression_dictionary (codec TEXT PRIMARY KEY, dict_id INTEGER, dict_blob BLOB);"
        "INSERT INTO module_info (info_id) VALUES (1);",
        nullptr, nullptr, nullptr);

    // Insert >8MB of prose so the module is eligible for auto-compression (§3.4).
    sqlite3_stmt* ins = nullptr;
    sqlite3_prepare_v2(db, "INSERT INTO commentary_entry (content) VALUES (?)", -1, &ins, nullptr);
    size_t total = 0;
    while (total < 9u * 1024 * 1024) {
        std::ostringstream s;
        s << "In the beginning God created the heaven and the earth, entry " << total
          << ". And the earth was without form, and void; and darkness was upon the face of the deep.";
        std::string text = s.str();
        sqlite3_bind_text(ins, 1, text.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(ins);
        sqlite3_reset(ins);
        total += text.size();
    }
    sqlite3_finalize(ins);

    std::string digestBefore = computeContentSha256(db, "commentary_entry", "entry_id", {"content"}, {"content"}, "none", {});

    CompressionOutcome outcome = applyCompression(db, "commentary_entry", {"content"});
    CHECK(outcome.codec == std::string(CODEC_DEFLATE));
    CHECK(outcome.rowsCompressed > 0);
    CHECK(!outcome.dictionary.empty());

    std::string digestAfter = computeContentSha256(
        db, "commentary_entry", "entry_id", {"content"}, {"content"}, outcome.codec, outcome.dictionary);
    CHECK(digestBefore == digestAfter); // the acceptance criterion: digest identical across codecs

    sqlite3_stmt* q = nullptr;
    sqlite3_prepare_v2(db, "SELECT COUNT(*) FROM commentary_entry WHERE typeof(content)='blob'", -1, &q, nullptr);
    sqlite3_step(q);
    CHECK(sqlite3_column_int64(q, 0) > 0);
    sqlite3_finalize(q);

    sqlite3_prepare_v2(db, "SELECT compression FROM module_info WHERE info_id=1", -1, &q, nullptr);
    sqlite3_step(q);
    std::string comp = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
    CHECK(comp == "deflate");
    sqlite3_finalize(q);

    // §2.8: dictionary row present iff compression != 'none'.
    sqlite3_prepare_v2(db, "SELECT COUNT(*) FROM compression_dictionary WHERE codec='deflate'", -1, &q, nullptr);
    sqlite3_step(q);
    CHECK(sqlite3_column_int64(q, 0) == 1);
    sqlite3_finalize(q);

    sqlite3_close(db);
}

void test_applyCompression_below_threshold_stays_none() {
    sqlite3* db = nullptr;
    sqlite3_open(":memory:", &db);
    sqlite3_exec(db,
        "CREATE TABLE commentary_entry (entry_id INTEGER PRIMARY KEY, content TEXT);"
        "CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, compression TEXT NOT NULL DEFAULT 'none');"
        "CREATE TABLE compression_dictionary (codec TEXT PRIMARY KEY, dict_id INTEGER, dict_blob BLOB);"
        "INSERT INTO module_info (info_id) VALUES (1);"
        "INSERT INTO commentary_entry (content) VALUES ('a small commentary entry, well under 8MB');",
        nullptr, nullptr, nullptr);

    CompressionOutcome outcome = applyCompression(db, "commentary_entry", {"content"});
    CHECK(outcome.codec == std::string(CODEC_NONE));
    CHECK(outcome.dictionary.empty());

    sqlite3_stmt* q = nullptr;
    sqlite3_prepare_v2(db, "SELECT typeof(content) FROM commentary_entry", -1, &q, nullptr);
    sqlite3_step(q);
    std::string t = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
    CHECK(t == "text"); // never became a BLOB
    sqlite3_finalize(q);

    sqlite3_close(db);
}

void test_applyCompression_forced_codec_overrides_threshold() {
    sqlite3* db = nullptr;
    sqlite3_open(":memory:", &db);
    sqlite3_exec(db,
        "CREATE TABLE commentary_entry (entry_id INTEGER PRIMARY KEY, content TEXT);"
        "CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, compression TEXT NOT NULL DEFAULT 'none');"
        "CREATE TABLE compression_dictionary (codec TEXT PRIMARY KEY, dict_id INTEGER, dict_blob BLOB);"
        "INSERT INTO module_info (info_id) VALUES (1);",
        nullptr, nullptr, nullptr);
    // Enough repetitive rows for a dictionary to train, but nowhere near 8MB.
    sqlite3_stmt* ins = nullptr;
    sqlite3_prepare_v2(db, "INSERT INTO commentary_entry (content) VALUES (?)", -1, &ins, nullptr);
    for (int i = 0; i < 300; i++) {
        std::string text = "a small entry number " + std::to_string(i) + " repeated for dictionary training purposes";
        sqlite3_bind_text(ins, 1, text.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(ins);
        sqlite3_reset(ins);
    }
    sqlite3_finalize(ins);

    CompressionOutcome outcome = applyCompression(db, "commentary_entry", {"content"}, CODEC_DEFLATE);
    CHECK(outcome.codec == std::string(CODEC_DEFLATE)); // --codec=deflate forces it despite being under threshold
}

// ============================================================================
// schema_bridge.{h,cpp} — against a fixture schema tree (no real Bible repo
// checkout is available to this task; see schema_bridge.h's doc comment).
// ============================================================================

void test_loadRepoSchema_against_fixture() {
    fs::path tmp = fs::temp_directory_path() / ("sword-common-selftest-" + std::to_string(getpid()));
    fs::path schemas = tmp / "packages" / "core" / "sql" / "schemas";
    fs::create_directories(schemas / "initial");
    fs::create_directories(schemas / "shared");

    {
        std::ofstream f(schemas / "shared" / "module_info.sql");
        f << "PRAGMA foreign_keys = ON;\n"
          << "CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, format_version TEXT NOT NULL DEFAULT '0.2');\n";
    }
    {
        std::ofstream f(schemas / "shared" / "verse_link.sql");
        f << "CREATE TABLE verse_link (link_id INTEGER PRIMARY KEY AUTOINCREMENT);\n";
    }
    {
        std::ofstream f(schemas / "initial" / "Fixture.sql");
        f << "-- @include ../shared/module_info.sql\n"
          << "-- @include ../shared/verse_link.sql\n"
          << "CREATE TABLE fixture_content (id INTEGER PRIMARY KEY, text TEXT NOT NULL);\n";
    }

    setenv("BIBLE_REPO", tmp.c_str(), 1);
#ifndef SELFTEST_SCHEMA_JS
#error "SELFTEST_SCHEMA_JS must be defined by the Makefile (absolute path to scripts/lib/schema.js)"
#endif
    std::string ddl = loadRepoSchema("Fixture.sql", SELFTEST_SCHEMA_JS);
    CHECK(ddl.find("CREATE TABLE module_info") != std::string::npos);
    CHECK(ddl.find("CREATE TABLE verse_link") != std::string::npos);
    CHECK(ddl.find("CREATE TABLE fixture_content") != std::string::npos);
    CHECK(ddl.find("PRAGMA") == std::string::npos);

    // The schema is directly executable.
    sqlite3* db = nullptr;
    sqlite3_open(":memory:", &db);
    char* errMsg = nullptr;
    int rc = sqlite3_exec(db, ddl.c_str(), nullptr, nullptr, &errMsg);
    CHECK(rc == SQLITE_OK);
    if (rc != SQLITE_OK) std::cerr << "    sqlite3_exec: " << (errMsg ? errMsg : "?") << "\n";
    sqlite3_free(errMsg);
    sqlite3_close(db);

    // Missing schema file -> a clear error, not a silent empty string.
    bool threw = false;
    try {
        loadRepoSchema("NoSuchType.sql", SELFTEST_SCHEMA_JS);
    } catch (const std::exception& e) {
        threw = true;
        CHECK(std::string(e.what()).find("Schema file not found") != std::string::npos);
    }
    CHECK(threw);

    unsetenv("BIBLE_REPO");
    fs::remove_all(tmp);
}

// ============================================================================

int main() {
    RUN(test_deflate_roundtrip_no_dict);
    RUN(test_deflate_roundtrip_with_dict);
    RUN(test_zstd_roundtrip_no_dict);
    RUN(test_zstd_roundtrip_with_dict);
    RUN(test_adler32_known_value);
    RUN(test_encodeCell_keep_only_if_smaller);
    RUN(test_applyCompression_and_digest_invariance);
    RUN(test_applyCompression_below_threshold_stays_none);
    RUN(test_applyCompression_forced_codec_overrides_threshold);
    RUN(test_loadRepoSchema_against_fixture);

    if (g_failures == 0) {
        std::cout << "\nALL SELFTESTS PASSED\n";
        return 0;
    }
    std::cerr << "\n" << g_failures << " CHECK(S) FAILED\n";
    return 1;
}
