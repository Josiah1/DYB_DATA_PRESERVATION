# Recovered fixed-catalog snapshot

This directory preserves the 85 fixed-version run catalogs that returned
filesystem I/O errors on 2026-08-28. It contains 19,823 source paths in
2,339,114 bytes. No event ROOT data is stored here.

- 82 catalogs were copied byte-for-byte from
  `/dybfs2/users/jixp/work_dyb/work_P17B/dybfs__rec__P***`.
- `P17B/EH1/run64327.list`, `P17B/EH2/run46003.list`, and
  `P17B/EH3/run60227.list` were unreadable in both the fixed per-run directory
  and jixp's backup. They were extracted by exact run directory from the
  authoritative fixed-version master file
  `/dybfs2/rec/P17B/goodrunlist_p17b_v4/paths_p17b_sync_v4.txt`.
- As a control, the same master-file extraction reproduced the readable
  per-run catalogs for runs 63563, 46127, and 58784 byte-for-byte.
- Across the wider backup trees, 3,792 readable jixp/original catalog pairs
  were compared byte-for-byte with zero mismatches.

The SHA-256 of the sorted 85-path failure set is
`5cacd2172cf17ce38c4911a4669bf53165c1e8f384cb2a5f99f4ca809a1d5eed`.
`SHA256SUMS` fixes every recovered file. Validate hashes, paths, run identity,
and source counts with:

```bash
python3 preserve.py verify-catalog-backups
```
