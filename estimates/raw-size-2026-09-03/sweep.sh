#!/usr/bin/env bash
set -uo pipefail
W=/tmp/est-38e0ac69; cd "$W"
echo "START $(date -u +%FT%TZ) paths=$(wc -l < all.list)"
rm -f out.* err.*
xargs -a all.list -d '\n' -n 400 -P 8 sh -c 'exec stat -c "%s	%b	%B	%n" "$@" >> "out.$$" 2>> "err.$$"' _
echo "stat finished $(date -u +%FT%TZ)"
cat out.* > sizes.tsv; cat err.* > stat.err; rm -f out.* err.*
sed -n "s/^stat: cannot stat[x]* '\(.*\)': \(.*\)$/\1\t\2/p" stat.err > failed.tsv
echo "ok=$(wc -l < sizes.tsv) errlines=$(wc -l < stat.err) failed=$(wc -l < failed.tsv) sum=$(( $(wc -l < sizes.tsv) + $(wc -l < failed.tsv) ))"
awk -F'\t' 'NF!=4 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ {bad++} END {print "malformed_lines=" bad+0}' sizes.tsv
awk -F'\t' '{ split($4, a, "/"); p=a[4]; h=($4 ~ /EH1-Merged/)?"EH1":($4 ~ /EH2-Merged/)?"EH2":($4 ~ /EH3-Merged/)?"EH3":"??"; k=p"/"h; n[k]++; b[k]+=$1; al[k]+=$2*$3; if(!(k in mn)||$1<mn[k])mn[k]=$1; if($1>mx[k])mx[k]=$1 } END { for(k in n) printf "%s\t%d\t%.0f\t%.0f\t%d\t%d\n", k, n[k], b[k], al[k], mn[k], mx[k] }' sizes.tsv | sort > summary.tsv
awk -F'\t' '{split($1,a,"/"); p=a[4]; h=($1 ~ /EH1-Merged/)?"EH1":($1 ~ /EH2-Merged/)?"EH2":"EH3"; n[p"/"h"\t"$2]++} END {for(k in n) print k"\t"n[k]}' failed.tsv | sort > failed_summary.tsv
awk -F'\t' '{d=$1; sub(/\/[^\/]*$/, "", d); f[d]++} END {for (d in f) print d"\t"f[d]}' failed.tsv | sort > failed_by_dir.tsv
awk '{d=$0; sub(/\/[^\/]*$/, "", d); t[d]++} END {for (d in t) print d"\t"t[d]}' all.list | sort > total_by_dir.tsv
join -t "$(printf '\t')" failed_by_dir.tsv total_by_dir.tsv > failed_dirs.tsv
awk -F'\t' '{if($2==$3)full++; else part++} END {print "dirs_with_failures=" NR " fully_failed_dirs=" full+0 " partially_failed_dirs=" part+0 " total_dirs=" }' failed_dirs.tsv
echo "total_dirs=$(wc -l < total_by_dir.tsv)"
echo "=== summary (period/hall, files, bytes, allocated_bytes, min, max) ==="; cat summary.tsv
echo "=== failed (period/hall, reason, count) ==="; cat failed_summary.tsv
echo "zero_size_files=$(awk -F'\t' '$1==0' sizes.tsv | wc -l)"
gzip -kf sizes.tsv; ls -l sizes.tsv.gz failed.tsv | awk '{print $5, $9}'
echo "END $(date -u +%FT%TZ)"
