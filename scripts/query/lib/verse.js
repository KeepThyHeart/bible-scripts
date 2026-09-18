/**
 * Verse ID utilities: `verse_id` = book * 1000000 + chapter * 1000 + verse.
 */

const { BOOKS } = require('./books');

function verseId(bookNum, chapter, verse) {
  return (bookNum * 1000000) + (chapter * 1000) + verse;
}

function parseVerseId(vid) {
  const bookNum = Math.floor(vid / 1000000);
  const remainder = vid % 1000000;
  const chapter = Math.floor(remainder / 1000);
  const verse = remainder % 1000;
  return { bookNum, chapter, verse };
}

function formatVerseId(vid) {
  const { bookNum, chapter, verse } = parseVerseId(vid);
  const book = BOOKS[bookNum];
  if (!book) return `Unknown(${vid})`;
  return `${book.abbr} ${chapter}:${verse}`;
}

/** Render an inclusive verse range, collapsing single-verse ranges. */
function formatVerseRange(start, end) {
  return end == null || end === start
    ? formatVerseId(start)
    : `${formatVerseId(start)}-${formatVerseId(end)}`;
}

/**
 * Book number for a name, abbreviation or alias (case and spacing ignored),
 * or null when nothing matches.
 */
function findBookNumber(name) {
  const key = name.replace(/\s+/g, '').toLowerCase();
  for (let i = 1; i < BOOKS.length; i++) {
    const b = BOOKS[i];
    const names = [
      b.name.replace(/\s+/g, '').toLowerCase(),
      b.abbr.toLowerCase(),
      ...b.aliases,
    ];
    if (names.includes(key)) return i;
  }
  return null;
}

/**
 * Parse a human-readable reference string into verse ID range.
 * Supports: "John 3:16", "Gen 1:1-5", "Ps 23", "1Cor 13:4-7", "Rev 22"
 * Returns: { start: verseId, end: verseId }
 */
function parseReference(refStr) {
  const ref = refStr.trim();

  // Match: optional number prefix + book name + chapter[:verse[-endverse]]
  const m = ref.match(/^(\d?\s*[A-Za-z]+(?:\s+of\s+[A-Za-z]+)?)\s+(\d+)(?::(\d+)(?:\s*-\s*(\d+))?)?$/);
  if (!m) {
    throw new Error(`Cannot parse reference: "${refStr}". Expected format: "Book Chapter:Verse" (e.g., "John 3:16", "Gen 1:1-5")`);
  }

  const chapter = parseInt(m[2], 10);
  const startVerse = m[3] ? parseInt(m[3], 10) : null;
  const endVerse = m[4] ? parseInt(m[4], 10) : null;

  const bookNum = findBookNumber(m[1]);
  if (!bookNum) {
    throw new Error(`Unknown book: "${m[1]}". Use book name or abbreviation (e.g., Gen, John, 1Cor).`);
  }

  if (startVerse !== null) {
    const start = verseId(bookNum, chapter, startVerse);
    const end = endVerse !== null ? verseId(bookNum, chapter, endVerse) : start;
    return { bookNum, chapter, startVerse, endVerse: endVerse || startVerse, start, end };
  }

  // Whole chapter
  const start = verseId(bookNum, chapter, 1);
  const end = verseId(bookNum, chapter, 999);
  return { bookNum, chapter, startVerse: 1, endVerse: 999, start, end };
}

module.exports = { verseId, parseVerseId, formatVerseId, formatVerseRange, findBookNumber, parseReference };
