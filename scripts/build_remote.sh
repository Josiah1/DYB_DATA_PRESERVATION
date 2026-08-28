#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=${1:-$(cd "$(dirname "$0")/.." && pwd)}
LCG_VIEW=${LCG_VIEW:-/cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt}
mkdir -p "$PROJECT_ROOT/bin"
BUILD_PATH=$(mktemp "$PROJECT_ROOT/bin/slim-root.XXXXXX")
trap 'rm -f -- "$BUILD_PATH"' EXIT

set +u
source "$LCG_VIEW/setup.sh"
set -u

g++ -O3 -DNDEBUG -std=c++17 $(root-config --cflags) \
  "$PROJECT_ROOT/src/slim_root.cpp" -o "$BUILD_PATH" $(root-config --libs)
chmod 700 "$BUILD_PATH"
"$BUILD_PATH" --version
mv -f "$BUILD_PATH" "$PROJECT_ROOT/bin/slim-root"
