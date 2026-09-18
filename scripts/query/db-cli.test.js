/**
 * Tests for scripts/query/db-cli.js — the developer CLI for module databases.
 *
 * Run: node scripts/query/db-cli.test.js
 *
 * The CLI is a script, not a module (it calls main() on load), so each case
 * spawns it and reads its stdout — which is also the interface a developer
 * actually uses. Fixtures are small databases built in a temp directory in the
 * shape the Bible repo's schemas define (packages/core/sql/schemas/initial).
 */

const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const sqlite3 = require('sqlite3');

const CLI = path.join(__dirname, 'db-cli.js');

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

/** Run the CLI, returning raw stdout. Throws with stderr on a non-zero exit. */
function cli(...args) {
  try {
    return execFileSync(process.execPath, [CLI, ...args], { encoding: 'utf8' });
  } catch (err) {
    throw new Error(`db-cli ${args.join(' ')} failed:\n${err.stderr || err.message}`);
  }
}

/** Run the CLI with --format=json and parse the result. */
function cliJson(...args) {
  return JSON.parse(cli(...args, '--format=json'));
}

// ── Fixtures ────────────────────────────────────────────────────────────────

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'db-cli-test-'));
process.on('exit', () => fs.rmSync(TMP, { recursive: true, force: true }));

/** Build a fixture database from a list of statements. */
async function buildDb(name, statements) {
  const file = path.join(TMP, name);
  const db = new sqlite3.Database(file);
  await new Promise((resolve, reject) => {
    db.serialize(() => {
      for (const sql of statements) {
        db.run(sql, err => { if (err) reject(new Error(`${name}: ${err.message}\n${sql}`)); });
      }
      db.run('PRAGMA user_version = 1', err => (err ? reject(err) : resolve()));
    });
  });
  await new Promise(resolve => db.close(() => resolve()));
  return file;
}

const VERSE_LINK_TABLE = `CREATE TABLE verse_link (
  link_id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL, source_id INTEGER NOT NULL,
  verse_id_start INTEGER NOT NULL, verse_id_end INTEGER NOT NULL,
  link_type TEXT NOT NULL DEFAULT 'reference', sort_order INTEGER NOT NULL DEFAULT 0,
  context TEXT, metadata TEXT)`;

// Two topics named "Manasseh": a leaf inserted first (so it holds the lower
// rowid) and the real root entry with its numbered senses. This is the shape
// that made `topic-tree Manasseh` render an empty leaf — Nave's has nine
// topics by that name and only one of them is the entry anybody wants.
function topicalStatements() {
  return [
    `CREATE TABLE topic (
       topic_id INTEGER PRIMARY KEY, parent_topic_id INTEGER REFERENCES topic(topic_id),
       name TEXT NOT NULL, description TEXT, sort_order INTEGER NOT NULL DEFAULT 0, metadata TEXT)`,
    VERSE_LINK_TABLE,
    `CREATE VIRTUAL TABLE topic_fts USING fts5(name, content='topic', content_rowid='topic_id')`,
    `INSERT INTO topic (topic_id, parent_topic_id, name, sort_order) VALUES
       (1, NULL, 'Israel', 1),
       (2, 1, 'Manasseh', 1),
       (3, NULL, 'Manasseh', 2),
       (4, 3, 'Son of Joseph and Asenath', 1),
       (5, 3, 'Tribe of', 2),
       (6, 5, 'Inheritance of', 1),
       (7, 3, 'King of Judah', 3)`,
    `INSERT INTO topic_fts(rowid, name) SELECT topic_id, name FROM topic`,
    // Gen 41:50 for sense 1; Josh 17:1-2 (a range) for "Inheritance of".
    `INSERT INTO verse_link (source_type, source_id, verse_id_start, verse_id_end, sort_order) VALUES
       ('topic', 4, 1041050, 1041050, 1),
       ('topic', 6, 6017001, 6017002, 1)`,
  ];
}

