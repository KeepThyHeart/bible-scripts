/**
 * SQLite helpers: async wrappers around the `sqlite3` package (NOT
 * better-sqlite3), so the CLI runs under system Node.js without needing
 * Electron-compiled native modules. Databases are always opened read-only.
 */

const sqlite3 = require('sqlite3');

function openDb(dbPath) {
  return new Promise((resolve, reject) => {
    const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
      if (err) reject(new Error(`Cannot open database: ${dbPath}\n${err.message}`));
      else resolve(db);
    });
  });
}

function dbAll(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.all(sql, params, (err, rows) => {
      if (err) reject(err);
      else resolve(rows || []);
    });
  });
}

function dbGet(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.get(sql, params, (err, row) => {
      if (err) reject(err);
      else resolve(row || null);
    });
  });
}

function dbClose(db) {
  return new Promise((resolve) => {
    db.close(() => resolve());
  });
}

/** Open `dbPath`, run `fn(db)`, and close the database however `fn` ends. */
async function withDb(dbPath, fn) {
  const db = await openDb(dbPath);
  try {
    return await fn(db);
  } finally {
    await dbClose(db);
  }
}

module.exports = { openDb, dbAll, dbGet, dbClose, withDb };
