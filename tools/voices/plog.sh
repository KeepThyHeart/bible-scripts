#!/usr/bin/env bash
# plog.sh VOICE STAGE PCT NOTE...  -> one line in ~/bible-audio/voices/progress.log
v=$1; st=$2; p=$3; shift 3
echo "$(date '+%Y-%m-%d %H:%M') | $v | $st | $p% | $*" >> ~/bible-audio/voices/progress.log
