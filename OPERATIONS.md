# Operations runbook

This runbook covers deployment, a one-run production pilot, restart, transfer,
cleanup, and final completeness verification. Nothing in the preparation or
benchmark performed on 2026-08-28 submitted a HepJob task or deleted cluster
data.

## Safety gates

Production requires all of the following:

- an explicit user order to start;
- the destination disk mounted at the path passed as `--mount-root`;
- the complete 4,009-catalog repository snapshot passing checksum and identity
  validation;
- `--execute --confirm-start START-DYB-PRESERVATION`;
- for automatic cluster cleanup,
  `--delete-remote-after-sync --confirm-remote-delete DELETE-VERIFIED-RUN`.

The program rechecks the mount immediately before and after each transfer.
Remote cleanup is impossible until local SHA-256 verification succeeds and the
local catalog/schema match the remote finalized run.

## Preflight

From this repository on the local machine:

```bash
make test
python3 preserve.py verify-catalog-backups
python3 preserve.py deploy
```

The `deploy` command is a dry run. After the explicit start order, deploy and
compile under the pinned IHEP LCG 110 environment:

```bash
python3 preserve.py deploy \
  --alias dayabay1 \
  --remote-code-root /dybfs2/users/lijj16/DYB_DATA_PRESERVATION/code \
  --execute
```

Then require a clean fixed-catalog inventory:

```bash
python3 preserve.py inventory --alias dayabay1
```

The inventory must report `catalogs=4009 readable=4009 unreadable=0
backups=4009 inputs=831334`. It exits nonzero if the snapshot is incomplete or
any checksum, period, hall, run identity, or source path fails validation. Do
not bypass this gate.

## Dry run and first one-run pilot

Replace both `/Volumes/DYB_DISK` examples with the real mounted disk paths.
This selection command only reports the planned work:

```bash
python3 preserve.py run \
  --local-root /Volumes/DYB_DISK/data \
  --mount-root /Volumes/DYB_DISK \
  --period P17B --hall EH1 --limit-runs 1
```

After reviewing the selection and receiving the start order, the same one-run
pilot becomes executable only with both confirmation phrases:

```bash
python3 preserve.py run \
  --local-root /Volumes/DYB_DISK/data \
  --mount-root /Volumes/DYB_DISK \
  --period P17B --hall EH1 --limit-runs 1 \
  --execute --confirm-start START-DYB-PRESERVATION \
  --delete-remote-after-sync \
  --confirm-remote-delete DELETE-VERIFIED-RUN
```

Inspect that local run with `verify-local` before expanding the selection.

## Full run

Omitting period, hall, pattern, and limit selects all fixed catalogs. This is
the command to use only after the one-run pilot is accepted:

```bash
python3 preserve.py run \
  --local-root /Volumes/DYB_DISK/data \
  --mount-root /Volumes/DYB_DISK \
  --execute --confirm-start START-DYB-PRESERVATION \
  --delete-remote-after-sync \
  --confirm-remote-delete DELETE-VERIFIED-RUN
```

Defaults are 16 source files per task, the AD energy selection on
(`--ad-selection on`; `off` keeps every entry), HepJob group `dyw`, `short`
walltime, 1,024 MB memory, two submission attempts, and a two-hour per-run
wait. Override only after measured evidence from the production pilot. Use one
selection mode for the whole archive: `verify-all` rejects a mixed archive and
`run` refuses to continue a local run made with the other mode.

## Restart and recovery

- Re-run the same command after an SSH interruption or failed task. The run
  marker fixes the catalog and chunking; valid outputs and timing reports are
  reopened, verified, and skipped.
- A catalog checksum, period, hall, `--files-per-job`, or `--ad-selection`
  mismatch is refused.
- All 4,009 catalogs are rehashed and checked for exact period, hall, run,
  source-path identity, and duplicate paths before they can be staged.
- A failed transfer remains under `.RUN_STEM.incoming` and never authorizes
  remote deletion. Re-running resumes `rsync` and rechecks all hashes.
- If the external disk disappears, remount it at the same `--mount-root` and
  rerun. Do not manually move `.incoming` into its final name.
- If a run exhausts both attempts, inspect its remote `logs/*.err` and
  `status/*.fail`; no cleanup occurs.
- Never manually remove a remote run until the corresponding local
  `verify-local` command succeeds.

## Per-run timing file

`durations.tsv` is tab-separated and follows the fixed source catalog order:

| Column | Meaning |
| --- | --- |
| `fileIndex` | zero-based index in the run catalog |
| `chunkIndex`, `sourceIndex` | output chunk and index within that chunk |
| `path` | original absolute ROOT path |
| `entries`, `inputBytes` | source event count before selection and original byte count |
| `firstTriggerTimeSec/NanoSec` | earliest trigger timestamp in the source |
| `lastTriggerTimeSec/NanoSec` | latest trigger timestamp in the source |
| `dataDurationSeconds` | `(latest_ns - earliest_ns) / 1e9` as a double |
| `processingWallSeconds` | source read plus compression-flush wall time |
| `processingCpuSeconds` | source read plus compression-flush CPU time |
| `keptEntries` | entries written to the ROOT chunk from this source |
| `ad1Below0p3MeV` … `ad4Below0p3MeV` | per-AD entries with `energy <= 0.3 MeV` or NaN |
| `ad1From0p3To0p7MeV` … `ad4From0p3To0p7MeV` | per-AD entries with `0.3 < energy <= 0.7 MeV` |

The seconds/nanoseconds columns make the acquisition duration independently
recomputable without subtracting large floating-point epoch values; they cover
every trigger in the source, including dropped AD entries. With the selection
on, `entries = keptEntries + sum of the eight AD counts` for every row; with it
off, `keptEntries = entries` and the counts are informational.

## Completeness verification

Per run:

```bash
python3 preserve.py verify-local /Volumes/DYB_DISK/data/P17B/EH1/RUN_STEM
```

After the full campaign:

```bash
python3 preserve.py verify-all /Volumes/DYB_DISK/data
```

`verify-all` requires exactly every run in `catalog-inventory.json`, rejects
unexpected run directories, matches catalog hashes and source counts, checks
one duration row per catalog source in exact order with valid selection
accounting, matches the row, entry, and kept-entry totals against `run.json`,
checks the exact ROOT chunk set, recomputes every artifact SHA-256 and byte
total, and reports the single selection mode of the archive. It exits nonzero
if runs were made with different modes.

Where a compatible ROOT installation is available, add structural reopening:

```bash
make converter
python3 preserve.py verify-all /Volumes/DYB_DISK/data \
  --root-verifier ./bin/slim-root
```

The converter then checks required trees and branches, the selection tag,
equal event-tree entry counts, per-source selection accounting, kept-entry
totals, finite nonnegative timing values, exact agreement between each double
data duration and its integer timestamps, and that no kept AD entry is at or
below 0.7 MeV; `verify-all` compares each chunk's reported kept entries, source
entries, source count, and mode with the corresponding timing rows.
