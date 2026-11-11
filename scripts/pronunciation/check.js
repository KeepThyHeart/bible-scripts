#!/usr/bin/env node
/**
 * Validate the pronunciation overlay data (pronunciation/) and keep its index.json current.
 *
 *   node scripts/pronunciation/check.js                    validate; fail if index.json is stale
 *   node scripts/pronunciation/check.js --write-index      validate, then rewrite index.json
 *   node scripts/pronunciation/check.js --verify-dump=FILE also check every occurrence against a
 *       verse dump (verses.jsonl from the Bible repo's tools/audio-build/scripts/pronunciation-dump.mjs):
 *       the verse hash must match its spoken text and the word must be at its index.
 *
 * Layout and formats: pronunciation/README.md. The Bible repo parses these files
 * (packages/core/src/audio/pronunciation.ts); keep the two in step.
 */

'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..', 'pronunciation');
const INDEX = path.join(ROOT, 'index.json');

/** Every symbol espeak-ng prints for en-us (what Piper voices and Kokoro accept). */
const SYMBOLS = new Set([...'abdefhijklmnoprstuvwxzæðŋɐɑɔəɚɛɜɡɪɹɾʃʊʌʒʔˈˌː̩θᵻ']);
const VOWELS = new Set([...'aeiouæɐɑɔəɚɛɜɪʊʌᵻ']);
const LOCALE = /^[a-z]{2,3}-[a-z0-9]+$/;
const LANGUAGE = /^[a-z]{2,3}$/;
const WORD = /\p{L}+(?:'\p{L}+)*/gu;

const errors = [];
const fail = (file, msg) => errors.push(`${file}: ${msg}`);

function ipaProblems(ipa) {
  const out = [];
  const bad = [...new Set([...ipa].filter(c => !SYMBOLS.has(c)))];
  if (bad.length) out.push(`symbols ${bad.join(' ')}`);
  for (let i = 0; i < ipa.length; i++) {
    if ((ipa[i] === 'ˈ' || ipa[i] === 'ˌ') && !VOWELS.has(ipa[i + 1])) out.push(`stress not before a vowel at ${i}`);
  }
  if (![...ipa].some(c => VOWELS.has(c))) out.push('no vowel');
  return out;
}

function fnv1a64(text) {
  let h = 0xcbf29ce484222325n;
  for (const b of Buffer.from(text, 'utf8')) {
    h ^= BigInt(b);
    h = (h * 0x100000001b3n) & 0xffffffffffffffffn;
  }
  return h.toString(16).padStart(16, '0');
}

function readJson(rel) {
  try { return JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8')); } catch (e) { fail(rel, e.message); return null; }
}

function checkVerses(rel, verses, value) {
  for (const [ref, v] of Object.entries(verses)) {
    if (!/^[1-3A-Z][A-Z0-9]{2}\.\d+\.\d+$/.test(ref)) fail(rel, `bad ref ${ref}`);
    if (!/^[0-9a-f]{16}$/.test(v.hash)) fail(rel, `${ref}: bad hash`);
    for (const [i, w] of Object.entries(v.words)) {
      if (!/^\d+$/.test(i) || !Array.isArray(w) || w.length !== 2 || typeof w[0] !== 'string') { fail(rel, `${ref} #${i}: bad entry`); continue; }
      value(ref, i, w[0], w[1]);
    }
  }
}

function main() {
  const args = process.argv.slice(2);
  const writeIndex = args.includes('--write-index');
  const dumpArg = args.find(a => a.startsWith('--verify-dump='));

  const files = [];
  const lexicons = new Map(); // locale -> lexicon
  const senseFiles = [];      // { rel, language, data }
  for (const dir of fs.readdirSync(ROOT).sort()) {
    const full = path.join(ROOT, dir);
    if (!fs.statSync(full).isDirectory()) continue;
    if (!LOCALE.test(dir) && !LANGUAGE.test(dir)) { fail(dir, 'not a language or locale directory'); continue; }
    for (const name of fs.readdirSync(full).sort()) {
      if (!name.endsWith('.json')) continue;
      const rel = `${dir}/${name}`;
      files.push(rel);
      const data = readJson(rel);
      if (!data) continue;
      if (LOCALE.test(dir) && name === 'lexicon.json') {
        if (data.format !== 'kth-pron-lexicon/2' || data.locale !== dir) { fail(rel, 'expected kth-pron-lexicon/2 for this locale'); continue; }
        for (const [w, ipa] of Object.entries(data.entries)) for (const p of ipaProblems(ipa)) fail(rel, `${w}: ${p}`);
        for (const [w, s] of Object.entries(data.senses ?? {})) for (const [label, ipa] of Object.entries(s)) for (const p of ipaProblems(ipa)) fail(rel, `${w}#${label}: ${p}`);
        lexicons.set(dir, data);
      } else if (LOCALE.test(dir)) {
        if (data.format !== 'kth-pron-module/1' || data.locale !== dir || `${data.module}.json` !== name) { fail(rel, 'expected kth-pron-module/1 for this locale and module'); continue; }
        checkVerses(rel, data.verses, (ref, i, w, ipa) => { for (const p of ipaProblems(ipa)) fail(rel, `${ref} #${i} ${w}: ${p}`); });
      } else {
        if (data.format !== 'kth-pron-senses/1' || data.language !== dir || `${data.module}.json` !== name) { fail(rel, 'expected kth-pron-senses/1 for this language and module'); continue; }
        senseFiles.push({ rel, language: dir, data });
      }
    }
  }

  // Every sense a language file uses must exist in every locale lexicon of that language.
  for (const { rel, language, data } of senseFiles) {
    const locales = [...lexicons.entries()].filter(([loc]) => loc.startsWith(`${language}-`));
    if (locales.length === 0) fail(rel, `no ${language}-* lexicon defines its senses`);
    checkVerses(rel, data.verses, (ref, i, w, label) => {
      for (const [loc, lex] of locales) {
        if (!lex.senses?.[w.toLowerCase()]?.[label]) fail(rel, `${ref} #${i}: ${loc} has no sense ${w.toLowerCase()}#${label}`);
      }
    });
  }

  if (dumpArg) {
    const verses = new Map();
    for (const line of fs.readFileSync(dumpArg.slice('--verify-dump='.length), 'utf8').split('\n')) {
      if (line.trim()) { const v = JSON.parse(line); verses.set(v.ref, v.text); }
    }
    let checked = 0;
    const modules = [...senseFiles.map(s => ({ rel: s.rel, data: s.data }))];
    for (const rel of files.filter(f => LOCALE.test(f.split('/')[0]) && !f.endsWith('/lexicon.json'))) modules.push({ rel, data: readJson(rel) });
    for (const { rel, data } of modules) {
      checkVerses(rel, data.verses, () => {});
      for (const [ref, v] of Object.entries(data.verses)) {
        const text = verses.get(ref);
        if (text === undefined) { fail(rel, `${ref}: not in the dump`); continue; }
        if (fnv1a64(text) !== v.hash) fail(rel, `${ref}: hash does not match the spoken text`);
        const words = [...text.matchAll(WORD)].map(m => m[0]);
        for (const [i, [w]] of Object.entries(v.words)) if (words[Number(i)] !== w) fail(rel, `${ref} #${i}: "${words[Number(i)]}" is not "${w}"`);
        checked++;
      }
    }
    console.log(`verified ${checked} verse(s) against the dump`);
  }

  const index = {
    format: 'kth-pron-index/1',
    files: files.map(rel => {
      const bytes = fs.readFileSync(path.join(ROOT, rel));
      return { path: rel, bytes: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex') };
    }),
  };
  const text = `${JSON.stringify(index, null, 2)}\n`;
  if (writeIndex) fs.writeFileSync(INDEX, text);
  else if (!fs.existsSync(INDEX) || fs.readFileSync(INDEX, 'utf8') !== text) fail('index.json', 'out of date: run with --write-index');

  if (errors.length) {
    for (const e of errors.slice(0, 50)) console.error(e);
    console.error(`${errors.length} problem(s)`);
    process.exit(1);
  }
  console.log(`pronunciation ok: ${files.length} file(s)`);
}

main();
