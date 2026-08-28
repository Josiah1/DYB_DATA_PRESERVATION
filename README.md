# Daya Bay analysis-input preservation

This repository is the complete, restartable toolchain for preserving the
inputs used by the current IBD/accidental, fast-neutron/muon-x, and Li9/He8
analyses. It processes one run at a time, copies that run to a mounted local
disk, verifies it, and only then permits deletion of that run's cluster
staging area.

Production has **not** been started. The repository is ready for review and a
small real-data pilot has passed; an explicit later order and the mounted
destination disk are still required. See [OPERATIONS.md](OPERATIONS.md) for
the runbook and [BENCHMARK.md](BENCHMARK.md) for measured resource estimates.

## Fixed inputs

The catalog roots are fixed to the versions in
`/dybfs2/users/lijj16/nHBackEndAnalysis/Gen_IBD_Retw_Script.py`:

| Period | Good-run catalog |
| --- | --- |
| P17B | `goodrunlist_p17b_v4/good_v4_sync.ihep` |
| P19A | `goodrunlist_p19a_v5/good_v5_sync.ihep` |
| P20A | `goodrunlist_p20a_v3/good_v3_sync.ihep` |
| P21A | `goodrunlist_p21a_v1/good_v1_sync.ihep` |

EH1, EH2, and EH3 are included. No newer reconstruction or good-run-list
version is substituted. The 85 fixed catalogs that were unreadable on the
primary filesystem are preserved under `catalog_backups/` with SHA-256
checksums; the tool uses these repository copies for those runs. At production
start, an immutable local
`catalog-inventory.json` records every expected run, catalog SHA-256, and
source-file count so final completeness can be checked without relying on
memory or scheduler history.

## Output format

ROOT with ZSTD level 7 is retained because the pilot compressed substantially
better than raw NumPy while preserving the tree paths used by the analyses.
Each chunk contains at most 16 source files and these trees:

- `/Event/Rec/AdSimpleNL`: `site`, `detector`, `triggerType`,
  `triggerNumber`, `triggerTimeSec`, `triggerTimeNanoSec`, `energy`, `x`, `y`,
  `z`, and constant provenance field `runNumber`.
- `/Event/Data/CalibStats`: `nHit`, `MiddleTimeRMS`, `Quadrant`, `Q1`, `Q2`,
  `MaxQ`, `MaxQ_2inchPMT`, `Kurtosis`, `time_PSD_local_RMS`.
- `/Preservation/Sources`: source path, output entry range, source byte count,
  earliest/latest trigger timestamp, data duration, and per-source compression
  wall/CPU duration.

Every completed run also has `durations.tsv`, with exactly one row per source
file in the fixed catalog order. The acquisition duration is calculated as an
exact signed 64-bit nanosecond difference before conversion to ROOT `Double_t`;
the double fields are printed with 17 significant digits for round-trip-safe
text. Integer second/nanosecond endpoints are retained as an exact reference.
Compression wall and CPU seconds are also double precision and are collected
during the same source-data pass.

The six event-identification fields come from `AdSimpleNL`. A complete-entry
comparison on a representative source found them identical to the fields
previously read from `AdScaledNL`. No `AdScaled*`, `AdSimple`, `AdTime`, or
unused branches are retained.

## Transaction and integrity model

One run is handled as one transaction:

1. Validate and split its fixed `.list` catalog.
2. Submit one HepJob array (`hep_sub -n N`) in group `dyw`.
3. Compress and time each source; publish chunk outputs atomically.
4. Reopen every ROOT chunk and validate trees, branches, entry totals, timing
   fields, and timestamp-derived durations.
5. Aggregate one `durations.tsv` and SHA-256 every preserved artifact.
6. Recheck the external mount, `rsync` to `.incoming`, recheck the mount, and
   recompute all SHA-256 hashes locally.
7. Atomically rename the verified local run.
8. Only with the matching catalog checksum and completion sentinels, optionally
   delete that exact remote staging directory and continue.

Failed chunks are resumable; verified chunks and timing reports are skipped.
The external disk is checked before and after every transfer, preventing a
silently unmounted volume from authorizing remote deletion.

## Verification commands

Local logic checks require no ROOT installation:

```bash
make test
```

Recheck the 85 recovered fixed catalogs independently:

```bash
python3 preserve.py verify-catalog-backups
```

Recheck one copied run, including its exact artifact set, catalog, duration
rows, byte totals, and SHA-256 hashes:

```bash
python3 preserve.py verify-local /mounted/archive/P17B/EH1/RUN_STEM
```

After all runs are copied, check the complete archive against the saved fixed
inventory and rehash every artifact:

```bash
python3 preserve.py verify-all /mounted/archive
```

For an additional structural reopen of every ROOT file, build the converter
where ROOT is available and supply it to either verification command:

```bash
make converter
python3 preserve.py verify-all /mounted/archive --root-verifier ./bin/slim-root
```

The archive-wide full checksum pass reads roughly 20 TB and is therefore
expected to take hours to days depending on the local disk. It is deliberately
separate from the mandatory per-run verification already performed before each
remote deletion.

## Analysis migration

Existing analysis code should stop constructing the `AdScaledNL` chain and
bind the six event-identification fields from `AdSimpleNL`. The remaining
`AdSimpleNL` and `CalibStats` paths and branch names are unchanged.

## Current estimate

Plan for a 25 TB destination. The best current estimate is 19–21 TB of slim
ROOT data, about 72 CPU-days centrally and 80–100 CPU-days as an operational
allowance. The 4,009 fixed catalogs contain exactly 831,334 source files and
will require about 52,000–54,000 16-file tasks. Production remains gated on a
clean deployed inventory and the explicit start order.
