#!/bin/bash
# Test script for converting Webster's 1828 Dictionary

set -e

echo "Converting Webster's 1828 Dictionary module..."
echo ""

./sword2dictionary --input ../test/data/Webster1828.zip --output dictionary_webster.db

echo ""
echo "Conversion complete!"
echo ""
echo "To inspect the database, run: make inspect"
echo "Or use: python3 -c \"import sqlite3; db=sqlite3.connect('dictionary_webster.db'); ...\""
