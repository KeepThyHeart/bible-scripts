/**
 * sword_common.cpp
 *
 * Implementation of shared functionality for SWORD module converters.
 * See sword_common.h for the module format contract these helpers implement.
 */

#include "sword_common.h"

#include <iostream>
#include <fstream>
#include <sstream>
#include <iterator>
#include <algorithm>
#include <cctype>
#include <cstring>
#include <regex>
#include <unistd.h>

// SWORD library
#include <swmgr.h>
#include <swmodule.h>
#include <markupfiltmgr.h>

// SQLite
#include <sqlite3.h>

// libzip
#include <zip.h>

using namespace sword;

namespace SwordCommon {

    const char* const FORMAT_VERSION = "0.1";
    const char* const CANON          = "protestant-66";
    const char* const VERSIFICATION  = "kjv-english";

    /**
     * Extract ZIP file to directory
     */
    bool extractZipFile(const std::string& zipPath, const fs::path& outputDir) {
        int error;
        zip* archive = zip_open(zipPath.c_str(), ZIP_RDONLY, &error);

        if (!archive) {
            char errBuf[256];
            zip_error_to_str(errBuf, sizeof(errBuf), error, errno);
            std::cerr << "Failed to open ZIP: " << errBuf << "\n";
            return false;
        }

        fs::create_directories(outputDir);

        int64_t numEntries = zip_get_num_entries(archive, 0);
        for (int64_t i = 0; i < numEntries; i++) {
            const char* name = zip_get_name(archive, i, 0);
            if (!name) continue;

            fs::path filePath = outputDir / name;

            // Create directory if entry ends with /
            if (name[strlen(name) - 1] == '/') {
                fs::create_directories(filePath);
                continue;
            }

            // Create parent directory
            fs::create_directories(filePath.parent_path());

            // Extract file
            zip_file* file = zip_fopen_index(archive, i, 0);
            if (!file) continue;

            std::ofstream out(filePath, std::ios::binary);
            char buffer[8192];
            int64_t bytesRead;

            while ((bytesRead = zip_fread(file, buffer, sizeof(buffer))) > 0) {
                out.write(buffer, bytesRead);
            }

            zip_fclose(file);
            out.close();
        }

        zip_close(archive);
        return true;
    }

    /**
     * Create a temporary directory for extraction
     */
    fs::path createTempDirectory(const std::string& prefix) {
        return fs::temp_directory_path() / (prefix + std::to_string(getpid()));
    }

    /**
     * Initialize SWORD library and load module matching filter
     */
    bool initializeSWORD(const fs::path& modulePath,
                         SWMgr** mgr,
                         SWModule** module,
                         ModuleFilterFunc filter) {
        *mgr = new SWMgr(modulePath.string().c_str(), true, new MarkupFilterMgr(FMT_HTMLHREF));

        // Iterate through all modules and apply filter
        ModMap::iterator it;
        for (it = (*mgr)->Modules.begin(); it != (*mgr)->Modules.end(); ++it) {
            SWModule* mod = it->second;

            if (*module == nullptr && filter(mod, modulePath)) {
                *module = mod;
                break;
            }
        }

        if (*module == nullptr) {
            std::cerr << "No matching SWORD module found in extracted files\n";
            return false;
        }

        return true;
    }

    /**
     * Parse module configuration file (.conf)
     */
    std::map<std::string, std::string> parseModuleConfig(const fs::path& tempDir,
                                                          const std::string& moduleName) {
        std::map<std::string, std::string> config;

        // Find .conf file
        fs::path confPath;
        fs::path modsDir = tempDir / "mods.d";

        if (fs::exists(modsDir)) {
            for (const auto& entry : fs::directory_iterator(modsDir)) {
                if (entry.path().extension() == ".conf") {
                    std::string filename = entry.path().stem().string();
                    std::string lowerFilename = toLower(filename);
                    std::string lowerModuleName = toLower(moduleName);

                    if (lowerFilename == lowerModuleName) {
                        confPath = entry.path();
                        break;
                    }
                }
            }
        }

        if (confPath.empty() || !fs::exists(confPath)) {
            return config; // Return empty map
        }

        // Parse config file. SWORD .conf files repeat a key to continue a long
        // value (notably About=), so repeated keys are appended rather than
        // overwriting — otherwise only the last fragment survives.
        std::ifstream confFile(confPath);
        std::string line;

        while (std::getline(confFile, line)) {
            if (line.find("=") == std::string::npos) continue;

            size_t pos = line.find("=");
            std::string key = trim(line.substr(0, pos));
            std::string value = trim(line.substr(pos + 1));

            auto existing = config.find(key);
            if (existing == config.end()) {
                config[key] = value;
            } else {
                existing->second += " " + value;
            }
        }

        return config;
    }

    /**
     * Set standard SQLite pragmas for performance
     */
    bool setSQLitePragmas(void* dbPtr, int cacheSizeKB) {
        sqlite3* db = static_cast<sqlite3*>(dbPtr);

        std::ostringstream pragmas;
        pragmas << "PRAGMA foreign_keys = ON;"
                << "PRAGMA journal_mode = WAL;"
                << "PRAGMA synchronous = NORMAL;"
                << "PRAGMA temp_store = MEMORY;"
                << "PRAGMA cache_size = -" << cacheSizeKB << ";";

        char* errMsg = nullptr;
        if (sqlite3_exec(db, pragmas.str().c_str(), nullptr, nullptr, &errMsg) != SQLITE_OK) {
            std::cerr << "Failed to set pragmas: " << errMsg << "\n";
            sqlite3_free(errMsg);
            return false;
        }

        return true;
    }

    // ==================================================================
    // Canonical book mapping
    // ==================================================================

    namespace {

        struct CanonEntry { const char* osis; int number; };

        // OSIS book abbreviations for the 66-book Protestant canon, plus the
        // spelling variants that appear in the wild. Anything not listed here
        // (1Esd, Tob, Jdt, Wis, Sir, Bar, EpJer, PrAzar, Sus, Bel, 1Macc,
        // 2Macc, PrMan, AddEsth, AddDan, ...) is deliberately absent so that
        // canonicalBookNumberFromOsis() returns 0 and the caller skips it.
        const CanonEntry CANON_BOOKS[] = {
            {"gen", 1}, {"genesis", 1},
            {"exod", 2}, {"exo", 2}, {"exodus", 2},
            {"lev", 3}, {"leviticus", 3},
            {"num", 4}, {"numbers", 4},
            {"deut", 5}, {"deu", 5}, {"deuteronomy", 5},
            {"josh", 6}, {"joshua", 6},
            {"judg", 7}, {"judges", 7},
            {"ruth", 8},
            {"1sam", 9}, {"1samuel", 9},
            {"2sam", 10}, {"2samuel", 10},
            {"1kgs", 11}, {"1kings", 11},
            {"2kgs", 12}, {"2kings", 12},
            {"1chr", 13}, {"1chronicles", 13},
            {"2chr", 14}, {"2chronicles", 14},
            {"ezra", 15},
            {"neh", 16}, {"nehemiah", 16},
            {"esth", 17}, {"esther", 17},
            {"job", 18},
            {"ps", 19}, {"psa", 19}, {"psalm", 19}, {"psalms", 19},
            {"prov", 20}, {"proverbs", 20},
            {"eccl", 21}, {"ecclesiastes", 21}, {"qoh", 21},
            {"song", 22}, {"sos", 22}, {"songofsongs", 22}, {"cant", 22},
            {"isa", 23}, {"isaiah", 23},
            {"jer", 24}, {"jeremiah", 24},
            {"lam", 25}, {"lamentations", 25},
            {"ezek", 26}, {"ezekiel", 26},
            {"dan", 27}, {"daniel", 27},
            {"hos", 28}, {"hosea", 28},
            {"joel", 29},
            {"amos", 30},
            {"obad", 31}, {"obadiah", 31},
            {"jonah", 32}, {"jon", 32},
            {"mic", 33}, {"micah", 33},
            {"nah", 34}, {"nahum", 34},
            {"hab", 35}, {"habakkuk", 35},
            {"zeph", 36}, {"zephaniah", 36},
            {"hag", 37}, {"haggai", 37},
            {"zech", 38}, {"zechariah", 38},
            {"mal", 39}, {"malachi", 39},
            {"matt", 40}, {"mat", 40}, {"matthew", 40},
            {"mark", 41},
            {"luke", 42},
            {"john", 43},
            {"acts", 44},
            {"rom", 45}, {"romans", 45},
            {"1cor", 46}, {"1corinthians", 46},
            {"2cor", 47}, {"2corinthians", 47},
            {"gal", 48}, {"galatians", 48},
            {"eph", 49}, {"ephesians", 49},
            {"phil", 50}, {"philippians", 50},
            {"col", 51}, {"colossians", 51},
            {"1thess", 52}, {"1thessalonians", 52},
            {"2thess", 53}, {"2thessalonians", 53},
            {"1tim", 54}, {"1timothy", 54},
            {"2tim", 55}, {"2timothy", 55},
            {"titus", 56}, {"tit", 56},
            {"phlm", 57}, {"phlmn", 57}, {"philemon", 57},
            {"heb", 58}, {"hebrews", 58},
            {"jas", 59}, {"james", 59},
            {"1pet", 60}, {"1peter", 60},
            {"2pet", 61}, {"2peter", 61},
            {"1john", 62}, {"1jn", 62},
            {"2john", 63}, {"2jn", 63},
            {"3john", 64}, {"3jn", 64},
            {"jude", 65},
            {"rev", 66}, {"revelation", 66}, {"revofjohn", 66},
        };

    } // anonymous namespace

    int canonicalBookNumberFromOsis(const std::string& osisBook) {
        if (osisBook.empty()) return 0;

        std::string key;
        key.reserve(osisBook.size());
        for (char c : osisBook) {
            // OSIS book names are ASCII; drop separators so "Song_of_Songs"
            // and "SongOfSongs" both normalise the same way.
            if (c == ' ' || c == '_' || c == '-' || c == '.') continue;
            key += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        }

        for (const auto& entry : CANON_BOOKS) {
            if (key == entry.osis) return entry.number;
        }
        return 0;
    }

