#!/usr/bin/env bash
# Fit a Kokoro voice pack to one LibriVox reader, end to end.
#
#   tools/fit_reader.sh NAME TEXT_URL_OR_FILE MP3_URL [MP3_URL ...]
#   tools/fit_reader.sh NAME - MP3_URL ...           # no source text (whisper only, stricter drops)
#   per-file texts: MP3_URL::TEXT_URL_OR_FILE overrides the default text for that file
#
# Env: DEBREATH=1 (silence breaths in pauses before training), HOURS=1.0 (fit budget; fit.py also stops early after 250 steps without held-out gain),
#      WHISPER=base.en, JOBS=3 (parallel whisper processes), THREADS=6, STAGE=0 (resume from a stage:
#      1 transcribe, 2 debreath, 3 prep, 4 init, 5 fit, 6 eval, 7 render+report).
# Only use recordings that the LibriVox catalog page credits to this one reader (group projects
# mix readers; archive.org file names carry the AUTHOR's name, not the reader's).
set -euo pipefail
NAME=$1; TEXT=$2; shift 2
V=~/bible-audio/voices; T=$V/tools; W=$V/work/$NAME; PY=~/bible-audio/prosody/venv/bin/python; WPY=$V/wvenv/bin/python
HOURS=${HOURS:-1.0}; WHISPER=${WHISPER:-base.en}; JOBS=${JOBS:-3}; THREADS=${THREADS:-6}; STAGE=${STAGE:-0}
mkdir -p $V/src/$NAME $V/src/texts $W/wav $W/tx $V/out
fetch() { case "$1" in http*) curl -4 -L -sS -f -m 900 -o "$2" "$1";; *) cp "$1" "$2";; esac; }

ITEMS=()
if [ "$STAGE" -le 0 ]; then
  echo '[]' > $W/sources.json
  [ "$TEXT" != "-" ] && fetch "$TEXT" $V/src/texts/$NAME-default.txt
fi
for u in "$@"; do
  url=${u%%::*}; b=$(basename "$url" .mp3); txt=$V/src/texts/$NAME-default.txt
  if [[ "$u" == *::* ]]; then txt=$V/src/texts/$NAME-$b.txt; [ "$STAGE" -le 0 ] && fetch "${u#*::}" "$txt"; fi
  [ "$TEXT" = "-" ] && [[ "$u" != *::* ]] && txt=""
  if [ "$STAGE" -le 0 ]; then
    [ -f $V/src/$NAME/$b.mp3 ] || fetch "$url" $V/src/$NAME/$b.mp3
    ffmpeg -loglevel error -y -i $V/src/$NAME/$b.mp3 -ac 1 -ar 24000 $W/wav/$b.wav
    python3 -I -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d.append(dict(url=sys.argv[2], text=sys.argv[3])); json.dump(d,open(p,'w'),indent=1)" $W/sources.json "$url" "$txt"
  fi
  ITEMS+=("$b|$txt")
done
WAVS=(); for it in "${ITEMS[@]}"; do WAVS+=($W/wav/${it%%|*}.wav); done

if [ "$STAGE" -le 1 ]; then  # transcribe, JOBS files at a time
  printf '%s\n' "${WAVS[@]}" | xargs -P $JOBS -I{} $WPY -I $T/transcribe.py $W/tx {} --model $WHISPER --threads 2
fi
CLEAN=()
if [ "${DEBREATH:-0}" = 1 ]; then
  if [ "$STAGE" -le 2 ]; then
    args=(); for it in "${ITEMS[@]}"; do b=${it%%|*}; args+=(--item $W/wav/$b.wav:$W/tx/$b.json); done
    $PY -I $T/debreath.py $W/clean "${args[@]}" --clips 8
    WTHREADS=$THREADS $WPY -I $T/debreath_check.py $NAME $W --n 40   # whisper check of the cuts + before/after pair
    $T/plog.sh $NAME debreath 25 "SAMPLE $NAME-debreath-before.ogg / $NAME-debreath-after.ogg | breath-removal pair; cut check in $W/clean/check.json"
  fi
  CLEAN=(--clean-dir $W/clean)
fi
if [ "$STAGE" -le 3 ]; then
  args=(); for it in "${ITEMS[@]}"; do b=${it%%|*}; t=${it#*|}; args+=(--item $W/wav/$b.wav:$W/tx/$b.json${t:+:$t}); done
  rm -rf $W/prep; $PY -I $T/prep.py $W/prep "${args[@]}" "${CLEAN[@]}"
  $PY -I $T/finalize.py $NAME $W --sample-only && $T/plog.sh $NAME correct 30 "SAMPLE $NAME-reader-sample.ogg | ~20 s of the original reader (body text$([ "${DEBREATH:-0}" = 1 ] && echo ", de-breathed"))"
fi
[ "$STAGE" -le 4 ] && $PY -I $T/init_voice.py $W/prep $W/init.json --threads $THREADS
INIT=$(python3 -I -c "import json,sys; print(json.load(open(sys.argv[1]))['init'])" $W/init.json)
[ "$STAGE" -le 5 ] && $PY -I $T/fit.py $W/prep "$INIT" $W/fit --hours $HOURS --threads $THREADS --name $NAME
if [ "$STAGE" -le 6 ]; then
  $PY -I $T/evaluate.py $W/prep $W/eval "stock=am_michael:0.5+bm_george:0.5" "init=$INIT" "fitted=$W/fit/pack-best.pt" "fitted_x0.6=back:0.6:$W/fit/pack-best.pt:$INIT" "last=$W/fit/pack.pt" --threads $THREADS
  WTHREADS=$THREADS $WPY -I $T/wer.py $W/eval/wer-list.json $W/eval/wer.json
fi
if [ "$STAGE" -le 7 ]; then
  $PY -I $T/finalize.py $NAME $W --pack $W/fit/pack-best.pt
  $PY -I $T/render.py $V/out/$NAME-john3-16.ogg --pack $V/$NAME.pt
  $PY -I $T/render.py $V/out/$NAME-john3-16-shaped.ogg --pack $V/$NAME.pt --local 0.6 --trend-ms 400 --en-range 0.7 --level 0.5
  [ -f $V/out/stock-john3-16.ogg ] || $PY -I $T/render.py $V/out/stock-john3-16.ogg --voice 'am_michael:0.5+bm_george:0.5'
  # render.py loudness-matches every file to the stock level (-26.6 dB RMS) by default
  WTHREADS=$THREADS $WPY -I $T/check_outputs.py $NAME
  $T/plog.sh $NAME render 100 "SAMPLE $NAME-john3-16.ogg / $NAME-john3-16-shaped.ogg | final John 3:16, raw and shaped; checks in $NAME-report.json"
  # If eval.json shows fitted_x0.6 close to fitted, or the renders sound rough, re-run stage 7 with a
  # backed-off pack instead: finalize.py ... --pack <a pack saved from init + 0.6*(best - init)>.
fi
