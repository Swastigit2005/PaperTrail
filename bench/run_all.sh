#!/usr/bin/env bash
# Run (or resume) the benchmark configs for one extraction pipeline, interleaving packs in steps
# so a partially used daily quota still yields results for both packs.
#   bench/run_all.sh vlm      # vision model: vlm_llm, then vlm_rules (reuses cached extractions)
#   bench/run_all.sh ocr      # OCR + text LLM: ocr_llm
#   bench/run_all.sh oracle   # ground-truth extraction: oracle_rules, oracle_llm
# Exit code 3 = a daily Groq quota ran out; re-run the same command after it resets.
set -u
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
export PAPERTRAIL_MAX_WAIT=${PAPERTRAIL_MAX_WAIT:-300}

case "${1:-}" in
  vlm) configs="vlm_llm vlm_rules" ;;
  vlmb) configs="vlmb_llm" ;;
  ocr) configs="ocr_llm" ;;
  oracle) configs="oracle_rules oracle_llm" ;;
  *) echo "usage: $0 vlm|vlmb|ocr|oracle" >&2; exit 2 ;;
esac

for config in $configs; do
  for limit in 15 30 45 60; do
    for pack in claims payables; do
      $PY -u -m bench.run_benchmark --pack "$pack" --config "$config" --limit "$limit"
      code=$?
      if [ $code -eq 3 ]; then echo "quota exhausted during $pack/$config; resume later"; exit 3; fi
      if [ $code -ne 0 ]; then echo "failed: $pack/$config (exit $code)"; exit $code; fi
    done
  done
done
echo "all done: $configs"
