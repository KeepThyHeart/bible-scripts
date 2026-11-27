#!/bin/bash
# Start, stop and check the KJV render jobs. Each job owns a fixed set of books, so jobs never
# render the same chapter; each is resumable (finished chapters are skipped).
#   bible-job.sh start JOB          run JOB in the foreground (run it in the background yourself)
#   bible-job.sh stop JOB [now]     stop after the chapter in progress (or at once with "now")
#   bible-job.sh status             every job: running or not, chapter in progress, done/total
# Jobs: nt (Matthew-Revelation), ot (Psalms, Proverbs, then Genesis-2 Kings), ot2 (Ecclesiastes-Malachi),
# ot3 (Job, then Esther back to 1 Chronicles); 2 Kings 1-12 is ot's, 13-25 ot3's.
# VOICE=harrell60 (default) or jim60 etc. picks blends/VOICE.pt and kjv-VOICE/.
V=~/bible-audio/voices
VOICE=${VOICE:-harrell60}
OUT=$V/kjv-$VOICE
J=$OUT/jobs
mkdir -p "$J"
books(){ case $1 in
  nt) seq 40 66 | tr '\n' ' ';;
  ot) echo "19 20 $(seq 1 11 | tr '\n' ' ') 12:1-12";;
  ot3) echo "18 17 16 15 14 13 12:13-25";;
  ot2) seq 21 39 | tr '\n' ' ';;
  *) echo "unknown job $1" >&2; exit 2;; esac; }
desc(){ for c in $(pgrep -P $1); do echo $c; desc $c; done; }
renderer(){ # the chapters.py process started by job pid $1
  for q in $(desc $1); do grep -q chapters.py /proc/$q/cmdline 2>/dev/null && { echo $q; return; }; done; }
current(){ # chapter in progress: first chapter argument of pid $1 not on disk
  for a in $(tr '\0' ' ' < /proc/$1/cmdline 2>/dev/null); do
    [[ $a =~ ^([0-9]+)\.([0-9]+)$ ]] || continue
    s=$(printf "%s/kjv-%02d-%03d" $OUT ${BASH_REMATCH[1]} ${BASH_REMATCH[2]})
    # done = ogg written and its raw .f32 removed (the .ogg exists while ffmpeg is still encoding)
    [ -f $s.ogg ] && [ ! -f $s.f32 ] || { echo $a; return; }
  done
}
case $1 in
start)
  b=$(books $2) || exit 2
  if [ -f "$J/$2.pid" ] && [ -d /proc/$(cat "$J/$2.pid") ]; then echo "$2 already running"; exit 1; fi
  echo $$ > "$J/$2.pid"
  BOOKS="$b" TAG=$2 bash $V/tools/render-bible.sh $V/blends/$VOICE.pt "$OUT" 1
  rm -f "$J/$2.pid";;
stop)
  [ -f "$J/$2.pid" ] || { echo "$2 not running"; exit 0; }
  w=$(cat "$J/$2.pid"); p=$(renderer $w)
  if [ -z "$p" ]; then kill $w 2>/dev/null; rm -f "$J/$2.pid"; echo "$2 stopped"; exit 0; fi
  if [ "$3" != now ]; then c=$(current $p); echo "$2: stopping after $c"; while [ "$(current $p)" = "$c" ] && [ -d /proc/$p ]; do sleep 5; done; fi
  c=$(current $p); kill $w $p 2>/dev/null; rm -f "$J/$2.pid"
  [[ $c =~ ^([0-9]+)\.([0-9]+)$ ]] && rm -f "$(printf "%s/kjv-%02d-%03d.f32" $OUT ${BASH_REMATCH[1]} ${BASH_REMATCH[2]})"
  echo "$(date '+%F %H:%M') $2 stopped by request" >> "$OUT/progress.log"; echo "$2 stopped";;
status)
  python3 - "$OUT" <<'EOF'
import glob, json, os, sys
out = sys.argv[1]
ch = {}
for l in open(os.path.expanduser('~/bible-audio/prosody/work/kjv-phonemes.jsonl')):
    r = json.loads(l); ch.setdefault(r['book'], set()).add(r['chapter'])
have = {os.path.basename(f) for f in glob.glob(out + '/kjv-*.ogg')}
for job, bs in (('nt', range(40, 67)), ('ot', [19, 20] + list(range(1, 12)) + [(12, 1, 12)]), ('ot2', range(21, 40)), ('ot3', [18, 17, 16, 15, 14, 13, (12, 13, 25)])):
    allc = [(b, c) for e in bs for b, lo, hi in [e if isinstance(e, tuple) else (e, 0, 999)] for c in sorted(ch[b]) if lo <= c <= hi]
    done = [x for x in allc if f'kjv-{x[0]:02d}-{x[1]:03d}.ogg' in have]
    nxt = next((x for x in allc if f'kjv-{x[0]:02d}-{x[1]:03d}.ogg' not in have), None)
    pf = f'{out}/jobs/{job}.pid'
    run = os.path.exists(pf) and os.path.isdir('/proc/' + open(pf).read().strip())
    print(f'{job}: {"running" if run else "stopped"}, {len(done)}/{len(allc)} chapters, next {nxt}')
EOF
  ;;
*) sed -n 2,9p "$0";;
esac
