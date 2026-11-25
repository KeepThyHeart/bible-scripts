#!/usr/bin/env node
'use strict';

/**
 * patch-provenance.js — add `metadata.provenance` to already-shipped modules
 * in place, without rebuilding them (task 0227).
 *
 * For every `<dir>/*.db` whose `module_info.metadata` carries a `sword_module`
 * (or, for xref_tsk.db, `sourceModule: commentary_tsk`, taking the SWORD trail
 * from commentary_tsk.db next to it), the module is COPIED to --out-dir, its
 * `metadata` JSON gets a `provenance` object (existing keys untouched), and
 * the copy is checkpointed. The source directory is only read.
 *
 *   --modules-dir=DIR   source modules (default: MODULES_DIR from lib/paths)
 *   --out-dir=DIR       where patched copies (and the .gz payloads) go (required)
 *   --repo=ABBREV=NAME|URL   non-CrossWire source, e.g. --repo=Gill=Xiphos|https://www.xiphos.org/

 *   --module=FILE_STEM=SWORDNAME   a module with no sword_module in its metadata that you know came
 *                       from SWORD, e.g. --module=commentary_gill=Gill (adds sword_module too)
 *   --gzip              also write <module>.db.gz (what catalogs serve)
 *   --catalog=FILE      a catalog.json: a patched copy goes to <out-dir>/catalog.json with the
 *                       entry's checksum (sha256 of the decompressed .db), download_size_bytes
 *                       and installed_size_bytes refreshed. content_sha256 is NOT touched: it
 *                       hashes content rows, which this patch never changes. The result is
 *                       UNSIGNED: re-sign with scripts/yubikey-sign.py (needs the YubiKey).
 *   --dry-run           report what would be patched, write nothing
 *   --report=FILE       write the licence report (JSON) there
 *
 * Patching is idempotent: a module that already has `provenance` is left alone.
 * Then check with: node scripts/modules/validate-module.js --all --modules-dir=<out-dir>
 */

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const crypto = require('crypto');
const sqlite3 = require('sqlite3');
const identity = require('../lib/module-identity');

function open(file, mode) {
  return new Promise((res, rej) => { const d = new sqlite3.Database(file, mode, e => e ? rej(e) : res(d)); });
}
const run = (d, sql, p = []) => new Promise((res, rej) => d.run(sql, p, e => e ? rej(e) : res()));
const get = (d, sql, p = []) => new Promise((res, rej) => d.get(sql, p, (e, r) => e ? rej(e) : res(r)));
const close = d => new Promise((res, rej) => d.close(e => e ? rej(e) : res()));

function parseArgs(argv) {
  const o = { repos: {}, names: {}, gzip: false, dryRun: false };
  for (const a of argv) {
    if (a === '--gzip') o.gzip = true;
    else if (a === '--dry-run') o.dryRun = true;
    else if (a.startsWith('--modules-dir=')) o.modulesDir = a.slice(14);
    else if (a.startsWith('--out-dir=')) o.outDir = a.slice(10);
    else if (a.startsWith('--catalog=')) o.catalog = a.slice(10);
    else if (a.startsWith('--report=')) o.report = a.slice(9);
    else if (a.startsWith('--module=')) {
      const m = /^([^=]+)=(.+)$/.exec(a.slice(9));
      if (!m) throw new Error(`Bad --module (want FILE_STEM=SWORDNAME): ${a}`);
      o.names[m[1]] = m[2];
    } else if (a.startsWith('--repo=')) {
      const m = /^([^=]+)=([^|]+)\|(.+)$/.exec(a.slice(7));
      if (!m) throw new Error(`Bad --repo (want ABBREV=NAME|URL): ${a}`);
      o.repos[m[1].toLowerCase()] = { name: m[2], url: m[3] };
    } else throw new Error(`Unknown option: ${a}`);
  }
  if (!o.modulesDir) o.modulesDir = require('../lib/paths').MODULES_DIR;
  if (!o.outDir && !o.dryRun) throw new Error('--out-dir is required (unless --dry-run)');
  return o;
}

async function readInfo(file) {
  const db = await open(file, sqlite3.OPEN_READONLY);
  try {
    return await get(db, 'SELECT abbreviation, content_version, license_spdx, copyright, metadata FROM module_info WHERE info_id = 1');
  } catch (e) {
    return null;
  } finally { await close(db); }
}

function parseMeta(info) {
  try { return JSON.parse((info && info.metadata) || '{}') || {}; } catch (e) { return {}; }
}

