/**
 * Output formatting for command results.
 */

function output(data, format) {
  if (format === 'count') {
    console.log(Array.isArray(data) ? data.length : (data ? 1 : 0));
    return;
  }
  if (format === 'pretty') {
    console.log(JSON.stringify(data, null, 2));
    return;
  }
  if (format === 'table') {
    if (Array.isArray(data) && data.length > 0) {
      const cols = Object.keys(data[0]);
      // Calculate column widths
      const widths = cols.map(c => Math.max(c.length, ...data.map(r => String(r[c] ?? '').length)));
      // Cap widths at 80 to keep table readable
      const maxWidth = 80;
      const cappedWidths = widths.map(w => Math.min(w, maxWidth));
      // Header
      console.log(cols.map((c, i) => c.padEnd(cappedWidths[i])).join('  '));
      console.log(cappedWidths.map(w => '─'.repeat(w)).join('──'));
      // Rows
      for (const row of data) {
        console.log(cols.map((c, i) => {
          const val = String(row[c] ?? '');
          return val.length > cappedWidths[i]
            ? val.substring(0, cappedWidths[i] - 1) + '…'
            : val.padEnd(cappedWidths[i]);
        }).join('  '));
      }
      console.log(`\n(${data.length} rows)`);
    } else if (data && !Array.isArray(data)) {
      // Single object
      for (const [k, v] of Object.entries(data)) {
        console.log(`${k}: ${v}`);
      }
    } else {
      console.log('(no results)');
    }
    return;
  }
  // Default: compact JSON
  console.log(JSON.stringify(data));
}

module.exports = { output };
