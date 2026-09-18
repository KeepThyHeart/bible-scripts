#!/usr/bin/env node
/**
 * Debug utility: List all installed module databases
 *
 * Usage:
 *   node tools/debug/list-modules.cjs             # List all modules
 *   node tools/debug/list-modules.cjs bible        # List only Bible modules
 *   node tools/debug/list-modules.cjs commentary   # List only commentary modules
 *   node tools/debug/list-modules.cjs dictionary    # List only dictionary modules
 */

const path = require('path');
const fs = require('fs');
const paths = require('../../scripts/lib/paths');

const filter = (process.argv[2] || '').toLowerCase();
const modulesDir = paths.MODULES_DIR;

try {
  const files = fs.readdirSync(modulesDir)
    .filter(f => f.endsWith('.db') && !f.includes('-shm') && !f.includes('-wal'))
    .filter(f => !filter || f.startsWith(filter + '_'))
    .sort();

  console.log(`\n=== Installed Modules (${files.length}) ===\n`);

  const groups = {};
  files.forEach(f => {
    const type = f.split('_')[0];
    if (!groups[type]) groups[type] = [];
    groups[type].push(f);
  });

  Object.keys(groups).sort().forEach(type => {
    console.log(`--- ${type} (${groups[type].length}) ---`);
    groups[type].forEach(f => {
      const stats = fs.statSync(path.join(modulesDir, f));
      const sizeMB = (stats.size / (1024 * 1024)).toFixed(1);
      console.log(`  ${f} (${sizeMB} MB)`);
    });
    console.log();
  });
} catch (err) {
  console.error('Error:', err.message);
  process.exit(1);
}
