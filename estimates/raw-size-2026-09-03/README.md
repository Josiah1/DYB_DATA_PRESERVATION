# Raw source data set size and readability sweep (2026-09-03)

Metadata-only `stat` of every one of the 831,334 fixed-catalog paths, run on
lxlogin001 from the deployed catalog snapshot (manifest SHA-256 verified),
8 workers, about two minutes. No file data was read. `sweep.sh` is the driver.

- `sizes.tsv.gz`: `size  blocks  blocksize  path` for the 792,155 stat-able files.
- `failed.tsv`: path and error for the 39,179 paths that failed (38,455
  permission denied, 724 no such file).
- `failed_dirs.tsv`, `failed_summary.tsv`, `summary.tsv`: per-directory and
  per-period/hall breakdowns; `missing_files.list`, `zero_size_files.list`.
- `summary.txt`: the totals table.

Result: 544.9 TB decimal (495.6 TiB) over the stat-able files, mean 688 MB
per file; about 571 TB for the full catalog when failed files are filled with
their period/hall mean. The permission-denied set coincided with `/dybfs2`
being mounted read-only and should be re-swept; the missing and zero-size
lists are candidate permanent losses.
