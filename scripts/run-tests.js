#!/usr/bin/env node

/**
 * Runs every `*.test.js` file under scripts/ as a separate process (each test
 * file is a script with its own `test()`/assert calls and its own process
 * exit code, the same convention db-cli.test.js established — see that
 * file's own doc comment). `npm test` calls this instead of listing test
 * files by hand, so adding a new *.test.js file is enough to have it run.
 */

'use strict';

const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');

const ROOT = path.join(__dirname, '..');

function findTestFiles(dir, out) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) findTestFiles(full, out);
    else if (entry.isFile() && entry.name.endsWith('.test.js')) out.push(full);
  }
  return out;
}

const files = findTestFiles(path.join(ROOT, 'scripts'), []).sort();

if (files.length === 0) {
  console.error('No *.test.js files found under scripts/.');
  process.exit(2);
}

let failed = 0;
for (const file of files) {
  console.log(`\n=== ${path.relative(ROOT, file)} ===`);
  const result = spawnSync(process.execPath, [file], { stdio: 'inherit' });
  if (result.status !== 0) failed++;
}

console.log(`\n${files.length - failed}/${files.length} test file(s) passed.`);
process.exit(failed === 0 ? 0 : 1);
