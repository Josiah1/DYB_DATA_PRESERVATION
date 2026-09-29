# Operations runbook

This runbook covers deployment, a one-run production pilot, restart, transfer,
cleanup, and final completeness verification. Nothing in the preparation or
benchmark performed on 2026-08-28 submitted a HepJob task or deleted cluster
data.

## Single-disk campaign, 2026-09-29

Status: the 0.6-MeV one-run pilot is running. At the user's request, cuts
0.55–0.59 MeV will be tested before choosing the final cut and starting the
full campaign. Verified changes must be committed and pushed first.

The user authorized threshold testing and production onto the mounted empty
portable disk. The chosen raw AD cut is **energy > 0.6 MeV**, with all WP/RPC
events retained. The [48-file worker study](estimates/energy-threshold-2026-09-29/README.md)
projects 11.84 TB, or 12.35 TB using within-stratum maxima, within a 12.60-TB
budget that reserves 10% of the disk's 14.00 TB free space.

- Disk UUID: `2A4F48F8BDFEE355`, currently `/dev/sdb3` at `/media/josiah/LAYJ萤捷1`.
- Data: `/media/josiah/LAYJ萤捷1/DYB_DATA_PRESERVATION/data`.
- Code and staging: `/scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/{code,staging}` on `dayabay1`.
- Format: `dyb-slim-v5`, metadata schema 4, `adFrom0p3To0p6MeV` count band.
- Local checks: `make test`; on IHEP all 12 tests passed, including both ROOT tests.
- Catalog inventory: 4,009 catalogs, 831,334 source files, no catalog failures.

`scripts/run_campaign.sh` locates this UUID, explicitly enables the selected
cut, and supplies the already-authorized execution and verified-staging
cleanup flags. Every transfer checks the UUID before and after copying; all
SHA-256 checks must pass before this run's generated scratch data is removed.
Original mini/recon inputs are read-only throughout.

```bash
# One-run pilot; the same output is retained for the full campaign.
bash scripts/run_campaign.sh --limit-runs 1
# Resume or run all catalogs; do not launch a second controller concurrently.
bash scripts/run_campaign.sh
```

The full campaign will use a local `dyb-preservation.service` user unit.
Read progress with `systemctl --user status dyb-preservation.service`
and `tail -n 30 build/campaign.log`. Launch it with a sleep inhibitor.
After a failure, inspect the log and fix its cause before restarting. An
unmounted/replaced disk, unreadable input, failed chunk, or checksum mismatch
stops the controller. This sample-based capacity estimate does not establish
archive-wide input availability or lossless TWin physics equivalence.

Schema-3 / 0.7-MeV archives require the previous toolchain at `5479aef`;
start new staging and a new destination for schema 4. Do not relabel old counts.

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

`inventory` validates catalogs, not current ROOT-file availability. Conversion
defaults to `--input-source mini`, using `/lustrefs/juno26/dyw/rec/All-mini`
with the exact run/file/version identities from those catalogs. New-run
preparation opens every mapped source and checks its four-byte ROOT signature
before staging. Unavailable inputs stop the run; no file is silently skipped
or replaced. The September 2 mini index leaves 4,087 required paths unlisted;
see `estimates/mini-branches-2026-09-27/`. A dry run does not open source files.

For an explicit original-reconstruction campaign, use `--input-source recon`.
Use one source per local archive. The archive snapshot and run metadata pin
the choice; restarting with a different source is refused. Older schema-3
archives require the previous toolchain at `5479aef`. Deploy the updated
`preserve.py` before using the new default on the cluster.

## Staging without a writable `/dybfs2`

On 2026-09-03 `/dybfs2` was mounted read-only on every login node and on the
compute nodes, and it was 97% full. Original `recon` inputs use `/dybfs2`
(`/dybfs` is a symlink into it); the new default reads `/lustrefs/juno26/dyw`.
The code root and the
staging root can live on the scratch Lustre pool instead. A worker probe job
that day (cluster 81006673 on node cws048) confirmed that compute nodes write
to `/scratchfs2/dyw/lijj16`, read the `/dybfs` sources, load the LCG 110 view
with ROOT 6.40.02, and run the deployed converter. The user quota there is
500 GB and 200,000 files against a peak of about 30 GB and a few hundred
files per run, and the pool had 1 PB free. Scratch is not backed up, which is
acceptable: the code is in Git and every verified run is deleted from staging
after its local copy is checked.

