/**
 * sword-probe - read a SWORD module through libsword and print what it
 * contains, independently of the converters.
 *
 * The verification tooling (tools/import/sword/verify/swordcheck.py) compares
 * this output against the converted database. It deliberately does NOT reuse
 * the converters' OSIS parser: the "plain" text comes from libsword's own
 * strip filters, and red letter comes from libsword's own HTML renderer, so a
 * bug in parseOsisVerse() shows up as a disagreement instead of being copied
 * into both sides of the comparison.
 *
 * Usage:
 *   sword-probe --input <module.zip|dir> [--module NAME] info
 *   sword-probe --input <module.zip|dir> [--module NAME] dump
 *   sword-probe --input <module.zip|dir> [--module NAME] verse <ref> [<ref> ...]
 *
 *   info   JSON: the module's .conf entries, its versification system and the
 *          books that system defines.
 *   dump   JSON Lines, one object per entry that has content:
 *            {"i":n,"key":"Genesis 1:1","osis":"Gen.1.1","book":"Gen","c":1,
 *             "v":1,"raw":"...","plain":"...","red":false,"linked":false}
 *          Non-verse-keyed modules (lexicons, general books) emit only
 *          i/key/raw/plain.
 *   verse  Human-readable raw / rendered HTML / plain for each reference,
 *          parsed in the module's own versification ("John 3:16", "Ps.23.1").
 *
 * raw    the native entry bytes (OSIS/GBF/ThML/TEI/plain). Invalid UTF-8
 *        (Latin-1 modules) is escaped byte-for-byte as \u00XX and flagged with
 *        "raw_latin1":true.
 * plain  libsword's stripText() with footnotes, cross-references, headings,
 *        Strong's and morphology switched off: the verse body only.
 * red    libsword's HTML render (Words of Christ in Red = On) contains a red
 *        font span.
 * linked this entry is a link to the previous key (SWORD stores one text for
 *        a verse range, e.g. "Rom 16:25-27" in some modules).
 */

#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <cstring>
#include <filesystem>

#include <swmgr.h>
#include <swmodule.h>
#include <swkey.h>
#include <versekey.h>
#include <versificationmgr.h>
#include <markupfiltmgr.h>

#include "sword_common.h"

namespace fs = std::filesystem;
using namespace sword;

namespace {

/** JSON string escaping that survives non-UTF-8 input (Latin-1 modules). */
std::string jsonString(const std::string& s, bool* hadInvalidUtf8 = nullptr) {
    std::string out;
    out.reserve(s.size() + 16);
    out += '"';
    const size_t n = s.size();
    size_t i = 0;
    auto hex4 = [&](unsigned code) {
        static const char* digits = "0123456789abcdef";
        out += "\\u";
        out += digits[(code >> 12) & 0xF];
        out += digits[(code >> 8) & 0xF];
        out += digits[(code >> 4) & 0xF];
        out += digits[code & 0xF];
    };
    while (i < n) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c == '"')  { out += "\\\""; i++; continue; }
        if (c == '\\') { out += "\\\\"; i++; continue; }
        if (c == '\n') { out += "\\n"; i++; continue; }
        if (c == '\r') { out += "\\r"; i++; continue; }
        if (c == '\t') { out += "\\t"; i++; continue; }
        if (c < 0x20)  { hex4(c); i++; continue; }
        if (c < 0x80)  { out += static_cast<char>(c); i++; continue; }

        // Validate one UTF-8 sequence; copy it through when well formed.
        int len = 0;
        if ((c & 0xE0) == 0xC0 && c >= 0xC2) len = 2;
        else if ((c & 0xF0) == 0xE0) len = 3;
        else if ((c & 0xF8) == 0xF0 && c <= 0xF4) len = 4;
        bool ok = len > 0 && i + static_cast<size_t>(len) <= n;
        for (int k = 1; ok && k < len; k++) {
            ok = (static_cast<unsigned char>(s[i + k]) & 0xC0) == 0x80;
        }
        if (ok) {
            out.append(s, i, static_cast<size_t>(len));
            i += static_cast<size_t>(len);
        } else {
            // Treat the byte as Latin-1, which is what SWORD assumes for
            // modules without Encoding=UTF-8.
            if (hadInvalidUtf8) *hadInvalidUtf8 = true;
            hex4(c);
            i++;
        }
    }
    out += '"';
    return out;
}

std::string lower(std::string s) {
    for (auto& ch : s) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return s;
}

bool hasRedFont(const std::string& html) {
    const std::string l = lower(html);
    return l.find("color=\"red\"") != std::string::npos
        || l.find("color='red'") != std::string::npos
        || l.find("color=red") != std::string::npos
        || l.find("color:red") != std::string::npos
        || l.find("color: red") != std::string::npos
        || l.find("#ff0000") != std::string::npos
        || l.find("wordsofjesus") != std::string::npos;
}