function bibleStatements() {
  return [
    `CREATE TABLE bible_verse (verse_id INTEGER PRIMARY KEY, text TEXT NOT NULL,
       formatting TEXT, word_count INTEGER, metadata TEXT)`,
    `INSERT INTO bible_verse (verse_id, text) VALUES (43003016, 'For God so loved the world')`,
  ];
}

function xrefStatements() {
  return [
    `CREATE TABLE cross_reference_group (
       group_id INTEGER PRIMARY KEY AUTOINCREMENT, verse_id_start INTEGER NOT NULL,
       verse_id_end INTEGER NOT NULL, phrase TEXT, sort_order INTEGER NOT NULL DEFAULT 0)`,
    VERSE_LINK_TABLE,
    `INSERT INTO cross_reference_group (group_id, verse_id_start, verse_id_end, phrase)
       VALUES (1, 43003016, 43003016, 'For God so loved')`,
    `INSERT INTO verse_link (source_type, source_id, verse_id_start, verse_id_end, sort_order) VALUES
       ('cross_reference_group', 1, 45005008, 45005008, 1),
       ('cross_reference_group', 1, 47005019, 47005021, 2)`,
  ];
}

function bookStatements() {
  return [
    `CREATE TABLE book_section (
       section_id INTEGER PRIMARY KEY, parent_section_id INTEGER, section_number TEXT,
       title TEXT, content TEXT, word_count INTEGER, metadata TEXT)`,
    VERSE_LINK_TABLE,
    `INSERT INTO book_section (section_id, parent_section_id, section_number, title, content)
       VALUES (1, NULL, '1', 'Preface', 'Text')`,
    `INSERT INTO verse_link (source_type, source_id, verse_id_start, verse_id_end)
       VALUES ('book_section', 1, 43003016, 43003016)`,
  ];
}

// ── Cases ───────────────────────────────────────────────────────────────────

function topicalCases(db) {
  test('topic-tree finds the root "Manasseh", not the lower-rowid leaf', () => {
    const tree = cliJson('topic-tree', db, 'Manasseh', '--depth=5');
    assert.strictEqual(tree.topic_id, 3, 'resolved the wrong topic');
    assert.deepStrictEqual(
      tree.children.map(c => c.name),
      ['Son of Joseph and Asenath', 'Tribe of', 'King of Judah']
    );
  });

  test('topic-tree nests grandchildren under the right sense', () => {
    const tree = cliJson('topic-tree', db, 'Manasseh', '--depth=5');
    const tribe = tree.children.find(c => c.name === 'Tribe of');
    assert.deepStrictEqual(tribe.children.map(c => c.name), ['Inheritance of']);
  });

  test('topic-tree --depth stops the walk', () => {
    const tree = cliJson('topic-tree', db, 'Manasseh', '--depth=1');
    assert.strictEqual(tree.children.length, 3);
    assert.deepStrictEqual(tree.children.map(c => c.children), [[], [], []]);
  });

  test('topic reports breadcrumb, children and verse refs', () => {
    const topic = cliJson('topic', db, 'Tribe of');
    assert.deepStrictEqual(topic.breadcrumb.map(b => b.name), ['Manasseh']);
    assert.deepStrictEqual(topic.children.map(c => c.name), ['Inheritance of']);
    const child = cliJson('topic', db, 'Inheritance of');
    assert.deepStrictEqual(child.verses.map(v => v.ref), ['Josh 17:1-Josh 17:2']);
  });

  test('topics lists roots, then the children of --parent', () => {
    assert.deepStrictEqual(cliJson('topics', db).map(r => r.name), ['Israel', 'Manasseh']);
    assert.deepStrictEqual(
      cliJson('topics', db, '--parent=Manasseh').map(r => r.name),
      ['Son of Joseph and Asenath', 'Tribe of', 'King of Judah']
    );
  });

  test('topics reports child and verse counts', () => {
    const kids = cliJson('topics', db, '--parent=Manasseh');
    assert.strictEqual(kids.find(k => k.name === 'Son of Joseph and Asenath').verse_count, 1);
    assert.strictEqual(kids.find(k => k.name === 'Tribe of').child_count, 1);
  });

  test('topic-verses returns direct refs, --recursive adds descendants', () => {
    assert.deepStrictEqual(cliJson('topic-verses', db, 'Manasseh').map(v => v.ref), []);
    assert.deepStrictEqual(
      cliJson('topic-verses', db, 'Manasseh', '--recursive').map(v => v.ref),
      ['Gen 41:50', 'Josh 17:1-Josh 17:2']
    );
  });

  test('verse-topics finds a topic through a verse range', () => {
    // Josh 17:2 is only reachable via the 17:1-17:2 range on "Inheritance of".
    const rows = cliJson('verse-topics', db, 'Josh 17:2');
    assert.deepStrictEqual(rows.map(r => r.name), ['Inheritance of']);
    assert.strictEqual(rows[0].parent_name, 'Tribe of');
  });

  test('stats counts topics and verse associations', () => {
    const stats = cliJson('stats', db);
    assert.strictEqual(stats.type, 'topical');
    assert.strictEqual(stats.total_topics, 7);
    assert.strictEqual(stats.root_topics, 2);
    assert.strictEqual(stats.verse_associations, 2);
  });

  test('search matches topics through FTS', () => {
    const hits = cliJson('search', db, 'Manasseh');
    assert.deepStrictEqual(hits.map(h => h.topic_id).sort(), [2, 3]);
  });
}

