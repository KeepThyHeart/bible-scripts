'use strict';

/**
 * Content codecs for module format v0.2 prose columns (design §3).
 *
 * A prose cell holds either TEXT (uncompressed) or a BLOB (one codec frame,
 * nothing else — no custom header; §3.2). This module is the Node-side half
 * of that contract: it decodes whatever the C++ converters wrote, and (for
 * 'deflate') can also encode, so JS-only tooling (tests, the validator, a
 * scratch transform) does not need the C++ binaries to build a fixture.
 *
 * Codec support here is intentionally asymmetric with the C++ converters:
 *   - 'deflate' (raw DEFLATE, RFC 1951, no zlib wrapper): full encode AND
 *     decode, with an optional preset dictionary (Node's zlib supports this
 *     natively). This is the publisher default (§3.4) and the path every
 *     test here exercises most.
 *   - 'zstd' (RFC 8878): DECODE only, and only WITHOUT a preset dictionary.
 *     Node has no maintained binding exposing zstd's dictionary API
 *     (ZSTD_decompress_usingDict / ZDICT_*); @mongodb-js/zstd's public API
 *     is `compress(buffer, level)` / `decompress(buffer)` with no dictionary
 *     parameter. Encoding zstd modules is the C++ converters' job (they link
 *     libzstd directly and use the real dictionary API). A caller that hits
 *     a zstd cell trained with a dictionary gets a clear
 *     'zstd-dictionary-unsupported' error rather than a wrong answer — see
 *     decode()'s doc comment.
 *
 * 'none' means the cell is already TEXT; codec.js is not involved.
 */

const zlib = require('zlib');

let zstdBinding = null;
let zstdLoadError = null;
try {
  // Optional dependency (package.json). Loaded lazily/guarded so a checkout
  // that hasn't run `npm install` with it still works for the deflate path,
  // which is all the publisher default needs.
  zstdBinding = require('@mongodb-js/zstd');
} catch (e) {
  zstdLoadError = e;
}

const SUPPORTED_CODECS = new Set(['none', 'deflate', 'zstd']);

function assertSupportedCodec(codec) {
  if (!SUPPORTED_CODECS.has(codec)) {
    throw new Error(`Unknown codec '${codec}'. Supported: ${[...SUPPORTED_CODECS].join(', ')}`);
  }
}

/**
 * Encode UTF-8 text as a raw DEFLATE frame (RFC 1951, no zlib/gzip wrapper).
 * @param {string} text
 * @param {Buffer} [dict] optional preset dictionary (§3.3)
 * @returns {Buffer}
 */
function deflateEncode(text, dict) {
  const opts = { level: zlib.constants.Z_BEST_COMPRESSION };
  if (dict && dict.length) opts.dictionary = dict;
  return zlib.deflateRawSync(Buffer.from(text, 'utf8'), opts);
}

/**
 * Decode a raw DEFLATE frame back to its exact original bytes (Buffer, not a
 * JS string): `.toString('utf8')` REPLACES invalid UTF-8 with U+FFFD (Node
 * gives no strict mode), which is fine for display but corrupts anything
 * that must reproduce the source byte-for-byte — content_sha256 (§2.7), most
 * of all, since compression.cpp/content_digest.cpp hash the raw decoded
 * bytes with no UTF-8 validation at all (confirmed on Barnes.zip, task 0035:
 * real SWORD source data, byte-identical to libsword's own reading, is not
 * valid UTF-8 in a few spots). Use this for the digest; deflateDecode()
 * (below) remains the lossy, string-returning form for display/validation
 * text scans, where U+FFFD is the intended, documented behaviour.
 * @param {Buffer} frame
 * @param {Buffer} [dict] must be the same dictionary the frame was encoded with
 * @returns {Buffer}
 */
function deflateDecodeBytes(frame, dict) {
  const opts = {};
  if (dict && dict.length) opts.dictionary = dict;
  return zlib.inflateRawSync(frame, opts);
}

/**
 * Decode a raw DEFLATE frame back to UTF-8 text.
 * @param {Buffer} frame
 * @param {Buffer} [dict] must be the same dictionary the frame was encoded with
 * @returns {string}
 */
function deflateDecode(frame, dict) {
  return deflateDecodeBytes(frame, dict).toString('utf8');
}

/** True when a Node binding for zstd decode is available. */
function hasZstdBinding() {
  return zstdBinding !== null;
}

/**
 * Decode a standard zstd frame (§3.2: magic + dictID, no custom framing) back
 * to UTF-8 text. Synchronous wrapper over @mongodb-js/zstd's async API, since
 * every other codec function here (and every caller: validate-module.js,
 * content-digest.js) is synchronous — decode is the read side of a build-time
 * tool, not a hot path.
 *
 * @param {Buffer} frame
 * @throws {Error} 'zstd-binding-unavailable' if the optional dependency isn't
 *   installed; 'zstd-dictionary-unsupported' if the frame carries a dictID
 *   (Node has no zstd dictionary binding — see the file doc comment).
 * @returns {Promise<string>}
 */