Pass both roots to every command:

```bash
CODE=/scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/code
STAGE=/scratchfs2/dyw/lijj16/DYB_DATA_PRESERVATION/staging
python3 preserve.py deploy --alias dayabay1 --remote-code-root $CODE --execute
python3 preserve.py inventory --alias dayabay1 --remote-code-root $CODE
python3 preserve.py run --remote-code-root $CODE --remote-stage-root $STAGE \
  --local-root /Volumes/DYB_DISK/data --mount-root /Volumes/DYB_DISK \
  --period P17B --hall EH1 --limit-runs 1
```

The last observed converter under `/dybfs2` was `dyb-slim-v2`; the current
format requires deploying `dyb-slim-v5` to the new code root first, together
with the updated `preserve.py`. Confirm `bin/slim-root --version` there before
starting. The September 28 format change did not deploy production code or
start production. Its bounded TWin test used disposable `/tmp` files. The earlier
worker probe did not test compute-node readability of `/lustrefs/juno26/dyw`;
verify it in the authorized pilot. A one-file local mini conversion passed
reopening and all retained-value comparisons; see
`estimates/mini-branches-2026-09-27/conversion-check.json`. This does not replace
the compute-node check. Inputs becoming unreadable after preparation
make their chunk fail; the run stays in staging until storage is repaired.

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

Defaults are mini inputs, ROOT ZSTD 9 / 256 KiB baskets, 16 source files per
task, the AD energy selection off (`--ad-selection off`, every entry kept),
HepJob group `dyw`, `short`
walltime, 1,024 MB memory, two submission attempts, and a two-hour per-run
wait. Override only after measured evidence from the production pilot. Use one
selection mode for the whole archive: `verify-all` rejects a mixed archive and
`run` refuses to continue a local run made with the other mode.

## Restart and recovery

- Re-run the same command after an SSH interruption or failed task. The run
  marker fixes the catalog and chunking; valid outputs and timing reports are
  reopened, verified, and skipped.
- A catalog checksum, period, hall, `--files-per-job`, `--input-source`, or `--ad-selection`
  mismatch is refused.
- New staging uses `preserved.NNNNNNN.slim.CCCCCC.root` filenames for TWin.
  Legacy staging without this recorded output prefix cannot resume under the
  new preparation command; use a new empty staging root. Existing completed
  archives retain their names and remain verifiable. Do not rename files
  independently of their checksums and run metadata.
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
| `path` | actual absolute input path: minirecon by default, recon in explicit original mode |
| `entries`, `inputBytes` | actual input event count before our selection and input byte count |
| `firstTriggerTimeSec/NanoSec` | earliest trigger timestamp in the source |
| `lastTriggerTimeSec/NanoSec` | latest trigger timestamp in the source |
| `dataDurationSeconds` | `(latest_ns - earliest_ns) / 1e9` as a double |
| `processingWallSeconds` | source read plus compression-flush wall time |
| `processingCpuSeconds` | source read plus compression-flush CPU time |
| `keptEntries` | entries written to the ROOT chunk from this source |
| `ad1Below0p3MeV` … `ad4Below0p3MeV` | per-AD entries with `energy <= 0.3 MeV` or NaN |
| `ad1From0p3To0p6MeV` … `ad4From0p3To0p6MeV` | per-AD entries with `0.3 < energy <= 0.6 MeV` |

The seconds/nanoseconds columns make the acquisition duration independently
recomputable without subtracting large floating-point epoch values; they cover
every trigger in the source, including dropped AD entries. With the selection
on, `entries = keptEntries + sum of the eight AD counts` for every row; with it
off, `keptEntries = entries` and the counts are informational.

`catalog.list` retains the original fixed recon paths. Verification maps those
paths using the recorded input source before comparing the duration rows.
Mini input byte counts and durations describe the actual mini files, not an
unmeasured original file. This tool cannot undo an upstream mini-file selection.

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
below 0.6 MeV; `verify-all` compares each chunk's reported kept entries, source
entries, source count, and mode with the corresponding timing rows.
