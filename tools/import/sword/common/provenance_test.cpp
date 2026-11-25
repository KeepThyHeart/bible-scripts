// Unit test for SwordCommon::buildProvenanceJson and deriveModuleIdentity provenance.
#include "sword_common.h"
#include <iostream>

static int failures = 0;
static void expectContains(const std::string& hay, const std::string& needle, const char* what) {
    if (hay.find(needle) == std::string::npos) {
        std::cerr << "FAIL " << what << ": missing " << needle << "\n  in " << hay << "\n";
        failures++;
    }
}

int main() {
    using namespace SwordCommon;
    SourceRepository cw;
    std::string j = buildProvenanceJson("KJV", "3.1", cw);
    expectContains(j, "\"converted_from\":\"sword\"", "cw");
    expectContains(j, "\"publisher\":\"CrossWire Bible Society\"", "cw");
    expectContains(j, "\"publisher_url\":\"https://crosswire.org/sword/\"", "cw");
    expectContains(j, "\"module\":\"KJV\",\"module_version\":\"3.1\"", "cw");
    expectContains(j, "\"attribution\":\"Converted from the SWORD module \\\"KJV\\\" (version 3.1), "
                      "distributed by the CrossWire Bible Society (crosswire.org).\"", "cw");

    SourceRepository xi;
    xi.name = "Xiphos";
    xi.url = "https://www.xiphos.org/modules/";
    j = buildProvenanceJson("Gill", "1.0", xi);
    expectContains(j, "\"publisher\":\"Xiphos\"", "xiphos");
    expectContains(j, "distributed through Xiphos (xiphos.org).", "xiphos");

    // deriveModuleIdentity embeds provenance and keeps the sword_* keys.
    std::map<std::string, std::string> conf{{"Version", "2.0"}, {"Lang", "en"}, {"DistributionLicense", "Public Domain"}};
    ModuleIdentity id = deriveModuleIdentity(conf, "commentary", "TestMod");
    expectContains(id.metadataJson, "\"provenance\":{\"converted_from\":\"sword\"", "identity");
    expectContains(id.metadataJson, "(version 2.0), distributed by the CrossWire Bible Society", "identity");
    expectContains(id.metadataJson, "\"sword_module\":\"TestMod\"", "identity");

    // CLI flags.
    const char* argvv[] = {"prog", "--source-repo", "eBible", "--source-repo-url", "https://ebible.org/sword/"};
    int i = 1;
    char** av = const_cast<char**>(argvv);
    if (!parseSourceRepoArg(5, av, i) || i != 2) { std::cerr << "FAIL flag name\n"; failures++; }
    i = 3;
    if (!parseSourceRepoArg(5, av, i) || i != 4) { std::cerr << "FAIL flag url\n"; failures++; }
    expectContains(buildProvenanceJson("X", "1", sourceRepository()), "distributed through eBible (ebible.org)", "flags");

    if (failures == 0) std::cout << "provenance tests passed\n";
    return failures ? 1 : 0;
}