async function zstdDecode(frame) {
  const out = await zstdDecodeBytes(frame);
  return out.toString('utf8');
}

/**
 * Byte-exact counterpart of zstdDecode() — see deflateDecodeBytes()'s doc
 * comment for why the digest needs this instead of the lossy string form.
 * @param {Buffer} frame
 * @returns {Promise<Buffer>}
 */
async function zstdDecodeBytes(frame) {
  if (!zstdBinding) {
    const err = new Error(
      `zstd decode requires the optional '@mongodb-js/zstd' dependency, which failed to load: ` +
      `${zstdLoadError ? zstdLoadError.message : 'not installed'}`
    );
    err.code = 'zstd-binding-unavailable';
    throw err;
  }
  if (zstdFrameHasDictId(frame)) {
    const err = new Error(
      'This zstd frame was encoded with a preset dictionary (§3.3). Node has no binding for ' +
      "zstd's dictionary API (ZSTD_decompress_usingDict); decode it with the C++ tooling or " +
      "Python's zstandard package (used by tools/import/sword/verify/swordcheck.py) instead."
    );
    err.code = 'zstd-dictionary-unsupported';
    throw err;
  }
  return zstdBinding.decompress(frame);
}

/**
 * Whether a standard zstd frame's header declares a dictionary ID (RFC 8878
 * §3.1.1.1.2): byte 4 (0-based) is the Frame_Header_Descriptor; its low 2
 * bits are Dictionary_ID_Flag, non-zero iff a Dictionary_ID field follows.
 * Magic number is the 4 bytes before it (0xFD2FB528, little-endian).
 */
function zstdFrameHasDictId(frame) {
  if (!Buffer.isBuffer(frame) || frame.length < 5) return false;
  if (frame.readUInt32LE(0) !== 0xfd2fb528) return false; // not a standard zstd frame
  const descriptor = frame[4];
  return (descriptor & 0x03) !== 0;
}

/**
 * Synchronous decode dispatcher used by content-digest.js and
 * validate-module.js, both of which are synchronous top to bottom apart from
 * their SQLite driver. zstd decode is inherently async (the binding's API),
 * so this throws if asked to decode zstd; callers that may see zstd content
 * use decodeAsync() instead.
 */
function decodeSync(codec, value, dict) {
  assertSupportedCodec(codec);
  if (codec === 'none') {
    if (Buffer.isBuffer(value)) return value.toString('utf8');
    return value;
  }
  if (!Buffer.isBuffer(value)) {
    // A cell under a non-'none' module codec is still allowed to be stored as
    // plain TEXT (§3.2: "keep the BLOB only if it shrinks"): the accessor
    // branches on the SQLite type, not on module_info.compression.
    return value;
  }
  if (codec === 'deflate') return deflateDecode(value, dict);
  throw new Error(`decodeSync() cannot decode codec '${codec}' — it requires async I/O; use decodeAsync()`);
}

/** Async decode dispatcher: the general case, handles every supported codec. */
async function decodeAsync(codec, value, dict) {
  assertSupportedCodec(codec);
  if (codec === 'none' || !Buffer.isBuffer(value)) return decodeSync('none', value, dict);
  if (codec === 'deflate') return deflateDecode(value, dict);
  if (codec === 'zstd') return zstdDecode(value);
  throw new Error(`Unreachable: codec '${codec}'`);
}

/**
 * Encode `text` under `codec`, honouring the per-row keep-only-if-smaller
 * rule (§3.2): returns the original string unless the encoded frame is
 * smaller than the UTF-8 byte length by more than 16 bytes.
 *
 * @returns {string|Buffer} what to store in the cell.
 */
function encodeRow(codec, text, dict) {
  if (codec === 'none' || text === null || text === undefined) return text;
  const utf8Len = Buffer.byteLength(text, 'utf8');
  let frame;
  if (codec === 'deflate') {
    frame = deflateEncode(text, dict);
  } else if (codec === 'zstd') {
    throw new Error("encodeRow() does not support 'zstd' — zstd encoding is C++-only (see file doc comment)");
  } else {
    assertSupportedCodec(codec);
  }
  return frame.length + 16 < utf8Len ? frame : text;
}

module.exports = {
  SUPPORTED_CODECS,
  deflateEncode,
  deflateDecode,
  deflateDecodeBytes,
  hasZstdBinding,
  zstdDecode,
  zstdDecodeBytes,
  zstdFrameHasDictId,
  decodeSync,
  decodeAsync,
  encodeRow,
};
