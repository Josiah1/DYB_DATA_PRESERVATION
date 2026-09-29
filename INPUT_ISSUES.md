# Unexpected input-file issues

Provisional record, updated **2026-09-29T17:28:32+00:00**. This is not the final campaign-wide
missing-file list: **828,445 expected inputs have not yet been assessed**.

## Coverage and findings

- Fixed catalog: 4,009 runs / 831,334 source files.
- Archived completed-run metadata: 11 runs / 2,763 preserved inputs.
- Additional live check: all 126 inputs in P17B/EH1/run21540.
- **14 mini-file access failures recorded**: 12 were recovered from their exact
  originals and preserved; 1 has a readable original header but is not yet
  processed; 1 remains unavailable from both attempted sources.

The archived observations come from each completed run's `run.json`; this check
did not repeat output checksums. The new source check opens files read-only and
checks their four-byte ROOT headers; it does not establish full ROOT readability.
Exact catalog paths, zero-based catalog indices, catalog checksums, observations,
and error messages are retained in [input-issues.json](input-issues.json).

| Period | Hall | Run | Source-file number | Recorded disposition |
|---|---|---|---|---|
| P17B | EH1 | run21228 | `0200` | Preserved from exact original |
| P17B | EH1 | run21235 | `0076` | Preserved from exact original |
| P17B | EH1 | run21358 | `0056` | Preserved from exact original |
| P17B | EH1 | run21367 | `0216` | Preserved from exact original |
| P17B | EH1 | run21367 | `0259` | Preserved from exact original |
| P17B | EH1 | run21367 | `0303` | Preserved from exact original |
| P17B | EH1 | run21505 | `0006` | Preserved from exact original |
| P17B | EH1 | run21508 | `0041` | Preserved from exact original |
| P17B | EH1 | run21508 | `0139` | Preserved from exact original |
| P17B | EH1 | run21508 | `0217` | Preserved from exact original |
| P17B | EH1 | run21520 | `0065` | Preserved from exact original |
| P17B | EH1 | run21520 | `0185` | Preserved from exact original |
| P17B | EH1 | run21540 | `0103` | Mini missing; original I/O error; blocked |
| P17B | EH1 | run21540 | `0159` | Original header readable; not processed |

## Current blocker

For **P17B/EH1/run21540, file 0103**:

- Mini: `/lustrefs/juno26/dyw/rec/All-mini/runs_0021000/runs_0021500/run_0021540/minirecon.Neutrino.0021540.Physics.EH1-Merged.P17B-I._0103.root`
- Mini result: `FileNotFoundError`, errno 2 (no such file).
- Exact original: `/dybfs/rec/P17B/rec/runs_0021000/runs_0021500/run_0021540/recon.Neutrino.0021540.Physics.EH1-Merged.P17B-I._0103.root`
- Original result: `OSError`, errno 5 (input/output error).

The original is unreadable in this observation; its permanent absence has not
been established. No input was intentionally omitted, and no campaign restart
was performed while preparing this record.

## Final campaign record

Retain every unexpected access failure, including mini failures successfully
recovered from exact originals. At campaign close, refresh this record against
all fixed-catalog entries and reconcile preserved, explicitly omitted,
unavailable, and unassessed inputs with the expected total. Keep missing paths,
I/O errors, and invalid ROOT headers distinct. Do not label unassessed files as
missing or call the archive complete while catalog gaps remain.

The Ubuntu disk copy is under
`/media/josiah/LAYJ萤捷1/DYB_DATA_PRESERVATION/provenance/` on disk UUID
`2A4F48F8BDFEE355`. This record supplements existing per-run provenance; it does
not change the pipeline's current stop-on-unavailable-input behavior.
