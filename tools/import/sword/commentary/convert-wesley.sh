#!/bin/bash
# Test script for converting Wesley commentary

set -e

echo "Converting Wesley commentary module..."
echo ""

./sword2commentary --input ../test/data/Wesley.zip --output commentary_wesley.db

echo ""
echo "Conversion complete!"
echo ""
echo "To inspect the database, run: make inspect"
echo "Or use: sqlite3 commentary_wesley.db"
