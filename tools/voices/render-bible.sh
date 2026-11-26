#!/bin/bash
# Render the KJV in one voice, two workers, resumable: chapters already on disk are skipped, so
# re-running continues where it stopped. Order: New Testament (Mark..Revelation; Matthew is done),
# then Psalms, Proverbs, then Genesis onward.
#   render-bible.sh PACK OUTDIR [WORKERS]
PACK=${1:-$HOME/bible-audio/voices/blends/harrell60.pt}
OUT=${2:-$HOME/bible-audio/voices/kjv-harrell60}
N=${3:-2}
V=~/bible-audio/voices
mkdir -p "$OUT"
ORDER="$(seq 40 66) 19 20 $(seq 1 18) $(seq 21 39)"
LIST=$(python3 - "$OUT" $ORDER <<'EOF'
import json, os, sys
out, order = sys.argv[1], [int(b) for b in sys.argv[2:]]
chaps = {}
for l in open(os.path.expanduser('~/bible-audio/prosody/work/kjv-phonemes.jsonl')):
    r = json.loads(l); chaps.setdefault(r['book'], set()).add(r['chapter'])
todo = [f'{b}.{c}' for b in order for c in sorted(chaps.get(b, ()))
        if not os.path.exists(os.path.join(out, f'kjv-{b:02d}-{c:03d}.ogg'))]
print(' '.join(todo))
EOF
)
set -- $LIST
echo "$(date '+%F %H:%M') start: $# chapters to render, $N workers" >> "$OUT/progress.log"
cd ~/bible-audio/prosody
for w in $(seq 0 $((N - 1))); do
  mine=(); i=0
  for c in "$@"; do [ $((i % N)) = $w ] && mine+=("$c"); i=$((i + 1)); done
  [ ${#mine[@]} = 0 ] && continue
  (env SUPP=1 INTRO_MS=1300 END_MS=2500 THREADS=3 OY=Y PACK="$PACK" venv/bin/python -u -I $V/tools/chapters.py "$OUT" kjv 1100 2400 650 350 "${mine[@]}" >> "$OUT/w$w.log" 2>&1) &
done
wait
echo "$(date '+%F %H:%M') stopped: $(ls "$OUT"/kjv-*.ogg | wc -l) chapters on disk" >> "$OUT/progress.log"
