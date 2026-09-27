# Daya Bay analysis-input preservation

This repository is the complete, restartable toolchain for preserving the
inputs used by the current IBD/accidental, fast-neutron/muon-x, and Li9/He8
analyses. It processes one run at a time, copies that run to a mounted local
disk, verifies it, and only then permits deletion of that run's cluster
staging area.

Production has **not** been started. The repository is ready for review and a
small real-data pilot with original recon inputs has passed. A one-file local
mini-to-slim conversion and full retained-value comparison also passed; the
new input still needs an IHEP worker pilot. An explicit later order and the
mounted destination disk are required for production. See [OPERATIONS.md](OPERATIONS.md) for
the runbook and [BENCHMARK.md](BENCHMARK.md) for measured resource estimates.
The live TWin source audit also found an unresolved raw-versus-corrected
energy-selection difference; see [AD energy selection](#ad-energy-selection)
before using selection-on output for that analysis.

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
version is substituted. All 4,009 per-run catalogs are preserved under
`catalog_backups/` with SHA-256 checksums, and the tool inventories and stages
runs from this complete repository snapshot rather than rediscovering catalogs
from the cluster filesystem. At production start, an immutable local
`catalog-inventory.json` records every expected run, catalog SHA-256, and
source-file count so final completeness can be checked without relying on
memory or scheduler history.

Conversion defaults to the matching mini-reconstruction files under
`/lustrefs/juno26/dyw/rec/All-mini` (`--input-source mini`). A frozen path
`/dybfs/rec/Pxxx/rec/runs_.../runs_.../run_.../recon....root` maps to
`/lustrefs/juno26/dyw/rec/All-mini/runs_.../runs_.../run_.../minirecon....root`.
Run, stream (Neutrino/NoTag), hall, version and file number are unchanged.
`catalog.list` and its checksum retain the original catalog; chunk lists and
timing/provenance paths identify the actual mini inputs.

Use `--input-source recon` explicitly for the original files. There is no
automatic fallback, exclusion, or version substitution. New-run preparation
opens every input and checks its ROOT header before staging. Missing or
unreadable files stop the run. Input source is recorded in run and archive
metadata; resume, transfer, cleanup and final verification check this choice.
Older schema-3 metadata without this field remains readable as `recon`.

The [mini-file inspection](estimates/mini-branches-2026-09-27/README.md) passed
the required-branch/type checks on 48 samples. A separate full-entry comparison
of six original/mini pairs (4,838,398 events) found zero bitwise differences
in all 19 required branches and no missing, added or reordered events.
This includes all three files in the flasher canvas and the three earlier
pilot samples; it is not an archive-wide equivalence check. The September 2 archive index
lists 827,247 of 831,334 required files, leaving 4,087 unlisted; this is not a
fresh all-file availability check. Completeness still needs resolution before
a full campaign. A [flasher-variable canvas](plots/residual_flasher/README.md)
records a separate three-file diagnostic sample.
These findings apply to the current named `All-mini` samples, not to older
mini productions, which may have different branch content.

## Output format

ROOT with ZSTD level 7 is retained because the pilot compressed substantially
better than raw NumPy while preserving the tree paths used by the analyses.
Each chunk contains at most 16 source files and these trees:

- `/Event/Rec/AdSimpleNL`: `site`, `detector`, `triggerType`,
  `triggerNumber`, `triggerTimeSec`, `triggerTimeNanoSec`, `energy`, `x`, `y`,
  `z`, and constant provenance field `runNumber`.
- `/Event/Data/CalibStats`: `nHit`, `MiddleTimeRMS`, `Quadrant`, `Q1`, `Q2`,
  `MaxQ`, `MaxQ_2inchPMT`, `Kurtosis`, `time_PSD_local_RMS`.
- `/Preservation/Sources`: source path, output entry range (`firstEntry`,
  `keptEntries`), source entry count before selection (`entries`), source byte
  count, earliest/latest trigger timestamp, data duration, per-source
  compression wall/CPU duration, and the per-AD counts `adBelow0p3MeV[4]` and
  `adFrom0p3To0p7MeV[4]`.
- `/Preservation/selection`: a `TNamed` whose title states the selection rule
  the chunk was written with; the verifier refuses any other text.

### AD energy selection

The selection is on by default and switched with `--ad-selection on|off` on
the `run` command (the converter takes the same flag). With it on, an entry
from an antineutrino detector (`detector` 1-4) is written only when
`energy > 0.7 MeV`; every entry from any other detector (water pools, RPC) is
written unchanged. Dropped AD entries are not lost as counts: for each source
file and each AD, `adBelow0p3MeV` counts entries with `energy <= 0.3 MeV` or
NaN and `adFrom0p3To0p7MeV` counts entries with `0.3 < energy <= 0.7 MeV`, so
`entries = keptEntries + sum(adBelow0p3MeV) + sum(adFrom0p3To0p7MeV)` holds for
every source. The earliest and latest trigger timestamps and the data duration
are taken over every trigger in the source, dropped ones included. With the
selection off every entry is written, `keptEntries = entries`, and the two AD
count arrays are still filled. The mode is recorded in the run marker,
`run.json`, and every chunk; one archive must use one mode.

**Pre-production caveat:** this selection uses uncorrected
`AdSimpleNL.energy`. The live `src_FD/Retw.C` applies `CorrEnergy` before
`BuildEvent.C` tests the 0.7 MeV threshold. Equivalence has not been
validated, and corrections above unity can promote otherwise dropped
events. Their individual inputs cannot be recovered from aggregate counts.
`--ad-selection off` avoids this loss by retaining every entry; selection-on
must not yet be considered a lossless TWin input. See the
[branch/correction audit](estimates/mini-branches-2026-09-27/README.md#residual-flasher-inputs-in-src_fd).

Every completed run also has `durations.tsv`, with exactly one row per source
file in the fixed catalog order and the same per-source columns, including
`keptEntries` and the eight `ad{N}Below0p3MeV`/`ad{N}From0p3To0p7MeV` counts.
The acquisition duration is calculated as an exact signed 64-bit nanosecond
difference before conversion to ROOT `Double_t`; the double fields are printed
with 17 significant digits for round-trip-safe text. Integer second/nanosecond endpoints are retained as an exact reference.
Compression wall and CPU seconds are also double precision and are collected
during the same source-data pass.

The six event-identification fields come from `AdSimpleNL`. A complete-entry
comparison on a representative source found them identical to the fields
previously read from `AdScaledNL`. No `AdScaled*`, `AdSimple`, `AdTime`, or
unused branches are retained.

## Transaction and integrity model

One run is handled as one transaction:

1. Validate its fixed catalog, resolve the selected input source, check input
   ROOT headers, and split the resolved paths.
2. Submit one HepJob array (`hep_sub -n N`) in group `dyw`.
3. Compress and time each source; publish chunk outputs atomically.
4. Reopen every ROOT chunk and validate trees, branches, the selection tag,
   per-source selection accounting, entry totals, timing fields, and
   timestamp-derived durations; with the selection on, rescan every kept AD
   entry for `energy > 0.7 MeV`; cross-check each chunk's kept entries, source
   entries, source count, and mode against its timing rows.
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

Recheck the complete fixed-catalog snapshot independently:

```bash
python3 preserve.py verify-catalog-backups
```

Recheck one copied run, including its exact artifact set, catalog, duration
rows, byte totals, and SHA-256 hashes:

```bash
python3 preserve.py verify-local /mounted/archive/P17B/EH1/RUN_STEM
```

After all runs are copied, check the complete archive against the saved fixed
inventory, rehash every artifact, check every run's selection mode and entry
totals against its timing table, and refuse an archive that mixes selection
modes or input sources:

```bash
python3 preserve.py verify-all /mounted/archive
```

For an additional structural reopen of every ROOT file, build the converter
where ROOT is available and supply it to either verification command:

```bash
make converter
python3 preserve.py verify-all /mounted/archive --root-verifier ./bin/slim-root
```

The archive-wide full checksum pass reads roughly 12 TB (20 TB with the
selection off) and is therefore
expected to take hours to days depending on the local disk. It is deliberately
separate from the mandatory per-run verification already performed before each
remote deletion.

## Analysis migration

Existing analysis code should stop constructing the `AdScaledNL` chain and
bind the six event-identification fields from `AdSimpleNL`. The remaining
`AdSimpleNL` and `CalibStats` paths and branch names are unchanged. With the
default selection, AD entries at or below 0.7 MeV are absent from the event
trees; their per-file, per-AD counts in the 0.3-0.7 MeV band and below 0.3 MeV
are in `/Preservation/Sources` and `durations.tsv`.

## Current estimate

Plan for a 25 TB destination. With the default AD selection the best current
estimate is about 12 TB (11.5–12.0 TB) of slim ROOT data, measured on
2026-09-03 from a 48-file sample across every period and hall (see
`estimates/adsel-2026-09-03/`); this raw-energy-selected estimate is not yet
validated for the corrected-energy TWin selection. With the selection off
it is 19–21 TB. CPU is
historically about 72 CPU-days centrally and 80–100 CPU-days as an operational
allowance for original `recon` inputs. Throughput has not yet been remeasured
for the new `mini` default; these are not mini-input CPU measurements. The
size projection also assumes minis preserve the necessary source events,
which has been verified for six file pairs, not for the whole archive.
The 4,009 fixed catalogs contain exactly 831,334 source files and
will require 53,821 16-file tasks. The complete repository snapshot fixes all
4,009 run catalogs and 831,334 source paths independently of catalog-filesystem
availability. Production remains gated on the explicit start order.
