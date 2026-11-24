#!/bin/bash
# Convert John Gill's Exposition (SWORD/Xiphos Gill.zip, v1.4 ThML zCom) with every
# source-defect repair it needs (task 0225):
#   --clean-html           <p>/<strong>/<em>; scripture references kept as text
#   --strip-trailing-byte  Gill's index overstates each entry by one character (junk at the end)
# Usage: ./convert-gill.sh Gill.zip commentary_gill.db [content-version]
set -e
export BIBLE_REPO="${BIBLE_REPO:-../../../../../bible}"
./sword2commentary --input "${1:?Gill.zip}" --output "${2:?output.db}" \
    --clean-html --strip-trailing-byte --content-version "${3:-1.0.1}"
