#!/usr/bin/env node

/**
 * db-cli.js — Developer CLI for querying Bible app module databases.
 *
 * Supports all module types: bible, commentary, dictionary, topical index,
 * book, and devotional. Also supports raw SQL and schema introspection.
 *
 * Uses the async `sqlite3` package (NOT better-sqlite3) so it runs with
 * system Node.js without needing Electron-compiled native modules.
 *
 * Usage:
 *   node scripts/query/db-cli.js <command> [args] [--options]
 *   node scripts/query/db-cli.js help [command]
 *
 * Module resolution: pass a DB filename (e.g. "bible_kjv"), abbreviation,
 * or full path. Files are resolved from the modules directory (see
 * scripts/lib/paths.js; override with BIBLE_MODULES_DIR).
 *
 * Output: JSON by default. Use --format=pretty for indented JSON,
 * --format=table for columnar text, or --format=count for just row count.
 *
 * This file only parses arguments and dispatches. Each command lives in
 * commands/<name>.js; shared helpers live in lib/.
 */

const { GROUPS, COMMANDS } = require('./commands');
const { output } = require('./lib/output');

function showHelp(command) {
  if (command && COMMANDS[command]) {
    const cmd = COMMANDS[command];
    console.log(`\n  ${cmd.usage}`);
    console.log(`  ${cmd.description}\n`);
    return;
  }

  console.log(`
  db-cli.js — Developer CLI for querying Bible app module databases

  Usage: node scripts/query/db-cli.js <command> [args] [--options]

  Global options:
    --format=json|pretty|table|count   Output format (default: pretty)

  Module resolution:
    Pass a DB filename (e.g., "bible_kjv"), abbreviation (e.g., "kjv"),
    or full path. Files are resolved from the modules directory
    (BIBLE_MODULES_DIR; see scripts/lib/paths.js).

  Commands:`);

  for (const [group, commands] of Object.entries(GROUPS)) {
    console.log(`\n    ${group}:`);
    for (const cmd of commands) {
      console.log(`      ${cmd.usage.padEnd(55)} ${cmd.description}`);
    }
  }

  console.log(`
  Examples:
    node scripts/query/db-cli.js modules
    node scripts/query/db-cli.js verse kjv "John 3:16"
    node scripts/query/db-cli.js verse bible_kjv "Gen 1:1-5" --format=table
    node scripts/query/db-cli.js search kjv "love one another" --limit=10
    node scripts/query/db-cli.js commentary gill "Rom 8:28"
    node scripts/query/db-cli.js define strongsgreek G25
    node scripts/query/db-cli.js topics topical_nave --parent="Jesus, The Christ"
    node scripts/query/db-cli.js topic topical_nave "MIRACLES OF"
    node scripts/query/db-cli.js topic-tree topical_nave Manasseh --depth=5
    node scripts/query/db-cli.js topic-tree topical_nave "Jesus, The Christ" --depth=2 --format=json
    node scripts/query/db-cli.js verse-topics topical_nave "John 3:2"
    node scripts/query/db-cli.js xref tsk "John 3:16"
    node scripts/query/db-cli.js sections book_institutes
    node scripts/query/db-cli.js devotional devotional_daily 1
    node scripts/query/db-cli.js sql bible_kjv "SELECT COUNT(*) as total FROM bible_verse"
    node scripts/query/db-cli.js stats kjv
  `);
}

async function main() {
  const rawArgs = process.argv.slice(2);

  // Parse options and positional args
  const opts = {};
  const positional = [];
  for (const arg of rawArgs) {
    if (arg.startsWith('--')) {
      const eqIdx = arg.indexOf('=');
      if (eqIdx > 0) {
        opts[arg.substring(2, eqIdx)] = arg.substring(eqIdx + 1);
      } else {
        opts[arg.substring(2)] = true;
      }
    } else {
      positional.push(arg);
    }
  }

  const format = opts.format || 'pretty';
  const command = positional[0];
  const args = positional.slice(1);

  if (!command || command === 'help') {
    showHelp(args[0]);
    return;
  }

  const cmd = COMMANDS[command];
  if (!cmd) {
    console.error(`Unknown command: "${command}". Run "node scripts/query/db-cli.js help" for usage.`);
    process.exit(1);
  }

  if (args.length < cmd.args) {
    console.error(`Usage: ${cmd.usage}`);
    process.exit(1);
  }

  try {
    const result = await cmd.run(args, opts);
    // A command may supply its own renderer for the default (`pretty`) format;
    // json/table/count always go through the generic formatter so output stays
    // machine-readable.
    if (cmd.render && (format === 'pretty' || format === 'tree')) cmd.render(result);
    else output(result, format);
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}

main();
