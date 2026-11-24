/**
 * Tests for scripts/lib/codec.js — module format v0.2 content codecs (design §3).
 * Run: node scripts/lib/codec.test.js
 */

'use strict';

const assert = require('assert');
const zlib = require('zlib');
const codec = require('./codec');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ok   ${name}`);
  } catch (err) {
    failed++;
    console.log(`  FAIL ${name}`);
    console.log(`       ${err.message}`);
  }
}

async function testAsync(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  ok   ${name}`);
  } catch (err) {
    failed++;
    console.log(`  FAIL ${name}`);
    console.log(`       ${err.stack || err.message}`);
  }
}

const SAMPLE = 'In the beginning God created the heaven and the earth.';

async function main() {
  test('deflateEncode/deflateDecode round-trip, no dictionary', () => {
    const frame = codec.deflateEncode(SAMPLE, null);
    const back = codec.deflateDecode(frame, null);
    assert.strictEqual(back, SAMPLE);
  });

  test('deflateEncode/deflateDecode round-trip, with dictionary', () => {
    const dict = Buffer.from('In the beginning God created the heaven and the earth');
    const frame = codec.deflateEncode(SAMPLE, dict);
    const back = codec.deflateDecode(frame, dict);
    assert.strictEqual(back, SAMPLE);
  });

  test('deflateEncode produces raw DEFLATE (no zlib header)', () => {
    const frame = codec.deflateEncode(SAMPLE, null);
    // A zlib-wrapped stream's first byte is 0x78 (CMF for a 32K window);
    // raw deflate has no such fixed marker byte, and zlib.inflateSync (which
    // expects the wrapper) must fail on it while inflateRawSync succeeds.
    assert.throws(() => zlib.inflateSync(frame));
    assert.doesNotThrow(() => zlib.inflateRawSync(frame));
  });

  test('decodeSync dispatches by codec, "none" passes strings through', () => {
    assert.strictEqual(codec.decodeSync('none', 'hello', null), 'hello');
  });

  test('decodeSync: a cell stored as plain TEXT under a compressed module is returned as-is', () => {
    // §3.2: "keep the BLOB only if it shrinks" — some rows stay TEXT even when
    // module_info.compression != 'none'. The accessor branches on SQLite type,
    // not on the module's codec.
    assert.strictEqual(codec.decodeSync('deflate', 'short', null), 'short');
  });

  test('encodeRow keeps short text as TEXT (does not grow rows) — §3.2', () => {
    const result = codec.encodeRow('deflate', 'hi', null);
    assert.strictEqual(typeof result, 'string');
    assert.strictEqual(result, 'hi');
  });

  test('encodeRow returns a Buffer when the frame shrinks by >16 bytes', () => {
    const longText = 'the quick brown fox jumps over the lazy dog. '.repeat(50);
    const result = codec.encodeRow('deflate', longText, null);
    assert.ok(Buffer.isBuffer(result), 'expected a Buffer for compressible long text');
    assert.ok(result.length + 16 < Buffer.byteLength(longText, 'utf8'));
  });

  test('encodeRow "none" always returns the original text', () => {
    const longText = 'x'.repeat(1000);
    assert.strictEqual(codec.encodeRow('none', longText, null), longText);
  });

  test('zstdFrameHasDictId: false for a non-zstd buffer', () => {
    assert.strictEqual(codec.zstdFrameHasDictId(Buffer.from('not zstd')), false);
  });

  test('zstdFrameHasDictId: false for a short buffer', () => {
    assert.strictEqual(codec.zstdFrameHasDictId(Buffer.from([1, 2])), false);
  });

  await testAsync('zstdDecode round-trips a frame encoded by @mongodb-js/zstd (no dictionary)', async () => {
    if (!codec.hasZstdBinding()) {
      console.log('       (skipped: @mongodb-js/zstd not installed)');
      return;
    }
    const zstd = require('@mongodb-js/zstd');
    const frame = await zstd.compress(Buffer.from(SAMPLE, 'utf8'), 19);
    const back = await codec.zstdDecode(frame);
    assert.strictEqual(back, SAMPLE);
  });

  await testAsync('decodeAsync handles all three codecs, including zstd', async () => {
    assert.strictEqual(await codec.decodeAsync('none', SAMPLE, null), SAMPLE);
    const dFrame = codec.deflateEncode(SAMPLE, null);
    assert.strictEqual(await codec.decodeAsync('deflate', dFrame, null), SAMPLE);
    if (codec.hasZstdBinding()) {
      const zstd = require('@mongodb-js/zstd');
      const zFrame = await zstd.compress(Buffer.from(SAMPLE, 'utf8'), 19);
      assert.strictEqual(await codec.decodeAsync('zstd', zFrame, null), SAMPLE);
    }
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
}

main();
