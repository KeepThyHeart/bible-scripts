/**
 * compression.cpp — see compression.h.
 */

#include "compression.h"

#include <zlib.h>
#include <sqlite3.h>
#include <cstring>
#include <stdexcept>
#include <iostream>

namespace SwordCommon {

    const char* const CODEC_NONE = "none";
    const char* const CODEC_DEFLATE = "deflate";
    const char* const CODEC_ZSTD = "zstd";

    // ==================================================================
    // zstd: hand-declared prototypes for the stable public API.
    //
    // No libzstd-dev package (and so no zstd.h) is available on this
    // checkout's build target; libzstd.so.1 (the runtime library) is. These
    // signatures match the real <zstd.h>/<zdict.h> exactly — they have been
    // stable since zstd 1.4 (this target ships 1.5.5) — and every one is a
    // plain C function taking only pointers/integers, so there is no
    // struct-layout ABI to get wrong from declaring the opaque context types
    // as incomplete structs. See the note in compression.h.
    // ==================================================================
    extern "C" {
        size_t ZSTD_compressBound(size_t srcSize);
        unsigned ZSTD_isError(size_t code);
        unsigned long long ZSTD_getFrameContentSize(const void* src, size_t srcSize);

        typedef struct ZSTD_CCtx_s ZSTD_CCtx;
        ZSTD_CCtx* ZSTD_createCCtx(void);
        size_t ZSTD_freeCCtx(ZSTD_CCtx* cctx);

        typedef struct ZSTD_DCtx_s ZSTD_DCtx;
        ZSTD_DCtx* ZSTD_createDCtx(void);
        size_t ZSTD_freeDCtx(ZSTD_DCtx* dctx);

        size_t ZSTD_compress_usingDict(ZSTD_CCtx* ctx,
                                        void* dst, size_t dstCapacity,
                                        const void* src, size_t srcSize,
                                        const void* dict, size_t dictSize,
                                        int compressionLevel);

        size_t ZSTD_decompress_usingDict(ZSTD_DCtx* dctx,
                                          void* dst, size_t dstCapacity,
                                          const void* src, size_t srcSize,
                                          const void* dict, size_t dictSize);

        size_t ZDICT_trainFromBuffer(void* dictBuffer, size_t dictBufferCapacity,
                                      const void* samplesBuffer,
                                      const size_t* samplesSizes, unsigned nbSamples);
        unsigned ZDICT_isError(size_t errorCode);
        unsigned ZDICT_getDictID(const void* dictBuffer, size_t dictSize);
    }

    bool zstdAvailable() {
        // Linked at build time (common/Makefile: -l:libzstd.so.1); if the
        // binary runs at all, the symbols above resolved.
        return true;
    }

    // ==================================================================
    // Raw DEFLATE (RFC 1951)
    // ==================================================================

    std::vector<uint8_t> deflateEncode(const std::string& text, const std::vector<uint8_t>& dict) {
        z_stream strm;
        std::memset(&strm, 0, sizeof(strm));

        // windowBits = -15: raw deflate, no zlib header/trailer (§3.2: "2
        // bytes of overhead" — that's the 2-byte block-final marker + Adler,
        // NOT a zlib wrapper, which raw mode already excludes).
        if (deflateInit2(&strm, Z_BEST_COMPRESSION, Z_DEFLATED, -15, 8, Z_DEFAULT_STRATEGY) != Z_OK) {
            throw std::runtime_error("deflateInit2 failed");
        }
        if (!dict.empty()) {
            if (deflateSetDictionary(&strm, dict.data(), static_cast<uInt>(dict.size())) != Z_OK) {
                deflateEnd(&strm);
                throw std::runtime_error("deflateSetDictionary failed");
            }
        }

        std::vector<uint8_t> out(deflateBound(&strm, static_cast<uLong>(text.size())) + 16);
        strm.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(text.data()));
        strm.avail_in = static_cast<uInt>(text.size());
        strm.next_out = out.data();
        strm.avail_out = static_cast<uInt>(out.size());