async function main() {
  const topical = await buildDb('topical_fix.db', topicalStatements());
  const bible = await buildDb('bible_fix.db', bibleStatements());
  const xref = await buildDb('xref_fix.db', xrefStatements());
  const book = await buildDb('book_fix.db', bookStatements());

  console.log('\ntopical commands (topic + verse_link)');
  topicalCases(topical);

  console.log('\ntopic-tree default rendering');

  test('renders an indented, numbered tree rather than nested JSON', () => {
    const lines = cli('topic-tree', topical, 'Manasseh', '--depth=5').split(/\r?\n/);
    assert.ok(/^Manasseh {2}\(#3\)/.test(lines[0]), `unexpected header: ${lines[0]}`);
    assert.ok(lines.some(l => /^ {2}2\. Tribe of/.test(l)), 'sense 2 not at the top level');
    assert.ok(lines.some(l => /^ {6}1\. Inheritance of/.test(l)), 'sub-point not nested');
  });

  console.log('\nbible commands');

  test('verse reads `text`', () => {
    assert.deepStrictEqual(
      cliJson('verse', bible, 'John 3:16').map(r => r.text),
      ['For God so loved the world']
    );
  });

  test('verse-lookup resolves numeric ids', () => {
    const rows = cliJson('verse-lookup', bible, '43003016');
    assert.strictEqual(rows[0].ref, 'John 3:16');
    assert.strictEqual(rows[0].text, 'For God so loved the world');
  });

  console.log('\ncross-references and book sections');

  test('xref reads targets from verse_link, ranges included', () => {
    const rows = cliJson('xref', xref, 'John 3:16');
    assert.deepStrictEqual(rows.map(r => r.to), ['Rom 5:8', '2Cor 5:19-2Cor 5:21']);
    assert.strictEqual(rows[0].from, 'John 3:16');
    assert.strictEqual(rows[0].phrase, 'For God so loved');
  });

  test('stats counts xref groups and their verse_link targets', () => {
    const stats = cliJson('stats', xref);
    assert.strictEqual(stats.type, 'xref');
    assert.strictEqual(stats.group_count, 1);
    assert.strictEqual(stats.reference_count, 2);
  });

  test('section lists scripture references from verse_link', () => {
    const section = cliJson('section', book, '1');
    assert.deepStrictEqual(section.scripture_references.map(r => r.ref), ['John 3:16']);
  });

  console.log(`\n${passed} passed, ${failed} failed\n`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch(err => {
  console.error('Fatal:', err);
  process.exit(1);
});
