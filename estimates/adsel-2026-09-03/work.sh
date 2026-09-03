#!/usr/bin/env bash
# Measurement driver: compile helpers, count 48 sample sources, convert 12 with the
# deployed converter, and rewrite each slim chunk into selection variants.
set -euo pipefail
WORK=$1
cd "$WORK"
set +u
source /cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt/setup.sh
set -u
echo "START $(date -u +%FT%TZ) host=$(hostname)"
g++ -O2 -std=c++17 $(root-config --cflags) adsel_count.cpp -o adsel_count $(root-config --libs)
g++ -O2 -std=c++17 $(root-config --cflags) adsel_filter.cpp -o adsel_filter $(root-config --libs)
CONV=/dybfs2/users/lijj16/DYB_DATA_PRESERVATION/code/bin/slim-root
"$CONV" --version
mkdir -p slim filt
split -n l/4 -d sample48.list part_
for p in part_0*; do
  ( ./adsel_count "$p" > "count.$p.tsv" 2> "count.$p.log" ) &
done
while IFS=$'\t' read -r tag period hall run path; do
  echo "CONVERT $tag $period $hall $run $(date -u +%T)"
  if ! "$CONV" --input-list "$tag.list" --output "slim/$tag.root" \
       --source-report "slim/$tag.sources.tsv" --run-number "$run" > "slim/$tag.result" 2>&1; then
    echo "CONVERT FAILED $tag"; cat "slim/$tag.result"; continue
  fi
  cat "slim/$tag.result"
  ./adsel_filter "slim/$tag.root" filt "$tag" > "filt/$tag.tsv" 2> "filt/$tag.log" || echo "FILTER FAILED $tag"
  cat "filt/$tag.tsv"
done < convert.tsv
wait
cat count.part_*.tsv | awk 'NR==1 || !/^path\t/' > counts.tsv
echo "END $(date -u +%FT%TZ)"
touch DONE
