/**
 * schema_bridge.h
 *
 * Closes the gap this repo's own README names: "the C++ converters keep
 * hand-maintained copies of module_info, verse_link and each type's content
 * tables ... and will drift again. The converters should load the Bible
 * repo's schemas directly ... as scripts/lib/schema.js does for the Node
 * importers." (task 0035 / design §6.1, "F10 ... load the schemas through
 * schema.js instead of the hand-copied DDL".)
 *
 * Rather than reimplementing schema.js's `-- @include` expansion in C++
 * (a second parser that could itself drift from the first), loadRepoSchema()
 * shells out to `node scripts/lib/schema.js <file>` — the exact same
 * `loadSchema()` the Node importers (import-tsk.js) already use — and
 * executes whatever comes back. One expanded schema file (e.g. BibleTranslation.sql)
 * already includes module_info.sql, compression_dictionary.sql,
 * module_feature.sql and verse_link.sql via its own `@include` lines, so one
 * call replaces a converter's entire hand-written schema block.
 */

#ifndef SWORD_COMMON_SCHEMA_BRIDGE_H
#define SWORD_COMMON_SCHEMA_BRIDGE_H

#include <string>

namespace SwordCommon {

    /**
     * Fetch the expanded DDL for `schemaFileName` (e.g. "BibleTranslation.sql",
     * "Commentary.sql" — a name under the Bible repo's
     * packages/core/sql/schemas/initial/, exactly as scripts/lib/schema.js's
     * loadSchema() expects it).
     *
     * Resolves scripts/lib/schema.js relative to this executable's own path
     * (tools/import/sword/<type>/sword2<type> -> repo root, via
     * /proc/self/exe — Linux only, matching this whole pipeline's platform
     * requirement), or from `schemaJsPathOverride` / the SWORD_SCHEMA_JS
     * environment variable when set (checked in that order), which is also
     * how a test points this at a fixture instead of a real checkout.
     * BIBLE_REPO itself is read by schema.js, not by this function — the
     * child process inherits the parent's environment, so setting BIBLE_REPO
     * before running a converter is all that's needed, same as for the Node
     * importers.
     *
     * @throws std::runtime_error with schema.js's own stderr message on
     *   failure (unresolvable schema.js, missing BIBLE_REPO, missing schema
     *   file, or a circular @include — schema.js reports all of these).
     */
    std::string loadRepoSchema(const std::string& schemaFileName,
                                const std::string& schemaJsPathOverride = "");

} // namespace SwordCommon

#endif // SWORD_COMMON_SCHEMA_BRIDGE_H
