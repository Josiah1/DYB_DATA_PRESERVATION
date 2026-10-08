# Daya Bay analysis-input preservation

This repository is the complete, restartable toolchain for preserving the
inputs used by the current IBD/accidental, fast-neutron/muon-x, and Li9/He8
analyses. It processes runs concurrently within a 300-job cap, copies each run to a mounted local
disk, verifies it, and only then permits deletion of that run's cluster
staging area.

The single-disk campaign was authorized on **2026-09-29**. Its 48-file,
288-conversion worker benchmark selected **raw AD energy > 0.6 MeV**, with
an estimated **11.84 TB** output and a **12.35 TB** upper sample envelope for
the empty 14-TB disk. See the [threshold study](estimates/energy-threshold-2026-09-29/README.md)
and [campaign runbook](OPERATIONS.md#single-disk-campaign-2026-09-29) for the
destination, validation, and launch command.
See [OPERATIONS.md](OPERATIONS.md) for
the runbook and [BENCHMARK.md](BENCHMARK.md) for measured resource estimates.
The default now retains every event, leaving corrected-energy selection to
TWin. See [TWin compatibility](TWIN_COMPATIBILITY.md) for the pinned GitHub
reader, filename contract, and bounded end-to-end regression.
The new two-file test passed on 1,509,445 readouts; four physics modes match
with a fixed test initialization clock (TWin's existing startup-clock
dependence is documented separately from preservation compatibility).

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
from the cluster filesystem. At production start, a local
`catalog-inventory.json` records every expected run, catalog SHA-256, and
source-file count. This catalog set stays fixed so final completeness can be
checked without relying on memory or scheduler history.

Conversion defaults to the matching mini-reconstruction files under
`/lustrefs/juno26/dyw/rec/All-mini` (`--input-source mini`). A frozen path
`/dybfs/rec/Pxxx/rec/runs_.../runs_.../run_.../recon....root` maps to
`/lustrefs/juno26/dyw/rec/All-mini/runs_.../runs_.../run_.../minirecon....root`.
Run, stream (Neutrino/NoTag), hall, version and file number are unchanged.
`catalog.list` and its checksum retain the original catalog; chunk lists and
timing/provenance paths identify the actual inputs.

The current single-disk launcher uses **`--input-source mini --skip-missing-minis`**.
It never opens original reconstruction files. Two consecutive missing-file
results permit an individual skip, recorded with its exact catalog index,
identity, mini path, timestamps and reason. I/O errors get up to three attempts;
unresolved I/O, permission and invalid-header errors stop the run. An inaccessible
or empty mini root, three consecutive missing minis, or more than 10% missing
(with a floor of three) stops preparation as a suspected storage outage.

The complete catalog stays unchanged. `input_count` is the expected count;
`processed_input_count + skipped_input_count` must equal it. `resolved_inputs`
stays aligned with the catalog, with `null` only for explicitly recorded gaps.
The SHA-256-protected `input-gaps.json` is required for every run with gaps.
`durations.tsv` lists only processed inputs, in catalog order; its `fileIndex`
is the dense processed index, while gap records use the original `catalog_index`.
The run's `complete` marker means finalization succeeded; `catalog_complete`
is false if any input was skipped.

Earlier verified runs, including original-file fallbacks, keep their source
metadata and output bytes. The policy transition pins those existing run names
and source modes in `catalog-inventory.json`; new runs must be mini-derived.
The automatically updated `provenance/input-issues.json` and `INPUT_ISSUES.md`
retain missing-mini records and historical fallback observations. The approximate
0.5% from an earlier inventory is not used as a target or forced missing count.
Schema-3 archives use the previous toolchain at commit `5479aef`; schema 4
records the new 0.6-MeV count band and refuses to mix with that older format.

The [mini-file inspection](estimates/mini-branches-2026-09-27/README.md) passed
the required-branch/type checks on 48 samples. A separate full-entry comparison
of six original/mini pairs (4,838,398 events) found zero bitwise differences
in all 19 required branches and no missing, added or reordered events.
This includes all three files in the flasher canvas and the three earlier
pilot samples; it is not an archive-wide equivalence check. The September 2 archive index
lists 827,247 of 831,334 required files, leaving 4,087 unlisted; this is not a
fresh all-file availability check. The campaign checks each run's minis before staging.
A [flasher-variable canvas](plots/residual_flasher/README.md)
records a separate three-file diagnostic sample.
These findings apply to the current named `All-mini` samples, not to older
mini productions, which may have different branch content.

## Output format

ROOT with **ZSTD level 9 and 256 KiB branch baskets** is the default
(`dyb-slim-v5`, schema 4). Automatic basket-size adaptation and autosave are disabled;
event baskets are flushed at each source boundary for bounded memory and
per-source timing. The lossless codec comparison in [BENCHMARK.md](BENCHMARK.md)
measured a smaller, slower LZMA alternative; it has not been adopted.
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
  `adFrom0p3To0p6MeV[4]`.
- `/Preservation/selection`: a `TNamed` whose title states the selection rule
  the chunk was written with; the verifier refuses any other text.

Chunk names are `preserved.0048734.slim.000000.root`, with a seven-digit
run field for TWin and a zero-based, six-digit chunk index. Tree paths, scalar
types and source event order are preserved; no precision reduction is used.
`run.json` records `output_prefix`; older archives without it still verify
using their original catalog-stem filenames.

### AD energy selection

The selection is **off by default** and switched with `--ad-selection on|off` on
the `run` command (the converter takes the same flag). With it on, an entry
from an antineutrino detector (`detector` 1-4) is written only when
`energy > 0.6 MeV`; every entry from any other detector (water pools, RPC) is
written unchanged. Dropped AD entries are not lost as counts: for each source
file and each AD, `adBelow0p3MeV` counts entries with `energy <= 0.3 MeV` or
NaN and `adFrom0p3To0p6MeV` counts entries with `0.3 < energy <= 0.6 MeV`, so
`entries = keptEntries + sum(adBelow0p3MeV) + sum(adFrom0p3To0p6MeV)` holds for
every source. The earliest and latest trigger timestamps and the data duration
are taken over every trigger in the source, dropped ones included. With the
selection off every entry is written, `keptEntries = entries`, and the two AD
count arrays are still filled. The mode is recorded in the run marker,
`run.json`, and every chunk; one archive must use one mode.

**Opt-in selection caveat:** this selection uses uncorrected
`AdSimpleNL.energy`. TWin applies an energy correction before
`BuildEvent.C` tests the threshold (0.7 MeV normally, 0.3 MeV with `--acpo`). Equivalence has not been
validated, and corrections above unity can promote otherwise dropped
events. Their individual inputs cannot be recovered from aggregate counts.
`--ad-selection off` avoids this loss by retaining every entry; selection-on
must not yet be considered a lossless TWin input. See the
[branch/correction audit](estimates/mini-branches-2026-09-27/README.md#residual-flasher-inputs-in-src_fd).

Every completed run also has `durations.tsv`, with exactly one row per source
file in the fixed catalog order and the same per-source columns, including
`keptEntries` and the eight `ad{N}Below0p3MeV`/`ad{N}From0p3To0p6MeV` counts.
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
   entry for `energy > 0.6 MeV`; cross-check each chunk's kept entries, source
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
modes or violates its recorded input-source policy. A reconciled archive with
gaps reports `complete: false`, `accounted: true`, `status: finished_with_gaps`
and exits with status 2. Missing runs or invalid artifacts still fail:

```bash
python3 preserve.py verify-all /mounted/archive
```

For an additional structural reopen of every ROOT file, build the converter
where ROOT is available and supply it to either verification command:

```bash
make converter
python3 preserve.py verify-all /mounted/archive --root-verifier ./bin/slim-root
```

The archive-wide full checksum pass reads roughly 19 TB with the default
selection off and is therefore
expected to take hours to days depending on the local disk. It is deliberately
separate from the mandatory per-run verification already performed before each
remote deletion.

## Analysis compatibility

The GitHub `Josiah1/DayaBay-TWin` reader at `d6b1b5e` already reads
`AdSimpleNL` and `CalibStats` without `AdScaledNL`. New output filenames
meet its run-number parser, and all events are retained by default.
Feed one run's chunks, in ascending chunk-index order, to one Retw invocation;
running Retw independently on each chunk would lose cross-chunk coincidences.
See [TWIN_COMPATIBILITY.md](TWIN_COMPATIBILITY.md) for usage and regression
evidence. Older cluster copies and the separate background readers must be
checked or migrated independently; this does not update them in place.

## Current estimate

The authorized **single-disk selection-on campaign** projects **11.84 TB**,
with an upper sample envelope of **12.35 TB**, for the disk with 14.00 TB
available. The launcher explicitly selects `--ad-selection on` (0.6 MeV).
For an **all-event archive**, plan for **25 TB of usable destination space**.
The 2026-09-27 IHEP-mini test, with **no energy selection**, projects **18.59
TB** for the adopted ROOT/ZSTD-9/256-KiB setting, or **16.69 TB** with the tested
ROOT/LZMA alternative. The old ZSTD-7 baseline projected **65.45 CPU-days**;
that is not an end-to-end timing estimate for the new setting. ZSTD-9 repacking
took 2.11 times the paired ZSTD-7 repacking CPU. The September 29 worker sweep
projects 169 CPU-days with the 0.6-MeV cut, before transfer and final checks.
These are sample-based projections, not
completed-campaign measurements. See
[BENCHMARK.md](BENCHMARK.md#current-all-event-estimate-ihep-minis-2026-09-27)
for weighting, sensitivity and timing boundaries.

The historical selection-on estimate remains about 11.5–12.0 TB, based on
the September 3 study (44 readable counting samples; 12 actually converted
period/hall representatives). That raw-energy-selected output is not yet
validated for TWin's corrected-energy selection. The current default
`--ad-selection off` retains every input event.
Original-to-mini event equivalence has been checked for six file pairs, not
for the whole archive.
The 4,009 fixed catalogs contain exactly 831,334 source files and
will require 53,821 16-file tasks. The complete repository snapshot fixes all
4,009 run catalogs and 831,334 source paths independently of catalog-filesystem
availability. The September 29 start order applies to the single-disk campaign.
