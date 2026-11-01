# Recorded missing input files

Campaign ledger snapshot: **2026-11-01T12:29:43+00:00**. All runs are finalized; status: `finished_with_gaps`.

- **4075 individual mini files** recorded missing with `ENOENT` (errno 2).
- **4075** omitted from verified local archives (`skipped`).
- **0** recorded for omission during run preparation (`skip_recorded`).
- **12** earlier mini failures were preserved from their exact originals;
  their provenance remains in the ledger and they are excluded from this lost-input list.

[Plain path list](missing-input-files.list) has one full All-mini input path per line.
[CSV](missing-input-files.csv) adds filename, period, hall, run, zero-based catalog index,
disposition, observation time, original catalog path, checksum, and evidence.
Each row identifies a file. Scheduler timeouts, bad-run classifications, I/O
errors, invalid ROOT headers, and unassessed inputs are excluded. Confirmed broken
minis are excluded from this missing list and separately exported in
[BROKEN_INPUTS.md](BROKEN_INPUTS.md), [their path list](broken_files.list),
[CSV](broken_files.csv), and [proof ledger](broken-input-issues.json).

Coverage is finalized archives plus recorded preparation observations. At this
snapshot, 4009 of 4009 runs were finalized,
with 825,529 preserved inputs, 4075 finalized missing omissions
and 1730 finalized broken omissions
out of 831,334 catalog inputs. All expected inputs are accounted for; no inputs remain pending.
Absence is established
at each recorded observation time; this export does not probe source storage again.

Source: Ubuntu's `provenance/input-issues.json` on archive disk UUID
`2A4F48F8BDFEE355`, copied to [input-issues.json](input-issues.json).
The ledger retains retry evidence, historical recoveries, and prior observations.

After copying an updated ledger, regenerate and validate the lists with:

```sh
~/TOOLs/install/python-current/bin/python3 scripts/export_missing_files.py
```

The exporter checks each catalog checksum and file index, exact mini-path mapping,
missing-file evidence, unique paths, and the finalized omission total before writing.