        int rc = deflate(&strm, Z_FINISH);
        deflateEnd(&strm);
        if (rc != Z_STREAM_END) throw std::runtime_error("deflate() did not finish the stream");

        out.resize(out.size() - strm.avail_out);
        return out;
    }

    std::string deflateDecode(const uint8_t* data, size_t len, const std::vector<uint8_t>& dict) {
        z_stream strm;
        std::memset(&strm, 0, sizeof(strm));

        if (inflateInit2(&strm, -15) != Z_OK) {
            throw std::runtime_error("inflateInit2 failed");
        }
        // Raw deflate carries no dictionary checksum to negotiate against
        // (unlike the zlib-wrapped format's Z_NEED_DICT): the caller must
        // supply the dictionary up front, before the first inflate() call.
        if (!dict.empty()) {
            if (inflateSetDictionary(&strm, dict.data(), static_cast<uInt>(dict.size())) != Z_OK) {
                inflateEnd(&strm);
                throw std::runtime_error("inflateSetDictionary failed");
            }
        }

        std::string out;
        std::vector<uint8_t> buf(std::max<size_t>(len * 4, 4096));
        strm.next_in = const_cast<Bytef*>(data);
        strm.avail_in = static_cast<uInt>(len);

        int rc;
        do {
            strm.next_out = buf.data();
            strm.avail_out = static_cast<uInt>(buf.size());
            rc = inflate(&strm, Z_NO_FLUSH);
            if (rc != Z_OK && rc != Z_STREAM_END) {
                inflateEnd(&strm);
                throw std::runtime_error(std::string("inflate() failed: ") + (strm.msg ? strm.msg : "unknown"));
            }
            out.append(reinterpret_cast<char*>(buf.data()), buf.size() - strm.avail_out);
        } while (rc != Z_STREAM_END);

        inflateEnd(&strm);
        return out;
    }

    // ==================================================================
    // zstd
    // ==================================================================

    std::vector<uint8_t> zstdEncode(const std::string& text, const std::vector<uint8_t>& dict, int level) {
        ZSTD_CCtx* ctx = ZSTD_createCCtx();
        if (!ctx) throw std::runtime_error("ZSTD_createCCtx failed");

        std::vector<uint8_t> out(ZSTD_compressBound(text.size()));
        // dict.data()/0 when empty behaves as plain (dictionary-less)
        // compression — documented behaviour of ZSTD_compress_usingDict.
        size_t written = ZSTD_compress_usingDict(
            ctx, out.data(), out.size(), text.data(), text.size(),
            dict.empty() ? nullptr : dict.data(), dict.size(), level);
        ZSTD_freeCCtx(ctx);

        if (ZSTD_isError(written)) throw std::runtime_error("ZSTD_compress_usingDict failed");
        out.resize(written);
        return out;
    }

    std::string zstdDecode(const uint8_t* data, size_t len, const std::vector<uint8_t>& dict) {
        unsigned long long contentSize = ZSTD_getFrameContentSize(data, len);
        // ZSTD_CONTENTSIZE_UNKNOWN = -1, ZSTD_CONTENTSIZE_ERROR = -2 (as unsigned long long).
        if (contentSize == static_cast<unsigned long long>(-1) ||
            contentSize == static_cast<unsigned long long>(-2)) {
            throw std::runtime_error("zstd frame has no known content size (not one of our own frames?)");
        }

        ZSTD_DCtx* ctx = ZSTD_createDCtx();
        if (!ctx) throw std::runtime_error("ZSTD_createDCtx failed");

        std::string out(static_cast<size_t>(contentSize), '\0');
        size_t written = ZSTD_decompress_usingDict(
            ctx, out.empty() ? nullptr : &out[0], out.size(), data, len,
            dict.empty() ? nullptr : dict.data(), dict.size());
        ZSTD_freeDCtx(ctx);

        if (ZSTD_isError(written)) throw std::runtime_error("ZSTD_decompress_usingDict failed");
        out.resize(written);
        return out;
    }

    // ==================================================================
    // Dictionary training (§3.3)
    // ==================================================================

    std::vector<uint8_t> trainDictionary(const std::vector<std::string>& samples, size_t dictSize) {
        if (samples.empty()) return {};

        std::string concatenated;
        std::vector<size_t> sizes;
        sizes.reserve(samples.size());
        size_t total = 0;
        for (const auto& s : samples) total += s.size();
        concatenated.reserve(total);
        for (const auto& s : samples) {
            concatenated += s;
            sizes.push_back(s.size());
        }

        std::vector<uint8_t> dict(dictSize);
        size_t actual = ZDICT_trainFromBuffer(
            dict.data(), dict.size(),
            concatenated.data(), sizes.data(), static_cast<unsigned>(sizes.size()));

        if (ZDICT_isError(actual)) {
            // Too little sample data, or samples too uniform, is a normal
            // outcome for a small module — the caller falls back to no
            // dictionary rather than failing the conversion.
            return {};
        }
        dict.resize(actual);
        return dict;
    }

    uint32_t adler32Of(const std::vector<uint8_t>& data) {
        uLong a = adler32(0L, nullptr, 0);
        a = adler32(a, data.data(), static_cast<uInt>(data.size()));
        return static_cast<uint32_t>(a);
    }

    uint32_t zstdDictId(const std::vector<uint8_t>& dict) {
        if (dict.empty()) return 0;
        return static_cast<uint32_t>(ZDICT_getDictID(dict.data(), dict.size()));
    }

    // ==================================================================
    // Per-row encode
    // ==================================================================

    EncodedCell encodeCell(const std::string& codec, const std::string& text, const std::vector<uint8_t>& dict) {
        EncodedCell result;
        if (codec == CODEC_NONE) return result; // stored = false: keep as TEXT

        std::vector<uint8_t> frame;
        if (codec == CODEC_DEFLATE) {
            frame = deflateEncode(text, dict);
        } else if (codec == CODEC_ZSTD) {
            frame = zstdEncode(text, dict);
        } else {
            throw std::runtime_error("encodeCell: unknown codec '" + codec + "'");
        }

        // §3.2: keep the BLOB only if smaller than the UTF-8 text by MORE
        // THAN 16 bytes. text.size() is already the UTF-8 byte length (the
        // caller passes clean UTF-8, never wide chars).
        if (frame.size() + 16 < text.size()) {
            result.stored = true;
            result.frame = std::move(frame);
        }
        return result;
    }

    // ==================================================================
    // Whole-module compression pass
    // ==================================================================

    CompressionOutcome applyCompression(void* dbPtr,
                                         const std::string& table,
                                         const std::vector<std::string>& proseColumns,
                                         const std::string& forcedCodec,
                                         uint64_t thresholdBytes) {
        sqlite3* db = static_cast<sqlite3*>(dbPtr);
        CompressionOutcome outcome;
        outcome.codec = CODEC_NONE;

        if (proseColumns.empty()) return outcome; // nothing to compress (matches §3.4's by-type exclusions)

        std::string colList;
        for (size_t i = 0; i < proseColumns.size(); i++) {
            if (i) colList += ", ";
            colList += "\"" + proseColumns[i] + "\"";
        }

        // Total decoded size, for the eligibility decision (§3.4). Cast to
        // BLOB: SQLite's length() on TEXT counts characters, not UTF-8 bytes.
        {
            std::string sql = "SELECT ";
            for (size_t i = 0; i < proseColumns.size(); i++) {
                if (i) sql += " + ";
                sql += "COALESCE(SUM(LENGTH(CAST(\"" + proseColumns[i] + "\" AS BLOB))), 0)";
            }
            sql += " FROM \"" + table + "\"";
            sqlite3_stmt* stmt = nullptr;
            if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
                if (sqlite3_step(stmt) == SQLITE_ROW) {
                    outcome.decodedBytes = static_cast<size_t>(sqlite3_column_int64(stmt, 0));
                }
                sqlite3_finalize(stmt);
            }
        }

        std::string codec;
        if (!forcedCodec.empty()) {
            codec = forcedCodec;
        } else {
            // Auto-decide: deflate only — zstd never picked automatically,
            // it is opt-in via --codec=zstd (§3.4).
            codec = (outcome.decodedBytes >= thresholdBytes) ? CODEC_DEFLATE : CODEC_NONE;
        }

        if (codec == CODEC_NONE) {
            outcome.codec = CODEC_NONE;
            return outcome;
        }

        // Sample up to DICTIONARY_SAMPLE_ROWS rows (§3.3), one concatenated
        // string per row across its prose columns, taken at a fixed stride
        // over iteration order so a rebuild samples the same rows every
        // time — deterministic, not a seeded PRNG, but reproducible, which
        // is the property §3.3 actually asks for.
        std::vector<std::string> samples;
        {
            std::string countSql = "SELECT COUNT(*) FROM \"" + table + "\"";
            sqlite3_stmt* cstmt = nullptr;
            int64_t total = 0;
            if (sqlite3_prepare_v2(db, countSql.c_str(), -1, &cstmt, nullptr) == SQLITE_OK) {
                if (sqlite3_step(cstmt) == SQLITE_ROW) total = sqlite3_column_int64(cstmt, 0);
                sqlite3_finalize(cstmt);
            }
            int64_t stride = (total > static_cast<int64_t>(DICTIONARY_SAMPLE_ROWS))
                ? total / static_cast<int64_t>(DICTIONARY_SAMPLE_ROWS) : 1;

            std::string sql = "SELECT " + colList + " FROM \"" + table + "\" ORDER BY rowid";
            sqlite3_stmt* stmt = nullptr;
            if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
                int64_t position = 0;
                while (sqlite3_step(stmt) == SQLITE_ROW) {
                    if (position % stride == 0 && samples.size() < DICTIONARY_SAMPLE_ROWS) {
                        std::string sample;
                        for (int c = 0; c < static_cast<int>(proseColumns.size()); c++) {
                            if (sqlite3_column_type(stmt, c) != SQLITE_NULL) {
                                const unsigned char* text = sqlite3_column_text(stmt, c);
                                int len = sqlite3_column_bytes(stmt, c);
                                if (text) sample.append(reinterpret_cast<const char*>(text), static_cast<size_t>(len));
                            }
                        }
                        if (!sample.empty()) samples.push_back(std::move(sample));
                    }
                    position++;
                }
                sqlite3_finalize(stmt);
            }
        }

        size_t dictSize = (codec == CODEC_ZSTD) ? ZSTD_DICT_SIZE : DEFLATE_DICT_SIZE;
        std::vector<uint8_t> dict = trainDictionary(samples, dictSize);

        if (dict.empty()) {
            // §2.8: "Dictionary row present iff compression != 'none'" is a
            // validator ERROR, not a warning — there is no conforming state
            // for "compressed, no dictionary". Falling back to CODEC_NONE
            // for the whole module is the only choice that stays
            // conforming; this is a normal (if unusual) outcome for a small
            // or very uniform module, not a conversion failure.
            std::cerr << "    compression: dictionary training produced nothing usable for \""
                      << table << "\" (" << samples.size() << " sample rows) — shipping uncompressed."
                      << std::endl;
            outcome.codec = CODEC_NONE;
            return outcome;
        }

        outcome.codec = codec;
        outcome.dictionary = dict;
        outcome.dictId = (codec == CODEC_ZSTD) ? zstdDictId(dict) : adler32Of(dict);

        // Rewrite each row's prose columns in place.
        {
            std::string selSql = "SELECT rowid, " + colList + " FROM \"" + table + "\"";
            sqlite3_stmt* sel = nullptr;
            if (sqlite3_prepare_v2(db, selSql.c_str(), -1, &sel, nullptr) != SQLITE_OK) {
                throw std::runtime_error("applyCompression: prepare SELECT failed: " + std::string(sqlite3_errmsg(db)));
            }

            std::string updSql = "UPDATE \"" + table + "\" SET ";
            for (size_t i = 0; i < proseColumns.size(); i++) {
                if (i) updSql += ", ";
                updSql += "\"" + proseColumns[i] + "\" = ?";
            }
            updSql += " WHERE rowid = ?";
            sqlite3_stmt* upd = nullptr;
            if (sqlite3_prepare_v2(db, updSql.c_str(), -1, &upd, nullptr) != SQLITE_OK) {
                sqlite3_finalize(sel);
                throw std::runtime_error("applyCompression: prepare UPDATE failed: " + std::string(sqlite3_errmsg(db)));
            }

            while (sqlite3_step(sel) == SQLITE_ROW) {
                int64_t rowid = sqlite3_column_int64(sel, 0);
                bool anyCompressed = false;

                for (size_t c = 0; c < proseColumns.size(); c++) {
                    int col = static_cast<int>(c) + 1;
                    int bindIdx = static_cast<int>(c) + 1;
                    if (sqlite3_column_type(sel, col) == SQLITE_NULL) {
                        sqlite3_bind_null(upd, bindIdx);
                        continue;
                    }
                    const unsigned char* text = sqlite3_column_text(sel, col);
                    int len = sqlite3_column_bytes(sel, col);
                    std::string value = text ? std::string(reinterpret_cast<const char*>(text), static_cast<size_t>(len)) : std::string();

                    EncodedCell cell = encodeCell(codec, value, dict);
                    if (cell.stored) {
                        sqlite3_bind_blob(upd, bindIdx, cell.frame.data(), static_cast<int>(cell.frame.size()), SQLITE_TRANSIENT);
                        anyCompressed = true;
                    } else {
                        sqlite3_bind_text(upd, bindIdx, value.c_str(), -1, SQLITE_TRANSIENT);
                    }
                }
                sqlite3_bind_int64(upd, static_cast<int>(proseColumns.size()) + 1, rowid);

                if (sqlite3_step(upd) != SQLITE_DONE) {
                    sqlite3_finalize(sel);
                    sqlite3_finalize(upd);
                    throw std::runtime_error("applyCompression: UPDATE failed: " + std::string(sqlite3_errmsg(db)));
                }
                sqlite3_reset(upd);
                sqlite3_clear_bindings(upd);
                if (anyCompressed) outcome.rowsCompressed++;
            }
            sqlite3_finalize(sel);
            sqlite3_finalize(upd);
        }

        // module_info.compression + compression_dictionary row.
        {
            sqlite3_stmt* stmt = nullptr;
            if (sqlite3_prepare_v2(db, "UPDATE module_info SET compression = ? WHERE info_id = 1", -1, &stmt, nullptr) == SQLITE_OK) {
                sqlite3_bind_text(stmt, 1, codec.c_str(), -1, SQLITE_TRANSIENT);
                sqlite3_step(stmt);
                sqlite3_finalize(stmt);
            }
        }
        {
            sqlite3_stmt* stmt = nullptr;
            const char* sql = "INSERT INTO compression_dictionary (codec, dict_id, dict_blob) VALUES (?, ?, ?)";
            if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) == SQLITE_OK) {
                sqlite3_bind_text(stmt, 1, codec.c_str(), -1, SQLITE_TRANSIENT);
                sqlite3_bind_int64(stmt, 2, static_cast<int64_t>(outcome.dictId));
                sqlite3_bind_blob(stmt, 3, dict.data(), static_cast<int>(dict.size()), SQLITE_TRANSIENT);
                if (sqlite3_step(stmt) != SQLITE_DONE) {
                    sqlite3_finalize(stmt);
                    throw std::runtime_error("applyCompression: failed to insert compression_dictionary row: " + std::string(sqlite3_errmsg(db)));
                }
                sqlite3_finalize(stmt);
            }
        }

        return outcome;
    }

} // namespace SwordCommon
