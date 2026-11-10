// Unit tests for parseOsisVerse / buildFormattingJson: poetry lines, Selah and
// stray-tag clean-up. Build and run with `make test`.
#include "sword_common.h"

#include <iostream>
#include <string>

static int failures = 0;

static void expectEq(const std::string& what, const std::string& got, const std::string& want) {
    if (got != want) {
        std::cerr << "FAIL " << what << "\n  got:  " << got << "\n  want: " << want << std::endl;
        failures++;
    }
}

static std::string fmt(const std::string& osis, std::string* text = nullptr) {
    const SwordCommon::OsisVerseResult r = SwordCommon::parseOsisVerse(osis);
    if (text) *text = r.text;
    return SwordCommon::buildFormattingJson(r);
}

int main() {
    std::string text;

    // Prose has no block at all.
    expectEq("prose", fmt("In the beginning God created.", &text), "");

    // Plain <lg>/<l> poetry: lines cover every word and carry their level.
    expectEq("two lines",
        fmt("<lg><l level=\"1\">The LORD is my shepherd;</l><l level=\"2\">I shall not want.</l></lg>", &text),
        "{\"v\":1,\"block\":{\"lines\":[{\"start\":0,\"end\":4,\"level\":1},{\"start\":5,\"end\":8,\"level\":2}]}}");
    expectEq("two lines text", text, "The LORD is my shepherd; I shall not want.");

    // Milestone-style lines (eBible): sID opens, eID closes.
    expectEq("milestone lines",
        fmt("<lg sID=\"a\"/><l level=\"1\" sID=\"b\"/>Blessed is the man<l level=\"1\" eID=\"b\"/>"
            "<l level=\"2\" sID=\"c\"/>who walks not<l level=\"2\" eID=\"c\"/><lg eID=\"a\"/>"),
        "{\"v\":1,\"block\":{\"lines\":[{\"start\":0,\"end\":3,\"level\":1},{\"start\":4,\"end\":6,\"level\":2}]}}");

    // A verse whose only line marker is the end milestone of a line that began
    // in the previous verse is that line's continuation.
    expectEq("continuation of a line",
        fmt("by the rod of his wrath. <l eID=\"x\" level=\"1\"/>"),
        "{\"v\":1,\"block\":{\"lines\":[{\"start\":0,\"end\":5,\"level\":1}]}}");

    // Selah inside a line is a musical_direction span, not a block flag.
    expectEq("selah seg",
        fmt("<l level=\"1\">There is no help for him in God. <seg type=\"x-selah\">Selah</seg></l>"),
        "{\"v\":1,\"block\":{\"lines\":[{\"start\":0,\"end\":8,\"level\":1}]},"
        "\"spans\":[{\"type\":\"musical_direction\",\"start\":8,\"end\":8}]}");

    // An <l type="selah"> line is the direction and nothing else.
    expectEq("selah line",
        fmt("<l level=\"1\">Rise up, O LORD.</l><l level=\"1\" type=\"selah\">Selah</l>"),
        "{\"v\":1,\"block\":{\"lines\":[{\"start\":0,\"end\":3,\"level\":1},{\"start\":4,\"end\":4,\"level\":1}]},"
        "\"spans\":[{\"type\":\"musical_direction\",\"start\":4,\"end\":4}]}");

    // Never a poetry_level or selah key.
    expectEq("no legacy keys",
        std::to_string(fmt("<l level=\"3\">x y</l>").find("poetry_level") == std::string::npos), "1");

    // Tag-shaped debris from escaped text is removed; offsets stay consistent.
    fmt("a b&lt;WAHb&gt;c <w lemma=\"strong:H1\">d</w>", &text);
    expectEq("stray tag removed", text, "a bc d");
    fmt("1 &lt; 2 and 3 &gt; 2", &text);
    expectEq("comparison kept", text, "1 < 2 and 3 > 2");

    if (failures == 0) std::cout << "all parseOsisVerse tests passed" << std::endl;
    return failures == 0 ? 0 : 1;
}