    std::string osisBookOf(const std::string& osisRef) {
        if (osisRef.empty()) return "";
        // "Gen.1.1", "Gen.1.1-Gen.1.5", "Gen"
        size_t end = osisRef.find_first_of(".-");
        return (end == std::string::npos) ? osisRef : osisRef.substr(0, end);
    }

    int64_t osisRefToVerseId(const std::string& osisRef) {
        if (osisRef.empty()) return 0;

        // Use only the start of a range.
        std::string ref = osisRef;
        size_t dash = ref.find('-');
        if (dash != std::string::npos) ref = ref.substr(0, dash);
        ref = trim(ref);
        if (ref.empty()) return 0;

        // Split on '.' into book / chapter / verse
        std::vector<std::string> parts;
        size_t start = 0;
        while (start <= ref.size()) {
            size_t dot = ref.find('.', start);
            if (dot == std::string::npos) {
                parts.push_back(ref.substr(start));
                break;
            }
            parts.push_back(ref.substr(start, dot - start));
            start = dot + 1;
        }

        if (parts.size() < 3) return 0;

        int book = canonicalBookNumberFromOsis(parts[0]);
        if (book == 0) return 0;

        int chapter = 0;
        int verse = 0;
        try {
            chapter = std::stoi(parts[1]);
            verse = std::stoi(parts[2]);
        } catch (const std::exception&) {
            return 0;
        }

        if (chapter <= 0 || chapter > 999 || verse <= 0 || verse > 999) return 0;
        return calculateVerseId(book, chapter, verse);
    }

    // ==================================================================
    // OSIS -> clean text + structured spans
    // ==================================================================

    namespace {