function sha256File(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

/** Decide the provenance for one module, or return { skip: reason }. */
async function planFor(file, info, opts) {
  const meta = parseMeta(info);
  if (meta.provenance) return { skip: 'already has provenance' };
  const base = path.basename(file, '.db');
  const abbrev = (info.abbreviation || '').toLowerCase();
  const repo = opts.repos[abbrev] || opts.repos[base] || identity.CROSSWIRE;

  const swordName = meta.sword_module || opts.names[base];
  if (swordName) {
    return { provenance: identity.buildProvenance(swordName, info.content_version, repo), extra: meta.sword_module ? {} : { sword_module: swordName } };
  }
  if (meta.sourceModule === 'commentary_tsk') {
    const src = path.join(path.dirname(file), 'commentary_tsk.db');
    const srcInfo = fs.existsSync(src) ? await readInfo(src) : null;
    const srcMeta = parseMeta(srcInfo);
    const name = srcMeta.sword_module || 'TSK';
    const version = (srcInfo && srcInfo.content_version) || info.content_version;
    const provenance = srcMeta.provenance || identity.buildProvenance(name, version, repo);
    return { provenance, extra: { sword_module: name } };
  }
  return { skip: 'no SWORD trail in metadata (not converted by the SWORD importers, or converted before sword_module was recorded)' };
}

async function patchCopy(src, dest, plan) {
  // Copy the db with any WAL/SHM beside it so unflushed pages are not lost.
  for (const suffix of ['', '-wal', '-shm']) {
    if (fs.existsSync(src + suffix)) fs.copyFileSync(src + suffix, dest + suffix);
  }
  const db = await open(dest, sqlite3.OPEN_READWRITE);
  try {
    const info = await get(db, 'SELECT metadata FROM module_info WHERE info_id = 1');
    const meta = parseMeta(info);
    // provenance first, as the converters write it; every other key keeps its value.
    const patched = { provenance: plan.provenance, ...plan.extra, ...meta };
    // Existing keys must survive verbatim (offlineRoutes.ts reads sword_*).
    for (const k of Object.keys(meta)) {
      if (patched[k] !== meta[k]) throw new Error(`${dest}: key ${k} would change`);
    }
    await run(db, 'UPDATE module_info SET metadata = ? WHERE info_id = 1', [JSON.stringify(patched)]);
    await get(db, 'PRAGMA wal_checkpoint(TRUNCATE)');
  } finally { await close(db); }
  for (const suffix of ['-wal', '-shm']) fs.rmSync(dest + suffix, { force: true });
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const files = fs.readdirSync(opts.modulesDir).filter(f => f.endsWith('.db')).sort();
  if (!opts.dryRun) fs.mkdirSync(opts.outDir, { recursive: true });

  const patched = [];
  const skipped = [];
  const licences = [];

  for (const f of files) {
    const src = path.join(opts.modulesDir, f);
    const info = await readInfo(src);
    if (!info) { skipped.push({ file: f, reason: 'no readable module_info' }); continue; }
    const meta = parseMeta(info);
    licences.push({
      file: f, abbreviation: info.abbreviation, license_spdx: info.license_spdx,
      distribution_license: meta.sword_distribution_license || null,
    });
    const plan = await planFor(src, info, opts);
    if (plan.skip) { skipped.push({ file: f, reason: plan.skip }); continue; }
    const entry = { file: f, attribution: plan.provenance.attribution };
    if (!opts.dryRun) {
      const dest = path.join(opts.outDir, f);
      await patchCopy(src, dest, plan);
      entry.sha256 = sha256File(dest);
      entry.installed_size_bytes = fs.statSync(dest).size;
      if (opts.gzip) {
        const gz = zlib.gzipSync(fs.readFileSync(dest), { level: 9 });
        fs.writeFileSync(dest + '.gz', gz);
        entry.download_size_bytes = gz.length;
      }
    }
    patched.push(entry);
    console.log(`${opts.dryRun ? 'would patch' : 'patched'} ${f}`);
  }

  if (opts.catalog && !opts.dryRun) {
    const cat = JSON.parse(fs.readFileSync(opts.catalog, 'utf8'));
    let touched = 0;
    for (const p of patched) {
      const id = p.file.replace(/\.db$/, '');
      const m = (cat.modules || []).find(x => x.module_id === id);
      if (!m) continue;
      m.checksum = p.sha256;
      m.installed_size_bytes = p.installed_size_bytes;
      if (p.download_size_bytes) m.download_size_bytes = p.download_size_bytes;
      touched++;
    }
    fs.writeFileSync(path.join(opts.outDir, 'catalog.json'), JSON.stringify(cat, null, 2) + '\n');
    console.log(`catalog: ${touched} entries refreshed -> ${path.join(opts.outDir, 'catalog.json')} (UNSIGNED: re-sign with yubikey-sign.py)`);
  }

  const free = /^(PD|CC0|CC-BY|CC-BY-SA|GPL|LGPL|Apache|MIT|BSD)/i;
  const report = {
    patched: patched.length, skipped,
    not_pd_or_open: licences.filter(l => !free.test(l.license_spdx || '') ),
  };
  if (opts.report) fs.writeFileSync(opts.report, JSON.stringify({ ...report, patched }, null, 2) + '\n');
  console.log(`\n${patched.length} patched, ${skipped.length} skipped.`);
  for (const s of skipped) console.log(`  skipped ${s.file}: ${s.reason}`);
}

main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
