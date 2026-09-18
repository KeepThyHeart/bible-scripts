/**
 * Where the Bible app's data and schema live.
 *
 * These scripts operate on the Bible app's databases but do not live in its
 * repo. By default they look for a sibling checkout (`../bible` next to this
 * repo); point them elsewhere with environment variables:
 *
 *   BIBLE_REPO         root of the Bible repo (default: ../bible)
 *   BIBLE_DATA_DIR     directory holding main.db and modules/
 *                      (default: <BIBLE_REPO>/apps/desktop/data)
 *   BIBLE_MODULES_DIR  module databases (default: <BIBLE_DATA_DIR>/modules)
 *   BIBLE_MAIN_DB      main.db (default: <BIBLE_DATA_DIR>/main.db)
 *
 * Scripts that accept explicit paths (--modules-dir, --input, ...) prefer those;
 * this only supplies the defaults.
 */

const path = require('path');

const SCRIPTS_REPO_ROOT = path.resolve(__dirname, '..', '..');

const BIBLE_REPO = path.resolve(process.env.BIBLE_REPO || path.join(SCRIPTS_REPO_ROOT, '..', 'bible'));
const DATA_DIR = process.env.BIBLE_DATA_DIR || path.join(BIBLE_REPO, 'apps', 'desktop', 'data');

module.exports = {
  BIBLE_REPO,
  DATA_DIR,
  MODULES_DIR: process.env.BIBLE_MODULES_DIR || path.join(DATA_DIR, 'modules'),
  MAIN_DB_PATH: process.env.BIBLE_MAIN_DB || path.join(DATA_DIR, 'main.db'),
  USERS_DIR: path.join(DATA_DIR, 'users'),
  KJV_VERSIFICATION_JSON: path.join(SCRIPTS_REPO_ROOT, 'scripts', 'data', 'kjv-versification.json'),
};
