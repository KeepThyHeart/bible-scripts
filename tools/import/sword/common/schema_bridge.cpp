/**
 * schema_bridge.cpp — see schema_bridge.h.
 */

#include "schema_bridge.h"

#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <filesystem>
#include <unistd.h>

namespace fs = std::filesystem;

namespace SwordCommon {

    namespace {
        /** Single-quote for a POSIX shell, escaping embedded single quotes. */
        std::string shellQuote(const std::string& s) {
            std::string out = "'";
            for (char c : s) {
                if (c == '\'') out += "'\\''";
                else out += c;
            }
            out += "'";
            return out;
        }

        fs::path exeDirectory() {
            char buf[4096];
            ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
            if (n <= 0) {
                throw std::runtime_error(
                    "loadRepoSchema: cannot resolve /proc/self/exe to find scripts/lib/schema.js "
                    "(this pipeline is Linux-only; set SWORD_SCHEMA_JS to the full path of "
                    "scripts/lib/schema.js to override)");
            }
            buf[n] = '\0';
            return fs::path(buf).parent_path();
        }

        fs::path resolveSchemaJsPath(const std::string& override) {
            if (!override.empty()) return fs::path(override);
            if (const char* env = std::getenv("SWORD_SCHEMA_JS")) {
                if (env[0] != '\0') return fs::path(env);
            }
            // The binary lives at <repoRoot>/tools/import/sword/<type>/sword2<type>;
            // scripts/lib/schema.js lives at <repoRoot>/scripts/lib/schema.js.
            fs::path repoRoot = exeDirectory().parent_path().parent_path().parent_path().parent_path();
            return repoRoot / "scripts" / "lib" / "schema.js";
        }
    } // namespace

    std::string loadRepoSchema(const std::string& schemaFileName, const std::string& schemaJsPathOverride) {
        fs::path schemaJs = resolveSchemaJsPath(schemaJsPathOverride);
        if (!fs::exists(schemaJs)) {
            throw std::runtime_error(
                "loadRepoSchema: schema.js not found at " + schemaJs.string() +
                " (set SWORD_SCHEMA_JS to override, or check this binary is under "
                "tools/import/sword/<type>/ of a full bible-scripts checkout)");
        }

        std::string cmd = "node " + shellQuote(schemaJs.string()) + " " + shellQuote(schemaFileName) + " 2>&1";
        FILE* pipe = popen(cmd.c_str(), "r");
        if (!pipe) {
            throw std::runtime_error("loadRepoSchema: failed to launch node (" + cmd + ")");
        }

        std::string output;
        char chunk[4096];
        size_t n;
        while ((n = fread(chunk, 1, sizeof(chunk), pipe)) > 0) {
            output.append(chunk, n);
        }
        int status = pclose(pipe);

        if (status != 0) {
            throw std::runtime_error(
                "loadRepoSchema('" + schemaFileName + "') failed (schema.js exited " +
                std::to_string(status) + "):\n" + output);
        }
        return output;
    }

} // namespace SwordCommon
