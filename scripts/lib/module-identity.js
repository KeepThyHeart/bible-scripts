'use strict';

/**
 * Shared identity, provenance and verse-linking helpers for module conversion.
 *
 * The module format is specified by the Bible repo
 * (packages/core/docs/features/module-format.md and packages/core/sql/schemas/).
 *
 * These must stay byte-compatible with the C++ implementation in
 * tools/import/sword/common/sword_common.cpp, so that a module converted by
 * either pipeline gets the SAME module_uuid and the same verse_link shape.
 */

const crypto = require('crypto');

/** The module format (spec) version emitted by these converters. */
const FORMAT_VERSION = '0.1';
/** The versification every module produced here conforms to. */
const VERSIFICATION = 'kjv-english';

/** Lowercase hex SHA-256. */
function sha256Hex(data) {
  return crypto.createHash('sha256').update(data, 'utf8').digest('hex');
}

/**
 * Derive a stable module UUID from an identity key.
 *
 * The key must contain only things that identify the WORK, never the revision,
 * so the UUID survives content updates (stable identity across
 * versions). Callers pass e.g. `topical_index:navetopics:en`.
 *
 * The result is an RFC 9562 version 8 (custom, hash-based) UUID. This mirrors
 * SwordCommon::deterministicUuid() exactly — same namespace prefix, same
 * truncation, same version/variant nibbles.
 */
function deterministicUuid(identityKey) {
  const digest = sha256Hex(`urn:bible-module:v0.1:${identityKey}`);
  const chars = digest.slice(0, 32).split('');

  // Version 8 (custom / hash-based).
  chars[12] = '8';

  // Variant bits: first hex digit of the 4th group must be 8, 9, a or b.
  const VARIANT = ['8', '9', 'a', 'b'];
  chars[16] = VARIANT[parseInt(chars[16], 16) & 0x3];

  const s = chars.join('');
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20, 32)}`;
}

/**
 * Strip RTF artefacts that SWORD .conf files embed in About/Copyright fields
 * (`\par`, `\pard`, `\qc`, escaped braces) and normalise whitespace.
 *
 * The shipped KJV's `description` is full of literal `\par` escapes; this is
 * what stops that being reproduced.
 */
function stripRtfArtifacts(text) {
  if (typeof text !== 'string' || text === '') return '';

  let out = '';
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '\\') {
      const next = text[i + 1];
      if (next === '\\' || next === '{' || next === '}') {
        out += next;
        i += 2;
        continue;
      }
      let j = i + 1;
      const wordStart = j;
      while (j < text.length && /[a-zA-Z]/.test(text[j])) j++;
      if (j > wordStart) {
        const word = text.slice(wordStart, j);
        if (text[j] === '-') j++;
        while (j < text.length && /[0-9]/.test(text[j])) j++;
        if (text[j] === ' ') j++;   // the control word's delimiter space
        if (word === 'par' || word === 'line' || word === 'pard' || word === 'tab') {
          out += ' ';
        }
        i = j;
        continue;
      }
      i++;                          // lone backslash
      continue;
    }
    if (ch === '{' || ch === '}') { i++; continue; }
    out += ch;
    i++;
  }

  return out.replace(/\s+/g, ' ').trim();
}

/**
 * Map a SWORD `DistributionLicense` value to an SPDX identifier (or a
 * LicenseRef- id where no SPDX id exists). Returns '' when unknown.
 * Mirrors SwordCommon::licenseToSpdx().
 */
function licenseToSpdx(distributionLicense) {
  const l = String(distributionLicense || '').trim().toLowerCase();
  if (l === '') return '';

  if (l.includes('public domain')) return 'PD';

  if (l.includes('creative commons') || l.includes('cc-by')) {
    let id = 'CC-BY';
    if (l.includes('noncommercial') || l.includes('non-commercial') || l.includes('-nc')) {
      id += '-NC';
    }
    if (l.includes('noderiv') || l.includes('-nd')) {
      id += '-ND';
    } else if (l.includes('sharealike') || l.includes('share alike') || l.includes('-sa')) {
      id += '-SA';
    }
    for (const v of ['4.0', '3.0', '2.5', '2.0', '1.0']) {
      if (l.includes(v)) return `${id}-${v}`;
    }
    return `${id}-4.0`;
  }

  if (l.includes('gpl')) {
    return l.includes('3') ? 'GPL-3.0-or-later' : 'GPL-2.0-or-later';
  }
  if (l.includes('free non-commercial') || l.includes('free noncommercial')) {
    return 'LicenseRef-SWORD-FreeNonCommercial';
  }
  if (l.includes('permission to distribute')) {
    return 'LicenseRef-SWORD-DistributionPermitted';
  }
  if (l.includes('copyright')) return 'LicenseRef-Proprietary';

  return '';
}

/** Canonical URL for a licence id produced by licenseToSpdx(). '' if none. */
function licenseUrlForSpdx(spdxId) {
  if (!spdxId) return '';
  if (spdxId === 'PD') return 'https://creativecommons.org/publicdomain/mark/1.0/';

  if (spdxId.startsWith('CC-')) {
    const lastDash = spdxId.lastIndexOf('-');
    if (lastDash < 3) return '';
    const code = spdxId.slice(3, lastDash).toLowerCase();
    const version = spdxId.slice(lastDash + 1);
    return `https://creativecommons.org/licenses/${code}/${version}/`;
  }

  if (spdxId === 'GPL-3.0-or-later') return 'https://www.gnu.org/licenses/gpl-3.0.html';
  if (spdxId === 'GPL-2.0-or-later') {
    return 'https://www.gnu.org/licenses/old-licenses/gpl-2.0.html';
  }
  return '';
}

/** Extract a four-digit year from free text ("1611-2011" -> 1611). 0 if none. */
function extractYear(text) {
  const match = /(?<!\d)(1\d{3}|2\d{3})(?!\d)/.exec(String(text || ''));
  return match ? parseInt(match[1], 10) : 0;
}

const VERSE_LINK_INSERT_SQL = `
  INSERT INTO verse_link (source_type, source_id, verse_id_start, verse_id_end, link_type, sort_order, context)
  VALUES (?, ?, ?, ?, ?, ?, ?)
`;

/**
 * Normalise a range for storage. Ranges are inclusive, and `verse_id_end` is
 * REQUIRED (NOT NULL): a single verse is `verse_id_end = verse_id_start`, never
 * NULL, so one containment query covers every row.
 */
function verseLinkEnd(startId, endId) {
  if (endId === null || endId === undefined) return startId;
  return endId;
}

module.exports = {
  FORMAT_VERSION,
  VERSIFICATION,
  sha256Hex,
  deterministicUuid,
  stripRtfArtifacts,
  licenseToSpdx,
  licenseUrlForSpdx,
  extractYear,
  VERSE_LINK_INSERT_SQL,
  verseLinkEnd,
};
