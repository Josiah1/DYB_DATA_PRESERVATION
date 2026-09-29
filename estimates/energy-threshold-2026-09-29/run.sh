#!/usr/bin/env bash
set -eo pipefail
unset PYTHONHOME PYTHONPATH
source /cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt/setup.sh
set -u
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
REPO=$(cd "$(dirname "$0")/../.." && pwd)
BENCH="$REPO/estimates/energy-threshold-2026-09-29/benchmark.py"
if [[ ${1:-} == build ]]; then
  python3 "$BENCH" build "$REPO/build"
else
  [[ ${1:-} =~ ^[0-9]+$ ]] && (( $1 < 48 ))
  printf -v SAMPLE '%03d' "$1"
  mkdir -p "$REPO/results"
  python3 "$BENCH" run "$REPO/build" --index "$1" > "$REPO/results/$SAMPLE.jsonl"
fi
