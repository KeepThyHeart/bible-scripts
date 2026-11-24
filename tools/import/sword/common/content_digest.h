/**
 * content_digest.h
 *
 * The canonical content digest (design §2.7): a codec-invariant, reproducible
 * SHA-256 over a module's actual content, stored in module_info.content_sha256.
 *
 * digest = SHA-256 over, for each ContentShape (declared order), for each row
 * ordered by its rowid column ascending:
 *   u64le(rowid) || for each column in (prose ∪ indexed), declared order:
 *     0x00 if NULL, else 0x01 || u64le(byteLength) || utf8(DECODED value)
 *   then 0x1E as a table separator.
 *
 * This is a from-spec reimplementation shared by every converter (mirrored,
 * not imported, by scripts/lib/content-digest.js on the Node side — see that
 * file's doc comment for why: this repo cannot import packages/core/src code
 * from the `bible` repo). Every converter in this repo has exactly one
 * ContentShape (one content table each), so computeContentSha256() below
 * handles that common case end to end, including the SELECT.
 */

#ifndef SWORD_COMMON_CONTENT_DIGEST_H
#define SWORD_COMMON_CONTENT_DIGEST_H

#include <cstdint>
#include <string>
#include <vector>

namespace SwordCommon {

    /**
     * @param db           open sqlite3* (void* to keep sqlite3.h out of this header)
     * @param table        e.g. "commentary_entry"
     * @param rowidColumn  e.g. "entry_id"
     * @param columns      in "prose ∪ indexed" order. For every module type
     *                     this repo converts, `indexed` (design §2.5's
     *                     CONTENT_MAP) is already a superset of `prose`, so
     *                     this is simply `indexed`'s own declared order —
     *                     see content-digest.js for the same reasoning.
     * @param proseColumns which of `columns` may be a compressed BLOB.
     * @param compression  module_info.compression ('none' | 'deflate' | 'zstd').
     * @param dictionary   compression_dictionary.dict_blob for `compression`
     *                     (ignored/empty when compression == 'none').
     * @returns lowercase hex SHA-256, 64 chars.
     */
    std::string computeContentSha256(void* db,
                                      const std::string& table,
                                      const std::string& rowidColumn,
                                      const std::vector<std::string>& columns,
                                      const std::vector<std::string>& proseColumns,
                                      const std::string& compression,
                                      const std::vector<uint8_t>& dictionary);

} // namespace SwordCommon

#endif // SWORD_COMMON_CONTENT_DIGEST_H