        /** Lowercase the element name of a tag body ("w lemma=..." -> "w"). */
        std::string tagName(const std::string& body) {
            size_t i = 0;
            while (i < body.size() && (body[i] == '/' || std::isspace(static_cast<unsigned char>(body[i])))) i++;
            size_t start = i;
            while (i < body.size() && !std::isspace(static_cast<unsigned char>(body[i]))
                   && body[i] != '/' && body[i] != '>') i++;
            std::string name = body.substr(start, i - start);
            std::transform(name.begin(), name.end(), name.begin(),
                           [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
            return name;
        }

        /**
         * Read an attribute value out of a tag body. Returns "" when absent.
         * Handles double-quoted, single-quoted and unquoted values, because
         * SWORD's rendered HTML is not consistently quoted.
         */
        std::string tagAttr(const std::string& body, const std::string& attr) {
            const std::string needle = attr + "=";
            size_t pos = 0;
            while ((pos = body.find(needle, pos)) != std::string::npos) {
                // Require a word boundary before the attribute name so that
                // looking for "type" does not match "subType".
                const bool boundary =
                    (pos == 0) || std::isspace(static_cast<unsigned char>(body[pos - 1]));
                if (!boundary) {
                    pos += needle.size();
                    continue;
                }

                size_t valueStart = pos + needle.size();
                while (valueStart < body.size()
                       && std::isspace(static_cast<unsigned char>(body[valueStart]))) {
                    valueStart++;
                }
                if (valueStart >= body.size()) return "";

                const char quote = body[valueStart];
                if (quote == '"' || quote == '\'') {
                    const size_t valueEnd = body.find(quote, valueStart + 1);
                    if (valueEnd == std::string::npos) return "";
                    return body.substr(valueStart + 1, valueEnd - valueStart - 1);
                }

                size_t valueEnd = valueStart;
                while (valueEnd < body.size()
                       && !std::isspace(static_cast<unsigned char>(body[valueEnd]))
                       && body[valueEnd] != '>' && body[valueEnd] != '/') {
                    valueEnd++;
                }
                return body.substr(valueStart, valueEnd - valueStart);
            }
            return "";
        }

        bool attrContains(const std::string& value, const std::string& needle) {
            std::string lower = value;
            std::transform(lower.begin(), lower.end(), lower.begin(),
                           [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
            return lower.find(needle) != std::string::npos;
        }

        /**
         * Decode an HTML/XML entity starting at `html[i]` (which is '&').
         * On success sets `out` to the decoded text and returns the index just
         * past the ';'. On failure returns i (caller treats '&' literally).
         */
        size_t decodeEntity(const std::string& s, size_t i, std::string& out) {
            size_t end = s.find(';', i);
            if (end == std::string::npos || end - i > 10) return i;

            std::string name = s.substr(i + 1, end - i - 1);
            if (name.empty()) return i;

            // Same named-entity table as packages/core/src/Data/Text/normalizeVerseText.ts,
            // so the two implementations produce identical clean text.
            std::string lower = name;
            std::transform(lower.begin(), lower.end(), lower.begin(),
                           [](unsigned char c) { return static_cast<char>(std::tolower(c)); });

            if (lower == "amp")    { out = "&";  return end + 1; }
            if (lower == "lt")     { out = "<";  return end + 1; }
            if (lower == "gt")     { out = ">";  return end + 1; }
            if (lower == "quot")   { out = "\""; return end + 1; }
            if (lower == "apos")   { out = "'";  return end + 1; }
            if (lower == "nbsp")   { out = " ";  return end + 1; }
            if (lower == "para")   { out = "\xC2\xB6"; return end + 1; }
            if (lower == "ndash")  { out = "\xE2\x80\x93"; return end + 1; }  // –
            if (lower == "mdash")  { out = "\xE2\x80\x94"; return end + 1; }  // —
            if (lower == "hellip") { out = "\xE2\x80\xA6"; return end + 1; }  // …
            if (lower == "rsquo")  { out = "\xE2\x80\x99"; return end + 1; }  // ’
            if (lower == "lsquo")  { out = "\xE2\x80\x98"; return end + 1; }  // ‘
            if (lower == "rdquo")  { out = "\xE2\x80\x9D"; return end + 1; }  // ”
            if (lower == "ldquo")  { out = "\xE2\x80\x9C"; return end + 1; }  // “

            if (name[0] == '#') {
                // Numeric entity; emit UTF-8.
                long code = 0;
                try {
                    code = (name.size() > 2 && (name[1] == 'x' || name[1] == 'X'))
                        ? std::stol(name.substr(2), nullptr, 16)
                        : std::stol(name.substr(1), nullptr, 10);
                } catch (const std::exception&) {
                    return i;
                }
                if (code <= 0) return i;

                std::string utf8;
                if (code < 0x80) {
                    utf8 += static_cast<char>(code);
                } else if (code < 0x800) {
                    utf8 += static_cast<char>(0xC0 | (code >> 6));
                    utf8 += static_cast<char>(0x80 | (code & 0x3F));
                } else if (code < 0x10000) {
                    utf8 += static_cast<char>(0xE0 | (code >> 12));
                    utf8 += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
                    utf8 += static_cast<char>(0x80 | (code & 0x3F));
                } else {
                    utf8 += static_cast<char>(0xF0 | (code >> 18));
                    utf8 += static_cast<char>(0x80 | ((code >> 12) & 0x3F));
                    utf8 += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
                    utf8 += static_cast<char>(0x80 | (code & 0x3F));
                }
                out = utf8;
                return end + 1;
            }

            return i;
        }

        /** An element that is currently open and contributing a span. */
        struct OpenSpan {
            std::string element;   // lowercase element name
            std::string type;      // span type
            std::string sid;       // milestone sID, when the element is a milestone pair
            int startWord = 0;
            int64_t ref = 0;
            bool drop = false;     // this element's text is not verse text
        };

        /**
         * Extract every Strong's number from a `lemma` attribute value.
         * "strong:G3588 strong:G2316 lemma.TR:o lemma.TR:qeos" -> {"G3588","G2316"}
         */
        std::vector<std::string> extractStrongs(const std::string& lemmaValue) {
            std::vector<std::string> numbers;
            size_t pos = 0;
            while ((pos = lemmaValue.find("strong:", pos)) != std::string::npos) {
                pos += 7;
                size_t end = pos;
                while (end < lemmaValue.size()
                       && !std::isspace(static_cast<unsigned char>(lemmaValue[end]))) {
                    end++;
                }
                std::string num = lemmaValue.substr(pos, end - pos);
                if (!num.empty() && (num[0] == 'G' || num[0] == 'H' || num[0] == 'g' || num[0] == 'h')) {
                    num[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(num[0])));
                    numbers.push_back(num);
                }
                pos = end;
            }
            return numbers;
        }

        /**
         * Extract the original-language word from a `lemma` attribute value.
         * Prefers lemma.TR: over a bare lemma:.
         */
        std::string extractOriginalWord(const std::string& lemmaValue) {
            for (const char* prefix : {"lemma.TR:", "lemma:"}) {
                const size_t prefixLen = std::strlen(prefix);
                size_t pos = lemmaValue.find(prefix);
                if (pos == std::string::npos) continue;
                pos += prefixLen;
                size_t end = pos;
                while (end < lemmaValue.size()
                       && !std::isspace(static_cast<unsigned char>(lemmaValue[end]))) {
                    end++;
                }
                std::string word = lemmaValue.substr(pos, end - pos);
                if (!word.empty()) return word;
            }
            return "";
        }

        /**
         * Strip scheme prefixes from morphology codes.
         * "robinson:T-NSM robinson:N-NSM" -> "T-NSM N-NSM"
         */
        std::string stripMorphPrefixes(const std::string& morphValue) {
            std::string result;
            std::istringstream stream(morphValue);
            std::string token;
            while (stream >> token) {
                const size_t colon = token.find(':');
                const std::string code = (colon != std::string::npos) ? token.substr(colon + 1) : token;
                if (code.empty()) continue;
                if (!result.empty()) result += ' ';
                result += code;
            }
            return result;
        }

        /**
         * Tags that separate lines or blocks. They contribute a word boundary,
         * because the data relies on them for word separation: ISV Rev 19:16
         * `written:<br />K<small>ING OF</small>` collapses to `written:KING`
         * without this. Inline tags (<i>, <font>, <small>, <a>) contribute
         * nothing, because `L<font size="-1">ORD</font>` must stay one word.
         *
         * Kept in sync with LAYOUT_TAG_NAMES in
         * packages/core/src/Data/Text/normalizeVerseText.ts.
         */
        bool isLayoutElement(const std::string& name) {
            static const char* const NAMES[] = {
                "br", "hr", "p", "div", "ul", "ol", "li", "dl", "dt", "dd",
                "table", "tr", "td", "th", "blockquote",
                // OSIS block structure
                "lb", "l", "lg", "title"
            };
            for (const char* n : NAMES) {
                if (name == n) return true;
            }
            return false;
        }

        /**
         * Elements whose opening/closing tags we pair up.
         *
         * An element must be tracked even when it maps to no span, otherwise a
         * closing tag can pair with the wrong ancestor: in
         * `<font color="red">A <font face="x">B</font> C</font>` the inner
         * `</font>` would otherwise close the red span three words early.
         */
        bool isTrackedElement(const std::string& name) {
            static const char* const NAMES[] = {
                "divinename", "transchange", "hi", "q", "seg", "foreign",
                "translit", "transliteration", "reference",
                "i", "b", "em", "strong", "small", "font", "cite", "sup"
            };
            for (const char* n : NAMES) {
                if (name == n) return true;
            }
            return false;
        }

        /** Split clean text (single-space separated) into words. */
        std::vector<std::string> splitWords(const std::string& text) {
            std::vector<std::string> words;
            size_t start = 0;
            while (start < text.size()) {
                size_t space = text.find(' ', start);
                if (space == std::string::npos) {
                    words.push_back(text.substr(start));
                    break;
                }
                words.push_back(text.substr(start, space - start));
                start = space + 1;
            }
            return words;
        }

        /**
         * Byte length of a Unicode space character starting at s[i] (NBSP,
         * U+2000-200A, U+202F, U+205F, U+3000), or 0. These separate words
         * exactly like ASCII whitespace; left inside the text a lone NBSP became
         * a "word" of its own (Vulgate Dan 12:13) and broke the single-space
         * convention that span offsets rely on.
         */
        size_t unicodeSpaceLength(const std::string& s, size_t i) {
            const auto b = [&](size_t k) -> unsigned {
                return k < s.size() ? static_cast<unsigned char>(s[k]) : 0u;
            };
            if (b(i) == 0xC2 && b(i + 1) == 0xA0) return 2;
            if (b(i) == 0xE2 && b(i + 1) == 0x80 && (b(i + 2) <= 0x8A || b(i + 2) == 0xAF)) return 3;
            if (b(i) == 0xE2 && b(i + 1) == 0x81 && b(i + 2) == 0x9F) return 3;
            if (b(i) == 0xE3 && b(i + 1) == 0x80 && b(i + 2) == 0x80) return 3;
            return 0;
        }

        /**
         * Some modules ship OSIS elements escaped as text: RusSynodal has
         * `&lt;note&gt;...&lt;/note&gt;` and `&lt;title type="psalm"&gt;` inside
         * verses. libsword displays them as a literal "note...", and decoding
         * the entities put real <note> tags into bible_verse.text. Restore only
         * well-known OSIS element names, so ordinary text such as "&lt;" stays
         * text.
         */
        std::string unescapeEscapedOsisMarkup(const std::string& s) {
            if (s.find("&lt;") == std::string::npos) return s;
            static const char* const NAMES[] = {
                "note", "title", "q", "hi", "divineName", "transChange", "lb", "l", "lg",
                "milestone", "foreign", "seg", "w", "reference", "p", "div", "catchWord", "rdg",
            };
            std::string out;
            out.reserve(s.size());
            size_t i = 0;
            while (i < s.size()) {
                if (s.compare(i, 4, "&lt;") == 0) {
                    size_t j = i + 4;
                    const bool closingTag = (j < s.size() && s[j] == '/');
                    if (closingTag) j++;
                    size_t nameEnd = j;
                    while (nameEnd < s.size() && std::isalpha(static_cast<unsigned char>(s[nameEnd]))) nameEnd++;
                    const std::string name = s.substr(j, nameEnd - j);
                    bool known = false;
                    for (const char* n : NAMES) {
                        if (name == n) { known = true; break; }
                    }
                    const bool boundary = nameEnd < s.size()
                        && (s[nameEnd] == ' ' || s[nameEnd] == '/' || s.compare(nameEnd, 4, "&gt;") == 0);
                    const size_t gt = s.find("&gt;", nameEnd);
                    const size_t nextLt = s.find("&lt;", nameEnd);
                    if (known && boundary && gt != std::string::npos && gt - i < 400
                        && (nextLt == std::string::npos || nextLt > gt)) {
                        std::string attrs = s.substr(nameEnd, gt - nameEnd);
                        size_t q;
                        while ((q = attrs.find("&quot;")) != std::string::npos) attrs.replace(q, 6, "\"");
                        while ((q = attrs.find("&amp;")) != std::string::npos) attrs.replace(q, 5, "&");
                        out += '<';
                        if (closingTag) out += '/';
                        out += name;
                        out += attrs;
                        out += '>';
                        i = gt + 4;
                        continue;
                    }
                }
                out += s[i];
                i++;
            }
            return out;
        }

    } // anonymous namespace

    OsisVerseResult parseOsisVerse(const std::string& osisInput) {
        const std::string osis = unescapeEscapedOsisMarkup(osisInput);
        OsisVerseResult result;
        result.text.reserve(osis.size());

        std::vector<OpenSpan> open;
        std::vector<InterlinearRecord> openWords;   // nested <w> elements
        std::string currentWord;
        std::string headingBuffer;

        int noteDepth = 0;    // inside <note> — discard everything
        int titleDepth = 0;   // inside <title> — divert to heading
        int dropDepth = 0;    // inside <sup class="n"> — a footnote marker, not text

        // Word index at which the last paragraph marker was seen.
        //  == 0                -> this verse starts a paragraph
        //  == final wordCount  -> the marker belongs to the NEXT verse
        //  anything between    -> a mid-verse break, not expressible per verse
        int lastParagraphMarkerAt = -1;

        // Emit the pending word, separated from the previous one by a single
        // space. This normalisation is what makes span offsets addressable:
        // splitting result.text on ' ' yields exactly result.wordCount words.
        auto flushWord = [&]() {
            if (currentWord.empty()) return;
            if (result.wordCount > 0) result.text += ' ';
            result.text += currentWord;
            currentWord.clear();
            result.wordCount++;
        };

        auto appendText = [&](const std::string& chunk) {
            for (char c : chunk) {
                if (std::isspace(static_cast<unsigned char>(c))) {
                    flushWord();
                } else {
                    currentWord += c;
                }
            }
        };

        // Record a paragraph marker at the current position. Only a marker at
        // word 0 sets paragraph_start on THIS verse; a trailing one is reported
        // separately so the caller can hand it to the next verse (AKJV-style
        // sources put the pilcrow at the end of the preceding verse).
        auto noteParagraphMarker = [&]() {
            const int at = result.wordCount + (currentWord.empty() ? 0 : 1);
            if (at == 0) result.block.paragraphStart = true;
            lastParagraphMarkerAt = at;
        };

        auto closeSpan = [&](const std::string& element, const std::string& eid) {
            for (auto it = open.rbegin(); it != open.rend(); ++it) {
                bool matches = (it->element == element);
                if (matches && !eid.empty() && !it->sid.empty()) {
                    matches = (it->sid == eid);
                }
                if (!matches) continue;

                if (it->drop && dropDepth > 0) dropDepth--;

                // A span that closes mid-word still covers that word.
                int endWord = currentWord.empty() ? result.wordCount - 1 : result.wordCount;
                if (!it->type.empty() && endWord >= it->startWord) {
                    FormatSpan span;
                    span.type = it->type;
                    span.start = it->startWord;
                    span.end = endWord;
                    span.ref = it->ref;
                    result.spans.push_back(span);
                }
                open.erase(std::next(it).base());
                return;
            }
        };

        size_t i = 0;
        while (i < osis.size()) {
            // ---------------------------------------------------------- tag
            if (osis[i] == '<') {
                size_t tagEnd = osis.find('>', i);
                if (tagEnd == std::string::npos) {
                    // Malformed markup — treat as literal text.
                    if (noteDepth == 0) {
                        if (titleDepth > 0) headingBuffer += '<'; else currentWord += '<';
                    }
                    i++;
                    continue;
                }

                std::string body = osis.substr(i + 1, tagEnd - i - 1);
                i = tagEnd + 1;

                bool closing = (!body.empty() && body[0] == '/');
                bool selfClosing = (!body.empty() && body[body.size() - 1] == '/');
                std::string name = tagName(body);

                // SWORD's HTML filters emit <!P> / <!/P> as paragraph markers.
                // These are not well-formed tags — tagName() cannot parse the
                // closing form — so they are matched on the raw body instead.
                if (!body.empty() && body[0] == '!') {
                    if (noteDepth == 0 && titleDepth == 0) {
                        flushWord();
                        // <!P> opens a paragraph; <!/P> only closes one.
                        if (body.size() >= 2 && (body[1] == 'P' || body[1] == 'p')) {
                            noteParagraphMarker();
                        }
                    }
                    continue;
                }

                // A layout tag is a word boundary regardless of what else it
                // means (see isLayoutElement). Suppressed inside <note>/<title>,
                // whose text is not part of the verse body.
                if (noteDepth == 0 && titleDepth == 0 && isLayoutElement(name)) {
                    flushWord();
                }

                // <note> swallows everything, including nested markup.
                if (name == "note") {
                    if (closing) {
                        if (noteDepth > 0) noteDepth--;
                    } else if (!selfClosing) {
                        noteDepth++;
                    }
                    continue;
                }
                if (noteDepth > 0) continue;

                // <title> is lifted out of the verse body into block.heading.
                if (name == "title") {
                    if (closing) {
                        if (titleDepth > 0) titleDepth--;
                    } else if (!selfClosing) {
                        titleDepth++;
                        if (!headingBuffer.empty()) headingBuffer += ' ';
                    }
                    continue;
                }
                if (titleDepth > 0) continue;   // ignore markup inside a title

                const std::string sid = tagAttr(body, "sID");
                const std::string eid = tagAttr(body, "eID");
                const std::string type = tagAttr(body, "type");

                // --- <w>: interlinear data, anchored to real word offsets ---
                if (name == "w") {
                    if (closing) {
                        if (!openWords.empty()) {
                            InterlinearRecord rec = openWords.back();
                            openWords.pop_back();
                            // A <w> that closes mid-word still covers that word.
                            rec.endWord = currentWord.empty() ? result.wordCount - 1 : result.wordCount;
                            rec.hasGloss = true;
                            if (rec.endWord >= rec.startWord && !rec.strongsNumber.empty()) {
                                result.interlinear.push_back(rec);
                            }
                        }
                        continue;
                    }

                    InterlinearRecord rec;
                    // Raw OSIS uses lemma=; SWORD's rendered HTML uses savlm=.
                    std::string lemma = tagAttr(body, "lemma");
                    if (lemma.empty()) lemma = tagAttr(body, "savlm");

                    const std::vector<std::string> strongs = extractStrongs(lemma);
                    if (!strongs.empty()) {
                        rec.strongsNumber = strongs[0];
                        for (size_t s = 0; s < strongs.size(); s++) {
                            if (s > 0) rec.allStrongs += ' ';
                            rec.allStrongs += strongs[s];
                        }
                    }
                    rec.originalWord = extractOriginalWord(lemma);
                    rec.morphology = stripMorphPrefixes(tagAttr(body, "morph"));
                    rec.startWord = result.wordCount;

                    if (selfClosing) {
                        // No English equivalent (e.g. a Greek article). Anchor it to
                        // the word that follows; clamped to range once the verse ends.
                        rec.endWord = rec.startWord;
                        rec.hasGloss = false;
                        if (!rec.strongsNumber.empty()) result.interlinear.push_back(rec);
                    } else {
                        openWords.push_back(rec);
                    }
                    continue;
                }

                // --- block-level markers -----------------------------------
                if (name == "lb") {
                    continue;   // already flushed as a layout element
                }
                if (name == "milestone") {
                    if (attrContains(type, "x-p") || attrContains(type, "paragraph")) {
                        noteParagraphMarker();
                    }
                    continue;
                }
                if (name == "p") {
                    if (!closing) noteParagraphMarker();
                    continue;
                }
                if (name == "div") {
                    if (!closing && attrContains(type, "paragraph")) {
                        noteParagraphMarker();
                    }
                    continue;
                }
                if (name == "lg") {
                    if (!closing && !selfClosing && result.block.poetryLevel == 0) {
                        result.block.poetryLevel = 1;
                    }
                    continue;
                }
                if (name == "l") {
                    if (!closing) {
                        if (attrContains(type, "selah")) {
                            result.block.selah = true;
                        }
                        int level = 1;
                        const std::string levelAttr = tagAttr(body, "level");
                        if (!levelAttr.empty()) {
                            try { level = std::stoi(levelAttr); } catch (const std::exception&) { level = 1; }
                        }
                        if (level < 1) level = 1;
                        if (level > 3) level = 3;
                        if (level > result.block.poetryLevel) result.block.poetryLevel = level;
                    } else {
                        flushWord();
                    }
                    continue;
                }

                // --- span-level markers ------------------------------------
                std::string spanType;
                int64_t spanRef = 0;
                bool dropContent = false;   // element whose text is not verse text

                if (name == "divinename") {
                    spanType = "divine_name";
                } else if (name == "transchange") {
                    // KJV et al: <transChange type="added">
                    spanType = (type.empty() || attrContains(type, "added")) ? "supplied" : "emphasis";
                } else if (name == "hi") {
                    // Only map presentation that carries known meaning. type="super"
                    // and friends are typographic noise and get no span.
                    if (attrContains(type, "italic")) spanType = "supplied";
                    else if (attrContains(type, "emphasis") || attrContains(type, "bold")) spanType = "emphasis";
                    else if (type.empty()) spanType = "emphasis";
                } else if (name == "q") {
                    const std::string who = tagAttr(body, "who");
                    if (attrContains(who, "jesus")) {
                        spanType = "words_of_christ";
                    } else {
                        // Matches spanForTag() in normalizeVerseText.ts, which
                        // maps <q> to quotation. `ref` is filled in only when
                        // the source names the passage being quoted.
                        spanType = "quotation";
                        spanRef = osisRefToVerseId(tagAttr(body, "osisRef"));
                    }
                } else if (name == "seg") {
                    if (attrContains(type, "selah")) {
                        result.block.selah = true;
                    }
                    // Alternate readings: <seg type="x-variant" subType="x-2">.
                    // libsword shows only the primary reading (x-1) by default
                    // ("Textual Variants" = Primary Reading). Keeping every
                    // reading put two psalters into one verse in the Vulgate
                    // (Gallican + iuxta Hebraeos), +5% words module-wide.
                    if (!closing && attrContains(type, "x-variant")) {
                        const std::string sub = toLower(tagAttr(body, "subType"));
                        if (!sub.empty() && sub != "x-1") dropContent = true;
                    }
                } else if (name == "foreign" || name == "translit" || name == "transliteration") {
                    spanType = "transliteration";
                } else if (name == "reference") {
                    const std::string osisRef = tagAttr(body, "osisRef");
                    int64_t refId = osisRefToVerseId(osisRef);
                    if (refId != 0) {
                        spanType = "quotation";
                        spanRef = refId;
                    }
                }
                // --- SWORD's rendered-HTML vocabulary ----------------------
                // Not every module is OSIS-sourced (GBF and ThML modules only
                // yield usable markup through the filter chain), so the same
                // parser understands the HTML those filters emit.
                //
                // This mapping is kept identical to spanForTag() in
                // packages/core/src/Data/Text/normalizeVerseText.ts, which was
                // derived from a full inventory of the 29 distinct tag strings
                // present across all 53 shipped bible modules. Deliberately
                // unmapped there and here: <small> (ISV uses it for "BABYLON THE
                // GREAT", not divine names), <sup> (ABP word-order numerals),
                // <a>, <ul>, <li>, <br>.
                else if (name == "i") {
                    spanType = "supplied";
                } else if (name == "b" || name == "strong" || name == "em") {
                    spanType = "emphasis";
                } else if (name == "cite") {
                    spanType = "quotation";
                } else if (name == "font") {
                    const std::string color = toLower(tagAttr(body, "color"));
                    const std::string size = trim(tagAttr(body, "size"));
                    if (color == "red" || color == "#ff0000" || color == "#f00") {
                        spanType = "words_of_christ";
                    } else if (!size.empty() && size[0] == '-') {
                        // Any negative size is SWORD's small-caps divine name.
                        spanType = "divine_name";
                    }
                } else if (name == "sup") {
                    // <sup class="n"> is a footnote marker, not verse text.
                    if (!closing && attrContains(tagAttr(body, "class"), "n")) {
                        dropContent = true;
                    }
                }

                // Elements we deliberately pass through without a span:
                // w, seg (non-selah), name, rdg, catchWord, verse, chapter, a, span, ...

                if (closing) {
                    closeSpan(name, "");
                } else if (!eid.empty()) {
                    closeSpan(name, eid);
                } else if (selfClosing && sid.empty()) {
                    // Self-closing with no milestone id contributes nothing.
                } else if (!spanType.empty() || !sid.empty() || dropContent || isTrackedElement(name)) {
                    // A tracked element is pushed even when it maps to no span,
                    // so that its closing tag pairs with it rather than with an
                    // enclosing element of the same name. An entry with an empty
                    // type records nothing when it closes.
                    OpenSpan os;
                    os.element = name;
                    os.type = spanType;
                    os.sid = sid;
                    // A span that opens mid-word covers that word; a span that
                    // opens between words covers the word that comes next.
                    // Both are index result.wordCount.
                    os.startWord = result.wordCount;
                    os.ref = spanRef;
                    os.drop = dropContent;
                    if (dropContent) dropDepth++;
                    open.push_back(os);
                }

                continue;
            }

            // ------------------------------------------------------- entity
            if (osis[i] == '&') {
                std::string decoded;
                size_t next = decodeEntity(osis, i, decoded);
                // Double-escaped entities ("&amp;#x2013;") decode to "&" plus a
                // second entity; libsword shows the final character, so do too.
                if (next != i && decoded == "&" && next < osis.size()) {
                    const std::string tail = "&" + osis.substr(next, 12);
                    std::string inner;
                    const size_t innerEnd = decodeEntity(tail, 0, inner);
                    if (innerEnd != 0) {
                        decoded = inner;
                        next += innerEnd - 1;
                    }
                }
                if (next != i) {
                    if (noteDepth == 0) {
                        if (titleDepth > 0) headingBuffer += decoded;
                        else if (decoded == "\xC2\xB6") noteParagraphMarker();
                        else if (dropDepth == 0) appendText(decoded);
                    }
                    i = next;
                    continue;
                }
                if (noteDepth == 0 && dropDepth == 0) {
                    if (titleDepth > 0) headingBuffer += '&'; else currentWord += '&';
                }
                i++;
                continue;
            }

            // --------------------------------------------------- pilcrow (¶)
            // The pilcrow itself never reaches `text`; it becomes block state.
            // Where it sits matters: leading means "this verse starts a
            // paragraph", trailing means "the NEXT verse does" (AKJV-style
            // sources put it at the end of the preceding verse).
            if (static_cast<unsigned char>(osis[i]) == 0xC2 && i + 1 < osis.size()
                && static_cast<unsigned char>(osis[i + 1]) == 0xB6) {
                if (noteDepth == 0 && titleDepth == 0) {
                    noteParagraphMarker();
                }
                i += 2;
                continue;
            }

            // ------------------------------------------------- literal char
            if (noteDepth > 0) { i++; continue; }

            if (const size_t spaceLen = unicodeSpaceLength(osis, i)) {
                if (titleDepth > 0) headingBuffer += ' ';
                else flushWord();
                i += spaceLen;
                continue;
            }

            // Literal USFM character markers left in the source ("\it вслух\it*"
            // in RSP's headings) are markup, not text; bible_verse.text MUST NOT
            // contain a backslash. Drop "\name", "\name*", "\+name" and "\name1".
            if (osis[i] == '\\' && i + 1 < osis.size()) {
                size_t j = i + 1;
                if (osis[j] == '+') j++;
                const size_t nameStart = j;
                while (j < osis.size() && std::islower(static_cast<unsigned char>(osis[j]))) j++;
                if (j > nameStart && j - nameStart <= 8) {
                    while (j < osis.size() && std::isdigit(static_cast<unsigned char>(osis[j]))) j++;
                    if (j < osis.size() && osis[j] == '*') j++;
                    i = j;
                    continue;
                }
            }

            if (titleDepth > 0) {
                headingBuffer += osis[i];
            } else if (std::isspace(static_cast<unsigned char>(osis[i]))) {
                flushWord();
            } else if (dropDepth == 0) {
                currentWord += osis[i];
            }
            i++;
        }

        flushWord();

        // A paragraph marker sitting after the last word belongs to the next
        // verse, not this one. Only the source document makes this visible, so
        // the converter carries it forward rather than losing it.
        if (lastParagraphMarkerAt >= 0 && lastParagraphMarkerAt == result.wordCount
            && result.wordCount > 0) {
            result.trailingParagraphMarker = true;
        }

        // Close any <w> left open by malformed markup at the last word.
        while (!openWords.empty()) {
            InterlinearRecord rec = openWords.back();
            openWords.pop_back();
            rec.endWord = result.wordCount - 1;
            rec.hasGloss = true;
            if (rec.endWord >= rec.startWord && !rec.strongsNumber.empty()) {
                result.interlinear.push_back(rec);
            }
        }

        // Close anything left open (malformed markup) at the last word.
        while (!open.empty()) {
            const OpenSpan& os = open.back();
            if (!os.type.empty() && result.wordCount - 1 >= os.startWord) {
                FormatSpan span;
                span.type = os.type;
                span.start = os.startWord;
                span.end = result.wordCount - 1;
                span.ref = os.ref;
                result.spans.push_back(span);
            }
            open.pop_back();
        }

        // Normalise the heading: collapse whitespace, trim.
        {
            std::string heading;
            bool pendingSpace = false;
            for (char c : headingBuffer) {
                if (std::isspace(static_cast<unsigned char>(c))) {
                    pendingSpace = !heading.empty();
                } else {
                    if (pendingSpace) { heading += ' '; pendingSpace = false; }
                    heading += c;
                }
            }
            result.block.heading = heading;
        }

        // Deterministic order: by start, then end, then type.
        std::sort(result.spans.begin(), result.spans.end(),
                  [](const FormatSpan& a, const FormatSpan& b) {
                      if (a.start != b.start) return a.start < b.start;
                      if (a.end != b.end) return a.end < b.end;
                      return a.type < b.type;
                  });

        // Finalise interlinear records: clamp offsets into range and fill the
        // gloss from the clean text now that every word is known.
        if (!result.interlinear.empty()) {
            const std::vector<std::string> words = splitWords(result.text);
            const int last = static_cast<int>(words.size()) - 1;

            std::vector<InterlinearRecord> kept;
            kept.reserve(result.interlinear.size());

            for (InterlinearRecord& rec : result.interlinear) {
                if (last < 0) continue;                       // verse has no words
                if (rec.startWord > last) rec.startWord = last;
                if (rec.endWord > last) rec.endWord = last;
                if (rec.startWord < 0) rec.startWord = 0;
                if (rec.endWord < rec.startWord) rec.endWord = rec.startWord;

                if (rec.hasGloss) {
                    std::string gloss;
                    for (int wi = rec.startWord; wi <= rec.endWord; wi++) {
                        if (!gloss.empty()) gloss += ' ';
                        gloss += words[static_cast<size_t>(wi)];
                    }
                    rec.gloss = gloss;
                }
                kept.push_back(rec);
            }

            result.interlinear.swap(kept);

            std::stable_sort(result.interlinear.begin(), result.interlinear.end(),
                             [](const InterlinearRecord& a, const InterlinearRecord& b) {
                                 return a.startWord < b.startWord;
                             });
        }

        return result;
    }

    std::string buildFormattingJson(const OsisVerseResult& result) {
        if (result.block.isEmpty() && result.spans.empty() && result.sourceVerses.empty()) return "";

        std::ostringstream json;
        json << "{\"v\":1";

        if (!result.block.isEmpty()) {
            json << ",\"block\":{";
            bool first = true;
            if (result.block.paragraphStart) {
                json << "\"paragraph_start\":true";
                first = false;
            }
            if (result.block.poetryLevel > 0) {
                if (!first) json << ",";
                json << "\"poetry_level\":" << result.block.poetryLevel;
                first = false;
            }
            if (!result.block.heading.empty()) {
                if (!first) json << ",";
                json << "\"heading\":\"" << jsonEscape(result.block.heading) << "\"";
                first = false;
            }
            if (result.block.selah) {
                if (!first) json << ",";
                json << "\"selah\":true";
                first = false;
            }
            json << "}";
        }

        if (!result.spans.empty()) {
            json << ",\"spans\":[";
            for (size_t i = 0; i < result.spans.size(); i++) {
                const FormatSpan& s = result.spans[i];
                if (i > 0) json << ",";
                json << "{\"type\":\"" << jsonEscape(s.type) << "\""
                     << ",\"start\":" << s.start
                     << ",\"end\":" << s.end;
                if (s.ref != 0) json << ",\"ref\":" << s.ref;
                json << "}";
            }
            json << "]";
        }

        // Provenance for merged variant verse divisions (see SourceVerseRecord).
        if (!result.sourceVerses.empty()) {
            json << ",\"source_verses\":[";
            for (size_t i = 0; i < result.sourceVerses.size(); i++) {
                const SourceVerseRecord& sv = result.sourceVerses[i];
                if (i > 0) json << ",";
                json << "{\"chapter\":" << sv.chapter
                     << ",\"verse\":" << sv.verse
                     << ",\"start\":" << sv.startWord
                     << ",\"end\":" << sv.endWord << "}";
            }
            json << "]";
        }

        json << "}";
        return json.str();
    }

    // ==================================================================
    // Identity / provenance
    // ==================================================================

    namespace {

        inline uint32_t rotr32(uint32_t x, uint32_t n) {
            return (x >> n) | (x << (32 - n));
        }

        const uint32_t SHA256_K[64] = {
            0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
            0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
            0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
            0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
            0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
            0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
            0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
            0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
            0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
            0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
            0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
            0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
            0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
            0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
            0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
            0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
        };

        void sha256Block(const unsigned char* block, uint32_t h[8]) {
            uint32_t w[64];
            for (int t = 0; t < 16; t++) {
                w[t] = (static_cast<uint32_t>(block[t * 4]) << 24)
                     | (static_cast<uint32_t>(block[t * 4 + 1]) << 16)
                     | (static_cast<uint32_t>(block[t * 4 + 2]) << 8)
                     | (static_cast<uint32_t>(block[t * 4 + 3]));
            }
            for (int t = 16; t < 64; t++) {
                uint32_t s0 = rotr32(w[t - 15], 7) ^ rotr32(w[t - 15], 18) ^ (w[t - 15] >> 3);
                uint32_t s1 = rotr32(w[t - 2], 17) ^ rotr32(w[t - 2], 19) ^ (w[t - 2] >> 10);
                w[t] = w[t - 16] + s0 + w[t - 7] + s1;
            }

            uint32_t a = h[0], b = h[1], c = h[2], d = h[3];
            uint32_t e = h[4], f = h[5], g = h[6], hh = h[7];

            for (int t = 0; t < 64; t++) {
                uint32_t S1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
                uint32_t ch = (e & f) ^ ((~e) & g);
                uint32_t temp1 = hh + S1 + ch + SHA256_K[t] + w[t];
                uint32_t S0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
                uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
                uint32_t temp2 = S0 + maj;

                hh = g; g = f; f = e;
                e = d + temp1;
                d = c; c = b; b = a;
                a = temp1 + temp2;
            }

            h[0] += a; h[1] += b; h[2] += c; h[3] += d;
            h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
        }

    } // anonymous namespace

    std::string sha256Hex(const std::string& data) {
        uint32_t h[8] = {
            0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
            0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u
        };

        const unsigned char* bytes = reinterpret_cast<const unsigned char*>(data.data());
        const size_t length = data.size();

        size_t offset = 0;
        while (offset + 64 <= length) {
            sha256Block(bytes + offset, h);
            offset += 64;
        }

        // Final block(s): remaining bytes + 0x80 + zero pad + 64-bit big-endian
        // bit length. Two blocks are needed when the remainder is >= 56.
        unsigned char tail[128];
        std::memset(tail, 0, sizeof(tail));
        const size_t remaining = length - offset;
        if (remaining > 0) std::memcpy(tail, bytes + offset, remaining);
        tail[remaining] = 0x80;

        const size_t tailBlocks = (remaining >= 56) ? 2 : 1;
        const size_t lengthPos = tailBlocks * 64 - 8;
        const uint64_t bitLength = static_cast<uint64_t>(length) * 8ull;
        for (int i = 0; i < 8; i++) {
            tail[lengthPos + i] = static_cast<unsigned char>((bitLength >> (56 - 8 * i)) & 0xFF);
        }
        for (size_t b = 0; b < tailBlocks; b++) {
            sha256Block(tail + b * 64, h);
        }

        static const char* HEX = "0123456789abcdef";
        std::string out;
        out.reserve(64);
        for (int i = 0; i < 8; i++) {
            for (int shift = 28; shift >= 0; shift -= 4) {
                out += HEX[(h[i] >> shift) & 0xF];
            }
        }
        return out;
    }

    std::string deterministicUuid(const std::string& identityKey) {
        // Namespace-prefixed so a module UUID can never collide with an
        // unrelated hash of the same string.
        std::string digest = sha256Hex("urn:bible-module:v0.1:" + identityKey);
        std::string u = digest.substr(0, 32);

        // RFC 9562 version 8 (custom / hash-based).
        u[12] = '8';

        // RFC 9562 variant bits: the first hex digit of the 4th group must be
        // 8, 9, a or b.
        char v = u[16];
        int nibble = (v >= '0' && v <= '9') ? (v - '0') : (v - 'a' + 10);
        static const char VARIANT[4] = {'8', '9', 'a', 'b'};
        u[16] = VARIANT[nibble & 0x3];

        return u.substr(0, 8) + "-" + u.substr(8, 4) + "-" + u.substr(12, 4) + "-"
             + u.substr(16, 4) + "-" + u.substr(20, 12);
    }

    std::string stripRtfArtifacts(const std::string& text) {
        if (text.empty()) return text;

        std::string result;
        result.reserve(text.size());

        size_t i = 0;
        while (i < text.size()) {
            if (text[i] == '\\') {
                // Escaped literal: \\ \{ \}
                if (i + 1 < text.size() && (text[i + 1] == '\\' || text[i + 1] == '{' || text[i + 1] == '}')) {
                    result += text[i + 1];
                    i += 2;
                    continue;
                }
                // Control word: \word[-]?[digits]? optionally followed by one space
                size_t j = i + 1;
                size_t wordStart = j;
                while (j < text.size() && std::isalpha(static_cast<unsigned char>(text[j]))) j++;
                if (j > wordStart) {
                    std::string word = text.substr(wordStart, j - wordStart);
                    if (j < text.size() && text[j] == '-') j++;
                    while (j < text.size() && std::isdigit(static_cast<unsigned char>(text[j]))) j++;
                    if (j < text.size() && text[j] == ' ') j++;   // delimiter space is consumed
                    // Paragraph/line breaks become a space so words don't fuse.
                    if (word == "par" || word == "line" || word == "pard" || word == "tab") {
                        result += ' ';
                    }
                    i = j;
                    continue;
                }
                // Lone backslash — drop it.
                i++;
                continue;
            }
            // HTML that some .conf files carry in About= (<p>, <br/>): a tag is
            // a word boundary, never text.
            if (text[i] == '<') {
                const size_t close = text.find('>', i);
                if (close != std::string::npos && close - i < 200 && i + 1 < text.size()
                    && (std::isalpha(static_cast<unsigned char>(text[i + 1]))
                        || text[i + 1] == '/' || text[i + 1] == '!')) {
                    result += ' ';
                    i = close + 1;
                    continue;
                }
            }
            if (text[i] == '{' || text[i] == '}') { i++; continue; }
            result += text[i];
            i++;
        }
        for (const auto& entity : std::vector<std::pair<std::string, std::string>>{
                 {"&nbsp;", " "}, {"&quot;", "\""}, {"&apos;", "'"}, {"&#39;", "'"},
                 {"&lt;", "<"}, {"&gt;", ">"}, {"&amp;", "&"}}) {
            size_t p = 0;
            while ((p = result.find(entity.first, p)) != std::string::npos) {
                result.replace(p, entity.first.size(), entity.second);
                p += entity.second.size();
            }
        }

        // Collapse whitespace and trim.
        std::string clean;
        clean.reserve(result.size());
        bool pendingSpace = false;
        for (char c : result) {
            if (std::isspace(static_cast<unsigned char>(c))) {
                pendingSpace = !clean.empty();
            } else {
                if (pendingSpace) { clean += ' '; pendingSpace = false; }
                clean += c;
            }
        }
        return clean;
    }

    std::string primaryLanguage(const std::string& langTag) {
        std::string out;
        for (char c : trim(langTag)) {
            if (c == '-' || c == '_') break;
            out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        }
        return out;
    }

    bool isRightToLeft(const std::string& langTag, const std::string& direction) {
        const std::string dir = toLower(trim(direction));
        if (dir == "rtol" || dir == "rtl") return true;
        if (dir == "ltor" || dir == "ltr") return false;
        static const char* const RTL_LANGUAGES[] = {
            "he", "hbo", "ar", "arb", "arz", "apc", "acm", "fa", "pes", "prs", "ur", "yi",
            "ps", "sd", "ug", "ckb", "dv", "syr", "arc", "sam",
        };
        const std::string primary = primaryLanguage(langTag);
        for (const char* l : RTL_LANGUAGES) {
            if (primary == l) return true;
        }
        // A right-to-left script subtag ("ku-Arab-IQ", "pa-Arab").
        const std::string lower = toLower(langTag);
        for (const char* script : {"-arab", "-hebr", "-syrc", "-thaa", "-nkoo", "-adlm", "-samr", "-mand"}) {
            const size_t p = lower.find(script);
            const size_t end = p + std::strlen(script);
            if (p != std::string::npos && (end == lower.size() || lower[end] == '-' || lower[end] == '_')) {
                return true;
            }
        }
        return false;
    }

    std::string licenseToSpdx(const std::string& distributionLicense) {
        const std::string l = toLower(trim(distributionLicense));
        if (l.empty()) return "";

        if (l.find("public domain") != std::string::npos) return "PD";
        if (l.find("cc0") != std::string::npos) return "CC0-1.0";

        // "Creative Commons ...", "CC-BY-SA-4.0" and "CC BY 4.0" (with a space,
        // as in THOT's DistributionLicense, which mapped to nothing).
        if (l.find("creative commons") != std::string::npos || l.find("cc-by") != std::string::npos
            || l.find("cc by") != std::string::npos) {
            std::string id = "CC-BY";
            if (l.find("noncommercial") != std::string::npos || l.find("non-commercial") != std::string::npos
                || l.find("-nc") != std::string::npos) {
                id += "-NC";
            }
            // ShareAlike and NoDerivs are mutually exclusive in CC licences.
            if (l.find("noderiv") != std::string::npos || l.find("-nd") != std::string::npos) {
                id += "-ND";
            } else if (l.find("sharealike") != std::string::npos || l.find("share alike") != std::string::npos
                       || l.find("-sa") != std::string::npos) {
                id += "-SA";
            }

            const char* versions[] = {"4.0", "3.0", "2.5", "2.0", "1.0"};
            for (const char* v : versions) {
                if (l.find(v) != std::string::npos) return id + "-" + v;
            }
            return id + "-4.0";
        }

        if (l.find("gpl") != std::string::npos) {
            if (l.find("3") != std::string::npos) return "GPL-3.0-or-later";
            return "GPL-2.0-or-later";
        }

        // SWORD's own vocabulary has no SPDX equivalent; use LicenseRef- ids so
        // the value is still machine-comparable.
        if (l.find("free non-commercial") != std::string::npos
            || l.find("free noncommercial") != std::string::npos) {
            return "LicenseRef-SWORD-FreeNonCommercial";
        }
        if (l.find("permission to distribute") != std::string::npos) {
            return "LicenseRef-SWORD-DistributionPermitted";
        }
        if (l.find("copyright") != std::string::npos) {
            return "LicenseRef-Proprietary";
        }

        return "";
    }

    std::string licenseUrlForSpdx(const std::string& spdxId) {
        if (spdxId.empty()) return "";
        if (spdxId == "PD") return "https://creativecommons.org/publicdomain/mark/1.0/";

        if (spdxId.rfind("CC-", 0) == 0) {
            // "CC-BY-NC-ND-4.0" -> "by-nc-nd" + "4.0"
            const size_t lastDash = spdxId.rfind('-');
            if (lastDash == std::string::npos || lastDash < 3) return "";
            std::string code = toLower(spdxId.substr(3, lastDash - 3));
            std::string version = spdxId.substr(lastDash + 1);
            return "https://creativecommons.org/licenses/" + code + "/" + version + "/";
        }

        if (spdxId == "GPL-3.0-or-later") return "https://www.gnu.org/licenses/gpl-3.0.html";
        if (spdxId == "GPL-2.0-or-later") return "https://www.gnu.org/licenses/old-licenses/gpl-2.0.html";

        return "";
    }

    int extractYear(const std::string& text) {
        for (size_t i = 0; i + 4 <= text.size(); i++) {
            if (!std::isdigit(static_cast<unsigned char>(text[i]))) continue;
            if (i > 0 && std::isdigit(static_cast<unsigned char>(text[i - 1]))) continue;
            bool allDigits = true;
            for (size_t j = i; j < i + 4; j++) {
                if (!std::isdigit(static_cast<unsigned char>(text[j]))) { allDigits = false; break; }
            }
            if (!allDigits) continue;
            if (i + 4 < text.size() && std::isdigit(static_cast<unsigned char>(text[i + 4]))) continue;

            int year = std::stoi(text.substr(i, 4));
            if (year >= 1000 && year <= 2999) return year;
        }
        return 0;
    }

    ModuleIdentity deriveModuleIdentity(const std::map<std::string, std::string>& config,
                                        const std::string& moduleType,
                                        const std::string& moduleName) {
        auto get = [&config](const std::string& key) -> std::string {
            auto it = config.find(key);
            if (it == config.end()) return std::string();
            // .conf values are not reliably UTF-8; module_info must be.
            return isValidUtf8(it->second) ? it->second : latin1ToUtf8(it->second);
        };

        ModuleIdentity id;

        id.abbreviation = get("Abbreviation");
        if (id.abbreviation.empty()) id.abbreviation = moduleName;

        id.fullName = get("Description");
        if (id.fullName.empty()) id.fullName = moduleName;

        id.author = get("Author");
        id.publisher = get("CopyrightHolder");
        if (id.publisher.empty()) id.publisher = get("Publisher");

        // language_code is the bare ISO 639 code. The full
        // SWORD tag (script/region: "zh-Hant", "ku-Arab-IQ") goes to metadata.
        const std::string lang = get("Lang");
        if (!lang.empty()) id.languageCode = primaryLanguage(lang);

        id.contentVersion = get("Version");
        if (id.contentVersion.empty()) id.contentVersion = "1.0";

        // RTF artefacts are stripped rather than shipped.
        id.description = stripRtfArtifacts(get("About"));

        std::string copyrightText = get("ShortCopyright");
        if (copyrightText.empty()) copyrightText = get("Copyright");
        if (copyrightText.empty()) copyrightText = get("CopyrightNotes");
        if (copyrightText.empty()) copyrightText = get("DistributionLicense");
        id.copyright = stripRtfArtifacts(copyrightText);

        const std::string distributionLicense = get("DistributionLicense");
        id.licenseSpdx = licenseToSpdx(distributionLicense);
        id.licenseUrl = licenseUrlForSpdx(id.licenseSpdx);

        const std::string textSource = get("TextSource");
        if (textSource.rfind("http://", 0) == 0 || textSource.rfind("https://", 0) == 0) {
            id.sourceUrl = textSource;
        }

        // CopyrightDate is frequently a range ("1611-2011") or free text, so
        // pull the first plausible four-digit year rather than stoi()ing it.
        id.yearPublished = extractYear(get("CopyrightDate"));
        if (id.yearPublished == 0) id.yearPublished = extractYear(id.copyright);

        // Identity that survives content revisions: never fold Version or the
        // content hash into the UUID.
        id.uuid = deterministicUuid(moduleType + ":" + toLower(id.abbreviation) + ":"
                                    + toLower(id.languageCode));

        std::ostringstream meta;
        meta << "{"
             << "\"sword_module\":\"" << jsonEscape(moduleName) << "\","
             << "\"sword_lang\":\"" << jsonEscape(lang) << "\","
             << "\"sword_source_type\":\"" << jsonEscape(get("SourceType")) << "\","
             << "\"sword_distribution_license\":\"" << jsonEscape(distributionLicense) << "\","
             << "\"sword_text_source\":\"" << jsonEscape(textSource) << "\""
             << "}";
        id.metadataJson = meta.str();

        return id;
    }

    bool isValidUtf8(const std::string& s) {
        size_t i = 0;
        const size_t n = s.size();
        while (i < n) {
            const unsigned char c = static_cast<unsigned char>(s[i]);
            if (c < 0x80) { i++; continue; }
            int len = 0;
            if (c >= 0xC2 && c <= 0xDF) len = 2;
            else if (c >= 0xE0 && c <= 0xEF) len = 3;
            else if (c >= 0xF0 && c <= 0xF4) len = 4;
            else return false;
            if (i + static_cast<size_t>(len) > n) return false;
            for (int k = 1; k < len; k++) {
                if ((static_cast<unsigned char>(s[i + k]) & 0xC0) != 0x80) return false;
            }
            i += static_cast<size_t>(len);
        }
        return true;
    }

    std::string latin1ToUtf8(const std::string& s) {
        // Windows-1252 for 0x80-0x9F; its five undefined slots stay C1 controls.
        static const unsigned CP1252[32] = {
            0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
            0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
            0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
            0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178,
        };
        std::string out;
        out.reserve(s.size() + s.size() / 8);
        for (unsigned char c : s) {
            unsigned code = c;
            if (c >= 0x80 && c <= 0x9F) code = CP1252[c - 0x80];
            if (code < 0x80) {
                out += static_cast<char>(code);
            } else if (code < 0x800) {
                out += static_cast<char>(0xC0 | (code >> 6));
                out += static_cast<char>(0x80 | (code & 0x3F));
            } else {
                out += static_cast<char>(0xE0 | (code >> 12));
                out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
                out += static_cast<char>(0x80 | (code & 0x3F));
            }
        }
        return out;
    }

    std::string repairUtf8(const std::string& s) {
        std::string out;
        out.reserve(s.size() + 8);
        size_t i = 0;
        const size_t n = s.size();
        while (i < n) {
            const unsigned char c = static_cast<unsigned char>(s[i]);
            int len = 0;
            if (c < 0x80) len = 1;
            else if (c >= 0xC2 && c <= 0xDF) len = 2;
            else if (c >= 0xE0 && c <= 0xEF) len = 3;
            else if (c >= 0xF0 && c <= 0xF4) len = 4;
            bool ok = len > 0 && i + static_cast<size_t>(len) <= n;
            for (int k = 1; ok && k < len; k++) {
                ok = (static_cast<unsigned char>(s[i + k]) & 0xC0) == 0x80;
            }
            if (ok) {
                out.append(s, i, static_cast<size_t>(len));
                i += static_cast<size_t>(len);
            } else {
                out += latin1ToUtf8(std::string(1, s[i]));
                i++;
            }
        }
        return out;
    }

    std::string jsonEscape(const std::string& str) {
        std::string result;
        result.reserve(str.size() + 8);
        for (unsigned char c : str) {
            switch (c) {
                case '"':  result += "\\\""; break;
                case '\\': result += "\\\\"; break;
                case '\n': result += "\\n"; break;
                case '\r': result += "\\r"; break;
                case '\t': result += "\\t"; break;
                case '\b': result += "\\b"; break;
                case '\f': result += "\\f"; break;
                default:
                    if (c < 0x20) {
                        static const char* HEX = "0123456789abcdef";
                        result += "\\u00";
                        result += HEX[(c >> 4) & 0xF];
                        result += HEX[c & 0xF];
                    } else {
                        result += static_cast<char>(c);
                    }
            }
        }
        return result;
    }

    // ==================================================================
    // Unified verse linking
    // ==================================================================

    // NOTE: hand-maintained copy of the Bible repo's schemas (see the note on
    // MODULE_INFO_SCHEMA_SQL in sword_common.h). Load them instead.
    const char* const MODULE_INFO_SCHEMA_SQL = R"SQL(
        CREATE TABLE module_info (
            info_id INTEGER PRIMARY KEY CHECK (info_id = 1),
            module_uuid TEXT NOT NULL,
            module_type TEXT NOT NULL,
            abbreviation TEXT NOT NULL,
            full_name TEXT NOT NULL,
            format TEXT NOT NULL,
            format_version TEXT NOT NULL DEFAULT '0.1',
            content_version TEXT,
            content_sha256 TEXT,
            author TEXT,
            publisher TEXT,
            year_published INTEGER,
            description TEXT,
            language_code TEXT NOT NULL DEFAULT 'en',
            is_original_language INTEGER NOT NULL DEFAULT 0,
            right_to_left INTEGER NOT NULL DEFAULT 0,
            copyright TEXT,
            license_spdx TEXT,
            license_url TEXT,
            source_url TEXT,
            versification TEXT NOT NULL DEFAULT 'kjv-english',
            created_date TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            CHECK (is_original_language IN (0, 1)),
            CHECK (right_to_left IN (0, 1))
        );
    )SQL";

    const char* const VERSE_LINK_SCHEMA_SQL = R"SQL(
        -- Unified content -> verse linking.
        -- Range convention: verse_id_start inclusive, verse_id_end inclusive
        -- and NOT NULL. A single verse is verse_id_end = verse_id_start, never NULL,
        -- so a containment probe is uniformly
        --   verse_id_start <= X AND verse_id_end >= X
        -- with no NULL arm for a consumer to forget.
        CREATE TABLE verse_link (
            link_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type     TEXT NOT NULL,
            source_id       INTEGER NOT NULL,
            verse_id_start  INTEGER NOT NULL,
            verse_id_end    INTEGER NOT NULL,
            link_type       TEXT NOT NULL DEFAULT 'reference',
            sort_order      INTEGER NOT NULL DEFAULT 0,
            context         TEXT,
            metadata        TEXT
        );

        CREATE INDEX idx_verse_link_source ON verse_link(source_type, source_id, sort_order);
        CREATE INDEX idx_verse_link_start  ON verse_link(verse_id_start);
        CREATE INDEX idx_verse_link_range  ON verse_link(verse_id_start, verse_id_end);
        -- The reverse pair, so containment can be driven from either side.
        CREATE INDEX idx_verse_link_covering ON verse_link(verse_id_end, verse_id_start);
    )SQL";

