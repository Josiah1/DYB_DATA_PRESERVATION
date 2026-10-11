#!/usr/bin/env bash
set -euo pipefail

[ "$#" -eq 3 ] || { echo "usage: worker.sh RUN_DIR PROC_ID CODE_ROOT" >&2; exit 2; }
RUN_DIR=$1
PROC_ID=$2
CODE_ROOT=$3
[[ "$PROC_ID" =~ ^[0-9]+$ ]] || { echo "invalid ProcId: $PROC_ID" >&2; exit 2; }
printf -v TASK_ID '%06d' "$PROC_ID"

STATUS_DIR="$RUN_DIR/status"
CHUNK_LIST="$RUN_DIR/chunks/$TASK_ID.list"
OUTPUT_PREFIX=$(<"$RUN_DIR/output-prefix")
RUN_NUMBER=$(<"$RUN_DIR/run-number")
AD_SELECTION=$(<"$RUN_DIR/ad-selection")
OUTPUT="$RUN_DIR/outputs/$OUTPUT_PREFIX.slim.$TASK_ID.root"
SOURCE_REPORT="$RUN_DIR/reports/$OUTPUT_PREFIX.slim.$TASK_ID.sources.tsv"
RUNNING="$STATUS_DIR/$TASK_ID.running"
OK="$STATUS_DIR/$TASK_ID.ok"
FAIL="$STATUS_DIR/$TASK_ID.fail"
REPORT="$STATUS_DIR/$TASK_ID.report"
LCG_VIEW=${LCG_VIEW:-/cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt}

mkdir -p "$STATUS_DIR" "$RUN_DIR/outputs" "$RUN_DIR/reports"
rm -f -- "$FAIL"
printf 'host=%s job=%s started=%s\n' "$(hostname)" "${_CONDOR_IHEP_JOB_ID:-unknown}" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUNNING"

finish() {
  rc=$?
  rm -f -- "$RUNNING"
  if [ "$rc" -ne 0 ]; then
    {
      printf 'rc=%s\n' "$rc"
      printf 'host=%s\n' "$(hostname)"
      printf 'job=%s\n' "${_CONDOR_IHEP_JOB_ID:-unknown}"
      printf 'finished=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$FAIL.tmp.$$"
    mv -f "$FAIL.tmp.$$" "$FAIL"
  fi
  exit "$rc"
}
trap finish EXIT

[ -r "$CHUNK_LIST" ] || { echo "missing chunk: $CHUNK_LIST" >&2; exit 3; }
[ -x "$CODE_ROOT/bin/slim-root" ] || { echo "missing converter" >&2; exit 4; }

if [ ! -s "$CHUNK_LIST" ]; then
  env -u PYTHONHOME -u PYTHONPATH /usr/bin/python3 "$CODE_ROOT/preserve.py" _check_chunk --run-dir "$RUN_DIR" \
    --stage-root "$(dirname "$(dirname "$(dirname "$RUN_DIR")")")" --chunk "$PROC_ID" >/dev/null
  printf 'EMPTY confirmed_broken_inputs\n' > "$REPORT"
else
set +u
source "$LCG_VIEW/setup.sh"
set -u

if [ -f "$OUTPUT" ] && [ -s "$SOURCE_REPORT" ] && \
   "$CODE_ROOT/bin/slim-root" --verify "$OUTPUT" >/dev/null; then
  env -u PYTHONHOME -u PYTHONPATH /usr/bin/python3 "$CODE_ROOT/preserve.py" _check_chunk --run-dir "$RUN_DIR" \
    --stage-root "$(dirname "$(dirname "$(dirname "$RUN_DIR")")")" --chunk "$PROC_ID" >/dev/null
  [ ! -s "$OK" ] || exit 0
  printf 'SKIP output=%s\n' "$OUTPUT" > "$REPORT"
else
  rm -f -- "$OUTPUT" "$SOURCE_REPORT"
  "$CODE_ROOT/bin/slim-root" --input-list "$CHUNK_LIST" --output "$OUTPUT" \
    --source-report "$SOURCE_REPORT" \
    --run-number "$RUN_NUMBER" --ad-selection "$AD_SELECTION" > "$REPORT"
  "$CODE_ROOT/bin/slim-root" --verify "$OUTPUT" >> "$REPORT"
fi
fi

{
  cat "$REPORT"
  printf 'host=%s\n' "$(hostname)"
  printf 'job=%s\n' "${_CONDOR_IHEP_JOB_ID:-unknown}"
  printf 'finished=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OK.tmp.$$"
mv -f "$OK.tmp.$$" "$OK"
rm -f -- "$FAIL"
