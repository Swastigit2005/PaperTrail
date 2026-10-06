#!/usr/bin/env bash
# Keep resuming the long-running jobs until everything is done, sleeping while a daily Groq quota
# is exhausted. Vision quota goes to the demo samples first, then the VLM benchmark; the OCR
# benchmark uses a different model (and quota) so it runs in parallel.
#   nohup bench/resume_loop.sh > bench/logs/loop.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
SLEEP=${SLEEP:-1800}

samples_done() {
  for pack in claims payables expenses; do
    for d in src/papertrail/packs/$pack/samples/*/; do
      [ -f "$d/result.json" ] || return 1
    done
  done
  return 0
}

while true; do
  echo "=== $(date '+%F %T') pass"
  ( bench/run_all.sh ocr > bench/logs/ocr.log 2>&1; echo $? > bench/logs/ocr.exit ) &
  (
    if ! samples_done; then $PY -u scripts/build_samples.py > bench/logs/samples.log 2>&1; fi
    if samples_done; then bench/run_all.sh vlm > bench/logs/vlm.log 2>&1; echo $? > bench/logs/vlm.exit
    else echo 3 > bench/logs/vlm.exit; fi
  ) &
  wait
  ocr=$(cat bench/logs/ocr.exit); vlm=$(cat bench/logs/vlm.exit)
  echo "ocr exit $ocr, vlm exit $vlm, samples $(samples_done && echo done || echo pending)"
  if [ "$ocr" = 0 ] && [ "$vlm" = 0 ] && samples_done; then break; fi
  if [ "$ocr" != 0 ] && [ "$ocr" != 3 ]; then echo "ocr failed"; fi
  if [ "$vlm" != 0 ] && [ "$vlm" != 3 ]; then echo "vlm failed"; fi
  sleep "$SLEEP"
done
$PY -m bench.report && $PY scripts/build_web.py
echo "=== $(date '+%F %T') all done"
