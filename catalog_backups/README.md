# Complete fixed-catalog snapshot

This directory preserves all 4,009 per-run catalogs from the versions fixed by
`Gen_IBD_Retw_Script.py`. It contains 831,334 source ROOT paths in 98,092,666
bytes. `preserve.py` uses this snapshot as its catalog source of truth; no event
ROOT data is stored here.

The snapshot was split without changing line order or bytes from these
authoritative sync master files:

| Period | Master file | SHA-256 |
| --- | --- | --- |
| P17B | `paths_p17b_sync_v4.txt` | `aef1107d8d082fd87d3a82a88bdd5bcde6c162c9e1558cefdebcdfceba39ca7d` |
| P19A | `paths_p19a_sync_v5.txt` | `73e26e62c36484854b61e5389033c34a26c1930e3a87083a79314adae7edc6d5` |
| P20A | `paths_p20a_sync_v3.txt` | `71b18d6595690b5a3b4717fb5546fc56961086f2cf63f80f6e0264dd8b91183d` |
| P21A | `paths_p21a_sync_v1.txt` | `73df980bd1e5295c56bab8f8c83f8e208fe17ed745adf2be9fbc7215112c10c0` |

As an independent check, 3,874 currently readable per-run files from
`/dybfs2/users/jixp/work_dyb/work_P17B/dybfs__rec__P***` were compared against
this snapshot. Every one was byte-identical; there were zero mismatches. The
remaining jixp entries returned filesystem I/O errors, which is why the
authoritative masters are the complete source.

`SHA256SUMS` fixes every per-run file. Validate the complete set, hashes, source
syntax, period, hall, run identity, and duplicate paths with:

```bash
python3 preserve.py verify-catalog-backups
```

To reproduce the split from independently acquired master files:

```bash
python3 scripts/build_catalog_backups.py \
  --masters-dir /path/to/four/masters \
  --output /new/empty/catalog_backups
```
