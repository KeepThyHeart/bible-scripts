/**
 * compression.h
 *
 * Content codecs for module format v0.2 (design §3): raw DEFLATE (RFC 1951)
 * and zstd (RFC 8878), each with an optional preset/trained dictionary.
 *
 * zstd is linked against the runtime library (`libzstd.so.1`, present on
 * every target this pipeline builds on — see tools/import/sword/verify/README.md's
 * `install-deps`) WITHOUT the `libzstd-dev` header: this checkout has no
 * `zstd.h` available (no dev package), so the handful of stable, long-frozen
 * public API functions used here are declared by hand in compression.cpp,
 * matching the real header exactly. These are plain-C functions that only
 * ever pass pointers/integers (no struct-by-value ABI to get wrong), and
 * compression.cpp's own self-test (see tools/import/sword/common/README or
 * compression_selftest.cpp) exercises every one of them against the real
 * library at build time.
 */

#ifndef SWORD_COMMON_COMPRESSION_H
#define SWORD_COMMON_COMPRESSION_H

#include <cstdint>
#include <string>
#include <vector>

namespace SwordCommon {

    /** Codec names as stored in module_info.compression (design §2.1). */
    extern const char* const CODEC_NONE;
    extern const char* const CODEC_DEFLATE;
    extern const char* const CODEC_ZSTD;

    /** Dictionary sizes, design §3.3: "32 KiB for deflate, 110 KiB for zstd." */
    constexpr size_t DEFLATE_DICT_SIZE = 32 * 1024;
    constexpr size_t ZSTD_DICT_SIZE = 110 * 1024;

    /** Up to this many sample rows are used to train a dictionary (§3.3). */
    constexpr size_t DICTIONARY_SAMPLE_ROWS = 20000;

    /** True on any target where the zstd runtime library was successfully linked. */
    bool zstdAvailable();

    // ------------------------------------------------------------------
    // Raw DEFLATE (RFC 1951, no zlib/gzip wrapper — §3.2)
    // ------------------------------------------------------------------

    /** @param dict optional preset dictionary; empty = none. */
    std::vector<uint8_t> deflateEncode(const std::string& text, const std::vector<uint8_t>& dict);

    /** @param dict MUST be the same dictionary the frame was encoded with. */
    std::string deflateDecode(const uint8_t* data, size_t len, const std::vector<uint8_t>& dict);

    // ------------------------------------------------------------------
    // zstd (RFC 8878, standard framing incl. magic + dictID — §3.2)
    // ------------------------------------------------------------------

    /** @param dict optional trained dictionary; empty = none. @param level zstd compression level. */
    std::vector<uint8_t> zstdEncode(const std::string& text, const std::vector<uint8_t>& dict, int level = 19);

    /** @param dict MUST be the same dictionary the frame was encoded with. */
    std::string zstdDecode(const uint8_t* data, size_t len, const std::vector<uint8_t>& dict);

    // ------------------------------------------------------------------
    // Dictionary training (§3.3)
    // ------------------------------------------------------------------

    /**
     * Train a dictionary from sample rows using zstd's trainer (used for
     * BOTH codecs — "for deflate the trained blob is simply handed to
     * deflateSetDictionary", §3.3). Returns an empty vector when training
     * fails (e.g. too little sample data — zstd's trainer needs a reasonable
     * multiple of the target size; the caller should fall back to no
     * dictionary in that case, never fail the conversion over it).
     *
     * @param samples   up to DICTIONARY_SAMPLE_ROWS rows of the module's own
     *                  prose, chosen by the caller with a fixed seed so a
     *                  rebuild is reproducible (§3.3).
     * @param dictSize  DEFLATE_DICT_SIZE or ZSTD_DICT_SIZE.
     */
    std::vector<uint8_t> trainDictionary(const std::vector<std::string>& samples, size_t dictSize);

    /**
     * The id stored in compression_dictionary.dict_id (design §2.2):
     * "zstd dictID, or Adler-32 of dict_blob for deflate".
     */
    uint32_t adler32Of(const std::vector<uint8_t>& data);
    uint32_t zstdDictId(const std::vector<uint8_t>& dict);

    // ------------------------------------------------------------------
    // Per-row encode with the keep-only-if-smaller rule (§3.2)
    // ------------------------------------------------------------------

    struct EncodedCell {
        /** true: bind `frame` as a BLOB. false: bind the original text as TEXT (frame not worth it, or codec == 'none'). */
        bool stored = false;
        std::vector<uint8_t> frame;
    };

    /**
     * Encode `text` under `codec`/`dict`, keeping the BLOB only if it is
     * smaller than the UTF-8 text by more than 16 bytes — "No threshold
     * constant to regret, and no row ever grows" (§3.2).
     */
    EncodedCell encodeCell(const std::string& codec, const std::string& text, const std::vector<uint8_t>& dict);

    // ------------------------------------------------------------------
    // Whole-module compression pass (§3, §3.3, §3.4)
    // ------------------------------------------------------------------

    /** Default eligibility threshold, design §3.4: "≥ 8 MB of decoded prose". */
    constexpr uint64_t DEFAULT_COMPRESSION_THRESHOLD_BYTES = 8ull * 1024 * 1024;

    struct CompressionOutcome {
        std::string codec;                // what module_info.compression ended up as
        std::vector<uint8_t> dictionary;  // empty iff codec == CODEC_NONE
        uint32_t dictId = 0;
        size_t rowsCompressed = 0;
        size_t decodedBytes = 0;          // total decoded prose size that drove the eligibility decision
    };

    /**
     * Decides whether `table` gets compressed, trains a dictionary if so, and
     * rewrites its prose columns in place (UPDATE ... WHERE rowid = ?),
     * honouring the per-row keep-only-if-smaller rule (encodeCell, §3.2).
     * Writes module_info.compression and the compression_dictionary row
     * itself — this is the one function each of the four compressible
     * converters (commentary/dictionary/book/devotional) calls.
     *
     * @param db            open sqlite3* (void* to keep sqlite3.h out of this header)
     * @param table         e.g. "commentary_entry"
     * @param proseColumns  columns eligible for compression (§2.5's `prose`
     *                      set for this type — e.g. ["content"], or
     *                      ["definition","usage_notes"] for dictionary)
     * @param forcedCodec   "" = auto-decide from `thresholdBytes` (deflate
     *                      only, never zstd — §3.4: zstd is opt-in);
     *                      otherwise CODEC_NONE/CODEC_DEFLATE/CODEC_ZSTD,
     *                      overriding the threshold entirely (--codec/--compress).
     * @param thresholdBytes eligibility threshold for auto-decide.
     *
     * Falls back to CODEC_NONE if dictionary training fails (too little
     * data): design §2.8 requires "dictionary row present iff compression !=
     * 'none'", so there is no conforming state for "compressed, no
     * dictionary" — see compression.cpp for the reasoning in full.
     */
    CompressionOutcome applyCompression(void* db,
                                         const std::string& table,
                                         const std::vector<std::string>& proseColumns,
                                         const std::string& forcedCodec = "",
                                         uint64_t thresholdBytes = DEFAULT_COMPRESSION_THRESHOLD_BYTES);

} // namespace SwordCommon

#endif // SWORD_COMMON_COMPRESSION_H
