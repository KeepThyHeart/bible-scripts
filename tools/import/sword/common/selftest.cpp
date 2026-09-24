/**
 * selftest.cpp — unit tests for the module format v0.2 additions to
 * sword_common (compression.{h,cpp}, content_digest.{h,cpp},
 * schema_bridge.{h,cpp}), plus parseOsisVerse/buildFormattingJson regression
 * coverage for bugs the task 0035 reconversion round found in real modules
 * (poetry lines, and the LEB/ABP text-hygiene fixes). Task 0035 / design
 * §2.3 (block.lines), §2.7, §3.
 *
 * Deliberately does NOT need libsword, a SWORD module, or a real Bible repo
 * checkout: compression and the digest are pure functions over sqlite3, the
 * schema bridge is tested against a tiny fixture schema tree built here
 * (this repo has no access to the real one — see schema_bridge.h's doc
 * comment), and parseOsisVerse is a pure string -> struct parser with no
 * SWMgr/SWModule dependency. Full end-to-end conversion is exercised by
 * tools/import/sword/verify/ against a real SWORD module and a real Bible
 * repo checkout, neither of which this binary requires.
 *
 * Run: make -C tools/import/sword/common test
 */

#include "compression.h"
#include "content_digest.h"
#include "schema_bridge.h"
#include "sword_common.h"

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
// sword_common.{h,cpp} — parseOsisVerse / buildFormattingJson (task 0035,
// poetry lines + real-module bugs the reconversion round surfaced).
//
// Pure string -> struct parsing: no libsword module or SWMgr needed.
// ============================================================================

void test_parseOsisVerse_poetry_lines_milestone() {
    // ASV/BSB/Darby/AKJV/... style: <l sID=".."/>text<l eID=".."/> pairs.
    const std::string osis =
        "<l level=\"1\" sID=\"L1\"/>Jehovah is my shepherd<l eID=\"L1\" level=\"1\"/> "
        "<l level=\"1\" sID=\"L2\"/>I shall not want<l eID=\"L2\" level=\"1\"/>";
    const OsisVerseResult r = parseOsisVerse(osis);
    CHECK(r.text == "Jehovah is my shepherd I shall not want");
    CHECK(r.block.lines.size() == 2);
    if (r.block.lines.size() == 2) {
        CHECK(r.block.lines[0].level == 1 && r.block.lines[0].start == 0 && r.block.lines[0].end == 3);
        CHECK(r.block.lines[1].level == 1 && r.block.lines[1].start == 4 && r.block.lines[1].end == 7);
    }
    const std::string json = buildFormattingJson(r);
    CHECK(json.find("\"lines\":[{\"level\":1,\"start\":0,\"end\":3},{\"level\":1,\"start\":4,\"end\":7}]")
          != std::string::npos);
    CHECK(json.find("poetry_level") == std::string::npos);
}

void test_parseOsisVerse_poetry_lines_bare_markers() {
    // NETfree/NETtext style: bare, id-less <l/> line-break markers.
    const std::string osis = "The LORD is my shepherd, <l />I lack nothing. <l />";
    const OsisVerseResult r = parseOsisVerse(osis);
    CHECK(r.block.lines.size() == 2);
    if (r.block.lines.size() == 2) {
        CHECK(r.block.lines[0].start == 0 && r.block.lines[0].end == 4);   // "The LORD is my shepherd,"
        CHECK(r.block.lines[1].start == 5 && r.block.lines[1].end == 7);   // "I lack nothing."
    }
}

void test_parseOsisVerse_no_lines_when_source_has_none() {
    // Prose with no <l> markup at all (e.g. KJV): no line data to report —
    // not a level-0 default, no "lines" key at all.
    const OsisVerseResult r = parseOsisVerse("In the beginning God created the heavens and the earth.");
    CHECK(r.block.lines.empty());
    CHECK(buildFormattingJson(r).empty());
}

void test_parseOsisVerse_escaped_angle_bracket_dropped() {
    // LEB ships stray markup double-escaped ("&lt;block&gt;"); libsword's own
    // plain-text fallback strips just the delimiters and keeps "block" as
    // plain text, and bible_verse.text MUST NOT contain '<' or '>'.
    const std::string osis =
        "To the brothers in Antioch and Syria and Cilicia. &lt;block&gt; Greetings!";
    const OsisVerseResult r = parseOsisVerse(osis);
    CHECK(r.text.find('<') == std::string::npos);
    CHECK(r.text.find('>') == std::string::npos);
    CHECK(r.text.find("block") != std::string::npos);
}

void test_parseOsisVerse_backslash_marker_any_case_stripped() {
    // ABP has a literal "\Eit" (an escape-style marker with an UPPERCASE
    // first letter) left in the source; bible_verse.text MUST NOT contain a
    // backslash, whatever case the marker name is.
    const std::string osis = "for loved you \\Eit the your God.";
    const OsisVerseResult r = parseOsisVerse(osis);
    CHECK(r.text.find('\\') == std::string::npos);
    CHECK(r.text.find("the your God") != std::string::npos);
}

void test_parseOsisVerse_abp_word_order_numeral_dropped() {
    // ABP's word-order numeral (<hi type="super"> in raw OSIS, <sup> in
    // SWORD's HTML rendering) is not verse text: left in, it fuses onto the
    // adjacent word with no space ("2way"), corrupting real words.
    const std::string osisRaw =
        "<w lemma=\"strong:G03598\" src=\"15\">[<seg><hi type=\"super\">2</hi></seg>way</w> "
        "<w lemma=\"strong:G02556\" src=\"16\"><seg><hi type=\"super\">1</hi></seg>an evil]</w>";
    const OsisVerseResult rRaw = parseOsisVerse(osisRaw);
    CHECK(rRaw.text == "[way an evil]");

    const std::string osisHtml = "[<sup>2</sup>way <sup>1</sup>an evil]";
    const OsisVerseResult rHtml = parseOsisVerse(osisHtml);
    CHECK(rHtml.text == "[way an evil]");
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
    RUN(test_parseOsisVerse_poetry_lines_milestone);
    RUN(test_parseOsisVerse_poetry_lines_bare_markers);
    RUN(test_parseOsisVerse_no_lines_when_source_has_none);
    RUN(test_parseOsisVerse_escaped_angle_bracket_dropped);
    RUN(test_parseOsisVerse_backslash_marker_any_case_stripped);
    RUN(test_parseOsisVerse_abp_word_order_numeral_dropped);

    if (g_failures == 0) {
        std::cout << "\nALL SELFTESTS PASSED\n";
        return 0;
    }
    std::cerr << "\n" << g_failures << " CHECK(S) FAILED\n";
    return 1;
}