    bool insertVerseLink(void* dbPtr,
                         const std::string& sourceType,
                         int64_t sourceId,
                         int64_t verseIdStart,
                         int64_t verseIdEnd,
                         const std::string& linkType,
                         int sortOrder,
                         const std::string& context) {
        sqlite3* db = static_cast<sqlite3*>(dbPtr);

        const char* sql = R"SQL(
            INSERT INTO verse_link (
                source_type, source_id, verse_id_start, verse_id_end,
                link_type, sort_order, context
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        )SQL";

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            return false;
        }

        sqlite3_bind_text(stmt, 1, sourceType.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int64(stmt, 2, sourceId);
        sqlite3_bind_int64(stmt, 3, verseIdStart);

        // The end is always materialised. Callers pass 0 for "single verse",
        // which is stored as end = start rather than NULL.
        sqlite3_bind_int64(stmt, 4, verseIdEnd > 0 ? verseIdEnd : verseIdStart);

        sqlite3_bind_text(stmt, 5, linkType.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int(stmt, 6, sortOrder);

        if (context.empty()) {
            sqlite3_bind_null(stmt, 7);
        } else {
            sqlite3_bind_text(stmt, 7, context.c_str(), -1, SQLITE_TRANSIENT);
        }

        bool success = (sqlite3_step(stmt) == SQLITE_DONE);
        sqlite3_finalize(stmt);
        return success;
    }

    // ==================================================================
    // HTML helpers
    // ==================================================================

    /**
     * Parse HTML/OSIS markup and extract formatting metadata
     * WORD-BASED OFFSET VERSION
     */
    HtmlParseResult parseHtmlMarkup(const std::string& html) {
        HtmlParseResult result;
        result.cleanText.reserve(html.size());

        // Stack to track active tags and their start word indices
        struct ActiveTag {
            std::string type;    // "red", "italic", "bold", "underline"
            int startWordIdx;    // Word index where tag started
        };
        std::vector<ActiveTag> tagStack;

        // Word tracking state
        int wordCount = 0;      // Number of complete words seen
        bool inWord = false;    // Currently inside a word
        std::string currentWord; // Current word being built

        auto completeWord = [&]() {
            if (!currentWord.empty()) {
                result.cleanText += currentWord;
                currentWord.clear();
                wordCount++;
                inWord = false;
            }
        };

        size_t i = 0;
        while (i < html.size()) {
            if (html[i] == '<') {
                // Complete any current word before processing tag
                if (inWord) {
                    completeWord();
                }

                // Find end of tag
                size_t tagEnd = html.find('>', i);
                if (tagEnd == std::string::npos) {
                    // Malformed HTML - treat as regular character
                    currentWord += '<';
                    inWord = true;
                    i++;
                    continue;
                }

                std::string tag = html.substr(i, tagEnd - i + 1);
                std::string tagLower = toLower(tag);

                // Determine tag type
                bool isClosingTag = (tag.find("</") == 0);
                std::string tagType;

                if (tagLower.find("<font") != std::string::npos &&
                    tagLower.find("color") != std::string::npos &&
                    tagLower.find("red") != std::string::npos) {
                    tagType = "red";
                } else if (tagLower.find("<q") != std::string::npos &&
                    tagLower.find("who=") != std::string::npos &&
                    tagLower.find("jesus") != std::string::npos) {
                    tagType = "red";
                } else if (tagLower.find("<i>") != std::string::npos || tagLower.find("<i ") != std::string::npos) {
                    tagType = "italic";
                } else if (tagLower.find("<b>") != std::string::npos || tagLower.find("<b ") != std::string::npos) {
                    tagType = "bold";
                } else if (tagLower.find("<u>") != std::string::npos || tagLower.find("<u ") != std::string::npos) {
                    tagType = "underline";
                } else if (tagLower.find("</font") != std::string::npos) {
                    tagType = "red";
                    isClosingTag = true;
                } else if (tagLower.find("</q>") != std::string::npos || tagLower.find("</q ") != std::string::npos) {
                    tagType = "red";
                    isClosingTag = true;
                } else if (tagLower.find("</i>") != std::string::npos) {
                    tagType = "italic";
                } else if (tagLower.find("</b>") != std::string::npos) {
                    tagType = "bold";
                } else if (tagLower.find("</u>") != std::string::npos) {
                    tagType = "underline";
                }

                if (!tagType.empty()) {
                    if (isClosingTag) {
                        // Find matching opening tag on stack
                        for (auto it = tagStack.rbegin(); it != tagStack.rend(); ++it) {
                            if (it->type == tagType) {
                                // Record the word range
                                TextRange range;
                                range.start = it->startWordIdx;
                                range.end = wordCount - 1;  // Last complete word (inclusive)

                                if (range.end >= range.start) {
                                    if (tagType == "red") {
                                        result.wordsOfChrist.push_back(range);
                                    } else if (tagType == "italic") {
                                        result.addedWords.push_back(range);
                                    } else if (tagType == "bold") {
                                        result.bold.push_back(range);
                                    } else if (tagType == "underline") {
                                        result.underline.push_back(range);
                                    }
                                }

                                // Remove from stack
                                tagStack.erase((it + 1).base());
                                break;
                            }
                        }
                    } else {
                        // Opening tag - record current word index
                        ActiveTag activeTag;
                        activeTag.type = tagType;
                        activeTag.startWordIdx = wordCount;  // Next word will be at this index
                        tagStack.push_back(activeTag);
                    }
                }

                // Skip past the tag
                i = tagEnd + 1;
            }
            else if (html[i] == '&') {
                // Handle HTML entities
                size_t entityEnd = html.find(';', i);
                if (entityEnd != std::string::npos && entityEnd - i < 10) {
                    std::string entity = html.substr(i, entityEnd - i + 1);
                    std::string entityLower = toLower(entity);
                    char decodedChar = 0;

                    if (entityLower == "&nbsp;") {
                        decodedChar = ' ';
                    } else if (entityLower == "&lt;") {
                        decodedChar = '<';
                    } else if (entityLower == "&gt;") {
                        decodedChar = '>';
                    } else if (entityLower == "&amp;") {
                        decodedChar = '&';
                    } else if (entityLower == "&quot;") {
                        decodedChar = '"';
                    } else if (entityLower == "&apos;") {
                        decodedChar = '\'';
                    }

                    if (decodedChar != 0) {
                        if (std::isspace(static_cast<unsigned char>(decodedChar))) {
                            if (inWord) {
                                completeWord();
                            }
                            result.cleanText += decodedChar;
                        } else {
                            currentWord += decodedChar;
                            inWord = true;
                        }
                        i = entityEnd + 1;
                    } else {
                        // Unknown entity - treat as part of word
                        currentWord += entity;
                        inWord = true;
                        i = entityEnd + 1;
                    }
                } else {
                    // Not a valid entity
                    currentWord += '&';
                    inWord = true;
                    i++;
                }
            }
            else if (std::isspace(static_cast<unsigned char>(html[i]))) {
                // Whitespace - complete current word if any
                if (inWord) {
                    completeWord();
                }
                result.cleanText += html[i];
                i++;
            }
            else {
                // Regular character - add to current word
                currentWord += html[i];
                inWord = true;
                i++;
            }
        }

        // Complete any remaining word
        if (inWord) {
            completeWord();
        }

        return result;
    }

    /**
     * Strip HTML/OSIS markup from text (simple version)
     */
    std::string stripMarkup(const std::string& text) {
        std::string result = text;

        // Remove HTML/XML tags
        std::regex tagRegex("<[^>]*>");
        result = std::regex_replace(result, tagRegex, "");

        // Decode common HTML entities
        std::regex nbsp("&nbsp;");
        result = std::regex_replace(result, nbsp, " ");

        std::regex lt("&lt;");
        result = std::regex_replace(result, lt, "<");

        std::regex gt("&gt;");
        result = std::regex_replace(result, gt, ">");

        std::regex amp("&amp;");
        result = std::regex_replace(result, amp, "&");

        std::regex quot("&quot;");
        result = std::regex_replace(result, quot, "\"");

        return result;
    }

    std::string stripMarkupClean(const std::string& text) {
        std::string stripped = stripMarkup(text);

        // Drop pilcrows — they are presentation, not content.
        std::string withoutPilcrow;
        withoutPilcrow.reserve(stripped.size());
        for (size_t i = 0; i < stripped.size(); i++) {
            if (static_cast<unsigned char>(stripped[i]) == 0xC2 && i + 1 < stripped.size()
                && static_cast<unsigned char>(stripped[i + 1]) == 0xB6) {
                i++;   // skip the two-byte pilcrow
                continue;
            }
            withoutPilcrow += stripped[i];
        }

        // Collapse whitespace runs to single spaces and trim.
        std::string clean;
        clean.reserve(withoutPilcrow.size());
        bool pendingSpace = false;
        for (char c : withoutPilcrow) {
            if (std::isspace(static_cast<unsigned char>(c))) {
                pendingSpace = !clean.empty();
            } else {
                if (pendingSpace) { clean += ' '; pendingSpace = false; }
                clean += c;
            }
        }
        return clean;
    }

    /**
     * Count words in text
     */
    int countWords(const std::string& text) {
        std::istringstream stream(text);
        return std::distance(std::istream_iterator<std::string>(stream),
                            std::istream_iterator<std::string>());
    }

    /**
     * Calculate verse ID from book, chapter, verse
     */
    int64_t calculateVerseId(int book, int chapter, int verse) {
        return (static_cast<int64_t>(book) * 1000000) + (chapter * 1000) + verse;
    }

    /**
     * Parse verse ID into book, chapter, verse
     */
    bool parseVerseId(int64_t verseId, int& book, int& chapter, int& verse) {
        if (verseId < 1000000 || verseId > 66999999) {
            return false; // Invalid verse ID
        }

        book = verseId / 1000000;
        int remainder = verseId % 1000000;
        chapter = remainder / 1000;
        verse = remainder % 1000;

        return true;
    }

    /**
     * Check if a file or directory exists
     */
    bool fileExists(const fs::path& path) {
        return fs::exists(path);
    }

    /**
     * Trim whitespace from string
     */
    std::string trim(const std::string& str) {
        size_t start = str.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) return "";

        size_t end = str.find_last_not_of(" \t\r\n");
        return str.substr(start, end - start + 1);
    }

    /**
     * Convert string to lowercase
     */
    std::string toLower(const std::string& str) {
        std::string result = str;
        std::transform(result.begin(), result.end(), result.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        return result;
    }

    /**
     * Check if module data path exists
     */
    bool moduleDataExists(const fs::path& modulePath, const std::string& dataPath) {
        fs::path moduleDataPath = modulePath / dataPath;

        // DataPath might be a file base (e.g., "dict") not a directory
        // Check multiple possibilities
        return fs::exists(moduleDataPath) ||
               fs::exists(moduleDataPath.parent_path()) ||
               fs::exists(std::string(moduleDataPath) + ".dat") ||
               fs::exists(std::string(moduleDataPath) + ".idx") ||
               fs::exists(std::string(moduleDataPath) + ".bzs") ||
               fs::exists(std::string(moduleDataPath) + ".bzv");
    }

} // namespace SwordCommon
