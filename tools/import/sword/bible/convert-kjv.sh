#!/bin/bash
# convert-kjv.sh - Test script to convert KJV.zip to bible_kjv.db

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================="
echo "SWORD Bible Module Converter Test"
echo "=================================="
echo ""

# Check if converter is built
if [ ! -f "./sword2bible" ]; then
    echo "Converter not found. Building..."
    make
    echo ""
fi

# Check if KJV.zip exists
if [ ! -f "../test/data/KJV.zip" ]; then
    echo "Error: ../test/data/KJV.zip not found in current directory"
    echo "Please download a SWORD module ZIP file first"
    exit 1
fi

# Remove old database if it exists
if [ -f "bible_kjv.db" ]; then
    echo "Removing existing bible_kjv.db..."
    rm bible_kjv.db
    echo ""
fi

# Run conversion
echo "Converting ../test/data/KJV.zip to bible_kjv.db..."
echo ""
./sword2bible --input ../test/data/KJV.zip --output bible_kjv.db

# Check if conversion succeeded
if [ $? -eq 0 ]; then
    echo ""
    echo "=================================="
    echo "Conversion Successful!"
    echo "=================================="
    echo ""

    # Display database info
    echo "Database Information:"
    echo "---------------------"
    sqlite3 bible_kjv.db <<EOF
.mode line
SELECT 'Abbreviation: ' || abbreviation ||
       '\nFull Name: ' || full_name ||
       '\nLanguage: ' || language_code ||
       '\nContent version: ' || COALESCE(content_version, '(none)') ||
       '\nFormat: ' || format || ' v' || format_version ||
       '\nModule UUID: ' || module_uuid ||
       '\nVersification: ' || versification ||
       '\nLicence: ' || COALESCE(license_spdx, '(unknown)') ||
       '\nCopyright: ' || COALESCE(copyright, '')
FROM module_info;
EOF

    echo ""
    echo "Statistics:"
    echo "-----------"
    sqlite3 bible_kjv.db <<EOF
SELECT 'Total Verses: ' || COUNT(*) FROM bible_verse;
EOF

    echo ""
    echo "Sample Verses (Genesis 1:1-3):"
    echo "------------------------------"
    sqlite3 bible_kjv.db <<EOF
.mode line
SELECT verse_id || ': ' || text FROM bible_verse
WHERE verse_id BETWEEN 1001001 AND 1001003;
EOF

    echo ""
    echo "Sample formatting spans (Psalm 23:1):"
    echo "-------------------------------------"
    sqlite3 bible_kjv.db <<EOF
.mode line
SELECT text, formatting FROM bible_verse WHERE verse_id = 19023001;
EOF

    echo ""
    echo "File size:"
    ls -lh bible_kjv.db | awk '{print $5 " - " $9}'

    echo ""
    echo "✓ Test completed successfully!"
    echo ""
    echo "You can now inspect the database:"
    echo "  sqlite3 bible_kjv.db"
    echo ""
else
    echo ""
    echo "✗ Conversion failed!"
    exit 1
fi
