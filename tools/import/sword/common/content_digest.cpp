/**
 * content_digest.cpp — see content_digest.h.
 */

#include "content_digest.h"
#include "compression.h"
#include "sword_common.h"

#include <sqlite3.h>
#include <algorithm>
#include <stdexcept>

namespace SwordCommon {

    namespace {
        void appendU64LE(std::string& out, uint64_t v) {
            for (int i = 0; i < 8; i++) {
                out += static_cast<char>((v >> (8 * i)) & 0xFF);
            }
        }

        std::string decodeColumn(sqlite3_stmt* stmt, int col, bool isProse,
                                  const std::string& compression,
                                  const std::vector<uint8_t>& dictionary) {
            int type = sqlite3_column_type(stmt, col);
            if (isProse && compression != CODEC_NONE && type == SQLITE_BLOB) {
                const uint8_t* blob = static_cast<const uint8_t*>(sqlite3_column_blob(stmt, col));
                size_t len = static_cast<size_t>(sqlite3_column_bytes(stmt, col));
                if (compression == CODEC_DEFLATE) return deflateDecode(blob, len, dictionary);
                if (compression == CODEC_ZSTD) return zstdDecode(blob, len, dictionary);
                throw std::runtime_error("computeContentSha256: unknown compression '" + compression + "'");
            }
            // Not a compressed prose cell: read as text, whatever the SQLite
            // storage class (matches the accessor's own tolerance — §3.2:
            // "stored uncompressed, any codec").
            const unsigned char* text = sqlite3_column_text(stmt, col);
            int len = sqlite3_column_bytes(stmt, col);
            return text ? std::string(reinterpret_cast<const char*>(text), static_cast<size_t>(len)) : std::string();
        }
    } // namespace

    std::string computeContentSha256(void* dbPtr,
                                      const std::string& table,
                                      const std::string& rowidColumn,
                                      const std::vector<std::string>& columns,
                                      const std::vector<std::string>& proseColumns,
                                      const std::string& compression,
                                      const std::vector<uint8_t>& dictionary) {
        sqlite3* db = static_cast<sqlite3*>(dbPtr);

        std::string sql = "SELECT \"" + rowidColumn + "\"";
        for (const auto& c : columns) sql += ", \"" + c + "\"";
        sql += " FROM \"" + table + "\" ORDER BY \"" + rowidColumn + "\" ASC";

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK) {
            throw std::runtime_error("computeContentSha256: prepare failed: " + std::string(sqlite3_errmsg(db)));
        }

        std::string content;
        int rc;
        while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
            int64_t rowid = sqlite3_column_int64(stmt, 0);
            appendU64LE(content, static_cast<uint64_t>(rowid));

            for (size_t i = 0; i < columns.size(); i++) {
                int col = static_cast<int>(i) + 1;
                if (sqlite3_column_type(stmt, col) == SQLITE_NULL) {
                    content += static_cast<char>(0x00);
                    continue;
                }
                bool isProse = std::find(proseColumns.begin(), proseColumns.end(), columns[i]) != proseColumns.end();
                std::string decoded = decodeColumn(stmt, col, isProse, compression, dictionary);
                content += static_cast<char>(0x01);
                appendU64LE(content, static_cast<uint64_t>(decoded.size()));
                content += decoded;
            }
        }
        sqlite3_finalize(stmt);
        if (rc != SQLITE_DONE) {
            throw std::runtime_error("computeContentSha256: step failed: " + std::string(sqlite3_errmsg(db)));
        }

        content += static_cast<char>(0x1e); // table separator (one ContentShape per call here)
        return sha256Hex(content);
    }

} // namespace SwordCommon