void usage(const char* prog) {
    std::cerr << "Usage: " << prog << " --input <module.zip|dir> [--module NAME] info|dump|verse <ref>...\n";
}

/**
 * Switch off everything that is not verse body text, so stripText() yields
 * what the converted `bible_verse.text` is meant to hold. Accents, vowel points
 * and cantillation are switched ON: the converter keeps them, so stripping
 * them here would create false character differences.
 */
void setProbeOptions(SWMgr* mgr) {
    const char* off[] = {
        "Footnotes", "Cross-references", "Strong's Numbers", "Morphological Tags",
        "Headings", "Lemmas", "Morpheme Segmentation", "Transliterated Forms",
        "Enumerations", "Glosses", "Word Javascript", "Hebrew Morphological Tags",
    };
    const char* on[] = {
        "Words of Christ in Red", "Hebrew Vowel Points", "Hebrew Cantillation",
        "Greek Accents", "Arabic Vowel Points", "Greek Breathing Marks",
    };
    for (const char* o : off) mgr->setGlobalOption(o, "Off");
    for (const char* o : on) mgr->setGlobalOption(o, "On");
    mgr->setGlobalOption("Textual Variants", "Primary Reading");
}

}  // namespace

int main(int argc, char* argv[]) {
    std::string input;
    std::string moduleName;
    std::string command;
    std::vector<std::string> refs;

    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        if ((a == "--input" || a == "-i") && i + 1 < argc) input = argv[++i];
        else if ((a == "--module" || a == "-m") && i + 1 < argc) moduleName = argv[++i];
        else if (a == "--help" || a == "-h") { usage(argv[0]); return 0; }
        else if (command.empty()) command = a;
        else refs.push_back(a);
    }
    if (input.empty() || command.empty()) { usage(argv[0]); return 2; }

    fs::path root;
    fs::path tempDir;
    if (fs::is_directory(input)) {
        root = input;
    } else {
        tempDir = SwordCommon::createTempDirectory("sword_probe_");
        if (!SwordCommon::extractZipFile(input, tempDir)) {
            std::cerr << "Failed to extract " << input << "\n";
            return 1;
        }
        root = tempDir;
    }

    int rc = 0;
    {
        SWMgr mgr(root.string().c_str(), true, new MarkupFilterMgr(FMT_HTMLHREF));
        setProbeOptions(&mgr);

        SWModule* mod = nullptr;
        for (auto it = mgr.Modules.begin(); it != mgr.Modules.end(); ++it) {
            SWModule* m = it->second;
            if (!moduleName.empty()) {
                if (lower(m->getName()) == lower(moduleName)) { mod = m; break; }
                continue;
            }
            const char* dp = m->getConfigEntry("DataPath");
            if (SwordCommon::moduleDataExists(root, dp ? dp : "")) { mod = m; break; }
        }
        if (!mod) {
            std::cerr << "No module found in " << input << "\n";
            rc = 1;
        } else if (command == "info") {
            std::ostringstream o;
            o << "{\"name\":" << jsonString(mod->getName())
              << ",\"type\":" << jsonString(mod->getType() ? mod->getType() : "")
              << ",\"description\":" << jsonString(mod->getDescription() ? mod->getDescription() : "");
            o << ",\"conf\":{";
            bool first = true;
            for (auto sit = mod->getConfig().begin(); sit != mod->getConfig().end(); ++sit) {
                if (!first) o << ",";
                first = false;
                o << jsonString(sit->first.c_str()) << ":" << jsonString(sit->second.c_str());
            }
            o << "}";
            VerseKey* vk = dynamic_cast<VerseKey*>(mod->getKey());
            if (vk) {
                const char* vsys = vk->getVersificationSystem();
                o << ",\"versification\":" << jsonString(vsys ? vsys : "");
                const VersificationMgr::System* sys =
                    VersificationMgr::getSystemVersificationMgr()->getVersificationSystem(vsys);
                o << ",\"system_books\":[";
                if (sys) {
                    for (int b = 0; b < sys->getBookCount(); b++) {
                        const VersificationMgr::Book* bk = sys->getBook(b);
                        if (b) o << ",";
                        o << "{\"osis\":" << jsonString(bk->getOSISName())
                          << ",\"chapters\":" << bk->getChapterMax() << "}";
                    }
                }
                o << "]";
            }
            o << "}";
            std::cout << o.str() << "\n";
        } else if (command == "dump") {
            VerseKey* vk = dynamic_cast<VerseKey*>(mod->getKey());
            long idx = 0;
            if (vk) {
                vk->setIntros(false);
                (*vk) = TOP;
                mod->popError();
                VerseKey prev(*vk);
                std::string prevRaw;
                bool havePrev = false;
                // The same reference in KJV versification, translated by
                // libsword's own mapping tables (identity when the module is
                // KJV, or when libsword has no map for its system).
                const char* vsysName = vk->getVersificationSystem();
                const bool sameSystem = !vsysName || lower(vsysName) == "kjv";
                VersificationMgr* vmgr = VersificationMgr::getSystemVersificationMgr();
                // Same aliases as sword2bible's versificationAlias(): systems
                // identical to a mapped one in the 66 canonical books.
                std::string mapSys = vsysName ? vsysName : "";
                if (lower(mapSys) == "nrsva") mapSys = "NRSV";
                if (lower(mapSys) == "synodalprot") mapSys = "Synodal";
                const VersificationMgr::System* srcSys =
                    sameSystem ? nullptr : vmgr->getVersificationSystem(mapSys.c_str());
                const VersificationMgr::System* kjvSys = vmgr->getVersificationSystem("KJV");
                while (!mod->popError()) {
                    const std::string raw = mod->getRawEntry();
                    if (!raw.empty()) {
                        const SWBuf html = mod->renderText();
                        const std::string plain = mod->stripText();
                        // Same rule as sword2bible: isLinked() plus the same
                        // text as the previous key (the empty entries of a
                        // partial module share an index position).
                        const bool linked = havePrev && raw == prevRaw && mod->isLinked(&prev, vk);
                        bool latin1 = false;
                        std::string kjvRef;
                        // Only for books the mapped system knows: an aliased
                        // system (NRSVA -> NRSV) lacks the deuterocanon, and
                        // translating an unknown book crashes libsword.
                        if (srcSys && kjvSys && srcSys->getBookNumberByOSISName(vk->getOSISBookName()) > 0) {
                            const char* bn = vk->getOSISBookName();
                            int ch = vk->getChapter();
                            int vs = vk->getVerse();
                            int ve = vs;
                            srcSys->translateVerse(kjvSys, &bn, &ch, &vs, &ve);
                            if (bn && *bn) {
                                kjvRef = std::string(bn) + "." + std::to_string(ch) + "." +
                                         std::to_string(vs);
                            }
                        }
                        std::ostringstream o;
                        o << "{\"i\":" << idx
                          << ",\"key\":" << jsonString(vk->getText())
                          << ",\"osis\":" << jsonString(vk->getOSISRef())
                          << ",\"book\":" << jsonString(vk->getOSISBookName())
                          << ",\"c\":" << vk->getChapter()
                          << ",\"v\":" << vk->getVerse();
                        if (!sameSystem) o << ",\"kjv\":" << jsonString(kjvRef);
                        o
                          << ",\"raw\":" << jsonString(raw, &latin1)
                          << ",\"plain\":" << jsonString(plain)
                          << ",\"red\":" << (hasRedFont(html.c_str()) ? "true" : "false")
                          << ",\"linked\":" << (linked ? "true" : "false");
                        if (latin1) o << ",\"raw_latin1\":true";
                        o << "}";
                        std::cout << o.str() << "\n";
                        idx++;
                    }
                    prev = *vk;
                    prevRaw = raw;
                    havePrev = true;
                    (*mod)++;
                }
            } else {
                (*mod) = TOP;
                mod->popError();
                while (!mod->popError()) {
                    const std::string raw = mod->getRawEntry();
                    const std::string plain = mod->stripText();
                    bool latin1 = false;
                    std::ostringstream o;
                    o << "{\"i\":" << idx
                      << ",\"key\":" << jsonString(mod->getKeyText())
                      << ",\"raw\":" << jsonString(raw, &latin1)
                      << ",\"plain\":" << jsonString(plain);
                    if (latin1) o << ",\"raw_latin1\":true";
                    o << "}";
                    std::cout << o.str() << "\n";
                    idx++;
                    (*mod)++;
                }
            }
        } else if (command == "verse") {
            if (refs.empty()) { usage(argv[0]); rc = 2; }
            for (const auto& ref : refs) {
                mod->setKey(ref.c_str());
                const std::string raw = mod->getRawEntry();
                const SWBuf html = mod->renderText();
                const std::string plain = mod->stripText();
                VerseKey* vk = dynamic_cast<VerseKey*>(mod->getKey());
                std::cout << "=== " << mod->getName() << " " << mod->getKeyText();
                if (vk) std::cout << " (" << vk->getOSISRef() << ", " << vk->getVersificationSystem() << ")";
                std::cout << "\n--- raw\n" << raw
                          << "\n--- html (headings/notes off, red letter on)\n" << html.c_str()
                          << "\n--- plain\n" << plain << "\n\n";
            }
        } else {
            usage(argv[0]);
            rc = 2;
        }
    }

    if (!tempDir.empty()) {
        std::error_code ec;
        fs::remove_all(tempDir, ec);
    }
    return rc;
}
