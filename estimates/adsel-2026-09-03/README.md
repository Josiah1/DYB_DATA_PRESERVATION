# AD-energy selection disk estimate (measured 2026-09-03)

Question: archive size if only events with AD energy above 0.3 MeV or 0.7 MeV
(detector 1-4) are kept, relative to the ~20 TB all-events estimate.

Method, run on lxlogin001 with the pinned LCG 110 ROOT:

- `sample48.list`: 4 source files per period x hall (12 combinations), chosen
  at run quantiles 1/8, 3/8, 5/8, 7/8 of each fixed catalog, middle file of the run.
- `adsel_count.cpp`: per-detector entry counts, AD counts above 0.3/0.7 MeV and a
  0.1 MeV AD energy histogram (`results/counts.tsv`, 44 readable files).
- One file per combination was converted with the deployed `slim-root`
  (`results/slim/*.result`) and rewritten by `adsel_filter.cpp` into ZSTD-7
  variants: `all`, `ad03`, `ad07` (AD-only above threshold), `ad03wp`, `ad07wp`
  (AD above threshold plus every water-pool/RPC entry) (`results/filt/*.tsv`).
- `analyze.py .` reproduces `summary.txt`: direct chunk-bytes x file-count
  totals (A) and per-combination fraction x compression-coefficient totals (C).

Findings: AD entries compress to ~47 B/event, non-AD entries to 11-20 B/event,
so byte fractions exceed entry fractions. Scaled to the 20 TB baseline:
AD-only >0.3 MeV ~14 TB (13.6-14.7), AD-only >0.7 MeV ~7.5 TB (7.2-7.8);
keeping all WP/RPC entries as well: ~18.6-19.0 TB and ~11.5-12.0 TB.

Cluster observations that day: `/dybfs2` mounted read-only on lxlogin001 and
4 of 48 sampled catalog sources unreadable (`results/count.retry.log`).
