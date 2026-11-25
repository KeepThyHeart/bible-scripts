'use strict';
// Run: node scripts/lib/module-identity.test.js
// Mirrors tools/import/sword/common/provenance_test.cpp (same wording).
const assert = require('assert');
const { buildProvenance, CROSSWIRE, hostOfUrl } = require('./module-identity');

const cw = buildProvenance('KJV', '3.1');
assert.deepStrictEqual(cw, {
  converted_from: 'sword',
  project: 'The SWORD Project',
  publisher: 'CrossWire Bible Society',
  publisher_url: 'https://crosswire.org/sword/',
  module: 'KJV',
  module_version: '3.1',
  attribution: 'Converted from the SWORD module "KJV" (version 3.1), distributed by the CrossWire Bible Society (crosswire.org).',
});

const xi = buildProvenance('Gill', '1.0', { name: 'Xiphos', url: 'https://www.xiphos.org/modules/' });
assert.strictEqual(xi.publisher, 'Xiphos');
assert.strictEqual(xi.attribution, 'Converted from the SWORD module "Gill" (version 1.0), distributed through Xiphos (xiphos.org).');

assert.strictEqual(hostOfUrl('https://www.xiphos.org/x'), 'xiphos.org');
assert.strictEqual(buildProvenance('X', '', CROSSWIRE).attribution.includes('(version'), false);
console.log('module-identity provenance tests passed');
