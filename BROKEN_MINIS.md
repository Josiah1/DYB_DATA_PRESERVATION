# Confirmed broken-mini policy and recovery

On 2026-10-11 the user decided: “just record them as broken files besides the
missing files”. This authorizes skipping confirmed broken default All-mini
inputs while preserving their full expected catalog identities and processing
the usable inputs. It does not authorize original reconstruction fallback or
generic error skipping.

## Proof and error boundary

For the currently observed failure, a worker must exit 1 with only
`ERROR missing required tree: <exact mini path>` on stderr, naming an input of
that exact fixed chunk. Independent ROOT inspection under the configured IHEP
LCG environment must then establish all of:

- The file exists and is readable; ROOT opens it without errors, zombie state
  or the recovered-file flag.
- Its only top-level key is `AdEnergy`, class `TNtuple`, with zero entries.
- Both `Event/Rec/AdSimpleNL` and `Event/Data/CalibStats` are absent.
- File size and modification time remain stable through inspection and SHA-256
  hashing. Exact path, full-catalog index/entry/hash, inspection time, byte size,
  SHA-256, reason, ROOT flags/keys and original worker failure evidence persist.

After proving every current failed chunk's first error, the controller inspects
the remaining inputs in those failed chunks. Files with both event trees remain
converter inputs; that inspection does not certify all their branches or events.
Every additional broken skip requires the same strict empty-tuple proof. An
unknown signature, ROOT warning/error, unreadable file, permission/I/O error or
changing file stops recovery without treating the input as broken or missing.
Changed worker-failure snapshots defer/recheck at most eight times. Missing-file
preparation still requires consecutive ENOENT and retains its storage-outage
protections. The earlier approximate missing fraction is never forced.

## Stable chunks and restart safety

The controller alone owns recovery, under its existing local lock and remote
submission lock. It first confirms that the run has no scheduler jobs, worker
markers or ambiguous submission, and is not already finalized. Other campaign
jobs are retained. Full `catalog.list` bytes and catalog inventory stay fixed.

The original missing-only preparation compacts confirmed missing paths before
splitting chunks. Broken removals filter only within those original boundaries;
they never re-split dense processed inputs. Good chunk lists, ROOT outputs,
source reports and success evidence remain intact. A changed chunk with any
existing ROOT output or verified success is rejected for inspection. Worker
reuse checks source-report identity/order against the current chunk list before
accepting an existing verified output.

An empty chunk has an explicit empty status and a header-only source report,
with no ROOT output or invented event data. The original chunk slot/number is
retained. `durations.tsv` has dense processed `fileIndex`, stable `chunkIndex`
and each surviving chunk's local `sourceIndex`; chunk indices can have gaps.
Even an all-broken run requires independent proof for every omitted input; it
can finalize a gap-only transaction but cannot become catalog-complete.

A small write-ahead recovery journal permits completion after interruption;
replay checks immutable metadata, prior gaps, exact source identities and fresh
matching broken-file hashes. Successful recovery advances the retry baseline
only when new broken inputs were proved. Generic failure retries remain bounded.
Original worker failure text/hash/time is retained in checksummed gap evidence.

## Records, reports and compatibility

Per-run `resolved_inputs` remains aligned to every expected catalog entry, with
`null` only for a validated skip. `skipped_inputs` has distinct `missing` and
`broken` categories (older untagged gaps mean missing). New broken-gap manifests
must satisfy:

```text
input_count = processed_input_count + missing_skipped_input_count + broken_skipped_input_count
skipped_input_count = missing_skipped_input_count + broken_skipped_input_count
```

`input-gaps.json`, covered by `SHA256SUMS`, contains exact inputs, proof,
category counts and worker failure evidence. A `.complete` marker means the run
transaction was verified, not that its catalog has no gaps. ROOT schema 4,
selection, ZSTD/baskets and historical original-source exceptions are unchanged.
Older finalized runs and missing-only gap reports remain verifiable.

Portable `DYB_DATA_PRESERVATION/provenance/` contains automatically updated:

- `input-issues.json`: all categorized gap observations and historical fallback
  observations, with finalized `skipped` versus prepared `skip_recorded` status.
- `INPUT_ISSUES.md`: reconciliation and confirmed missing-mini filenames.
- `BROKEN_INPUTS.md`, `broken_files.list`, `broken_files.csv` and
  `broken-input-issues.json`: separately exported confirmed broken filenames and
  proof. Prepared observations are not counted as finalized archive skips.
- `pending-input-issues.json`: historical hold/inspection evidence and current
  dispositions. Released holds do not retain a current unanswered decision or
  single held-run claim; historical evidence remains explicitly labeled.

Archive totals separately expose `missing_skipped_inputs` and
`broken_skipped_inputs`, retaining total `skipped_inputs` for compatibility.
Unfinalized expected inputs stay pending. Final reconciliation distinguishes an
archive finished with recorded gaps from a gap-free complete archive.

## Validation and operational evidence

Initial evidence: 93 distinct previously observed paths across seven runs, whose
full catalogs contain 1,590 inputs. All 93 were revalidated read-only before the
change. They are an identified set, not a complete census. Thirty-seven good
chunks in run51237/run51350 were recorded before recovery for preservation checks.

Focused tests cover proof/category/catalog/count trust boundaries, old missing
outage checks, preserved good chunks after removals, empty/all-broken chunks,
source-list reuse rejection, changing failure snapshots, journal replay,
worker-free retry and unaffected transfers, and separate exports/current holds.
IHEP LCG passed ten broken-policy tests (including a real ROOT skip/resume and
schema-4 content verification) and three existing proof/hold tests. Local tests:
41 run, 37 passed and four supported-runtime skips; shell syntax, whitespace and
all 4,009 fixed catalog checks passed. Existing finalized run47033 passed current
checksum/metadata verification (26 inputs; 324,600,532 artifact bytes). Results are in the ignored
`build/oct11-*` results. No full-archive rehash, bulk copy or new dependency is
part of this change. Live deployment results are summarized in
`build/oct11-broken-result.json` after the controlled transition.

Worker metadata validation clears inherited `PYTHONHOME`/`PYTHONPATH`, matching
the existing remote Python invocation. A deliberately poisoned environment is
tested for both successful good-output reuse and empty-chunk handling; changing
the list still rejects reuse and preserves the original output/report/status.

The established missing exporter and test were relayed from the Mac because
they were absent on Ubuntu. Their supplied pre-change SHA-256 hashes were
verified before editing: `e3bbd0f940c3f8d3510467dcea15dd443ab12cbd957f458dc0d9de5a2c455e0d`
and `af07f70580864a7bd4df4dc0bb34de6a8bfce20d1f6bb83c67be34832c0a4d7d`.
The exporter excludes broken records, validates finalized missing rows against
`missing_skipped_inputs` with the old total as fallback, and regenerates separate
broken exports from the same copied snapshot. The source ledger snapshot and
missing/broken reports are tracked locally and updated in place after recovery.

## Applied recovery snapshot, 2026-10-11 UTC

Runtime changes were committed locally as `3068422` and `3389fbc`, then deployed
under the remote submission lock before one controller was restarted. The
converter subprocess fixture correction is local commit `4490f57`; its affected
end-to-end test passed under IHEP LCG ROOT. Independent Mac validation subsequently
reported 41 tests: 38 passed and three expected platform/LCG skips. Nothing was
pushed.

Independent inspection of failed chunks established the following full-catalog
accounting. The earlier 93 observations were not used as a forced total.

| P17B/EH1 run | Expected | Usable inputs | Missing skips | Broken skips |
| --- | ---: | ---: | ---: | ---: |
| run51237 | 389 | 358 | 0 | 31 |
| run51243 | 112 | 14 | 0 | 98 |
| run51250 | 158 | 19 | 0 | 139 |
| run51255 | 248 | 38 | 1 | 209 |
| run51263 | 21 | 1 | 0 | 20 |
| run51344 | 416 | 65 | 4 | 347 |
| run51350 | 246 | 239 | 0 | 7 |
| Total | 1,590 | 734 | 5 | 851 |

All seven holds were released and all seven runs received recovery submissions.
Fresh checks through 05:55:33 UTC found run51237, run51243, run51250, run51255 and
run51344 complete in staging, run51350 still processing, and run51263 already
saved and verified on the portable disk. Run51263 contains one ROOT output,
13,056,219 artifact bytes, one processed mini and 20 separately checksummed broken
gaps; its full 21-input catalog remains intact and `catalog_complete` is false.

All 37 pre-existing good chunks in run51237/run51350 retained identical chunk-list,
source-report and success-evidence SHA-256 values, plus identical ROOT output byte
sizes and modification times, before and after recovery submissions. These checks
did not rehash their bulk ROOT data; normal run verification checks final outputs
before remote cleanup.

The copied report snapshot is dated **2026-10-11T05:57:41+00:00**: 590 verified
local runs, 138,014 processed inputs, 656 finalized missing skips, 20 finalized
broken skips and 692,644 pending inputs, totaling all 831,334 expected inputs.
Artifact bytes were 1,756,930,894,692. The separate filename exports contain 788
confirmed missing paths and 851 proved broken paths, with no overlap. Prepared
observations are included in those filename lists but excluded from finalized
skip totals; all reports share the copied ledger timestamp. This is an incomplete
archive with recorded gaps.

A subsequent fresh IHEP read at 05:57:08 UTC confirmed all six remaining affected
runs COMPLETE with no jobs of their own; all seven have therefore finished their
compression work, with six awaiting verified transfer. All 37 old good chunks
still matched the recorded preservation checks. The live queue had 79 other
running single-core jobs, zero idle/held, with 390,797,285,408 reserved bytes below
400 GiB. These counts are a dated observation, not a claim of constant utilization.

The controller/checker now show no holds, no pending policy decision and enabled
new-run admission. Legacy decision/scope fields remain under
`historical_hold_evidence`. One controller owns transfers, submissions and cleanup;
the 300 single-core job and 400-GiB staging limits remain unchanged. Portable
verifier/runbook snapshots and the root README policy were updated in place; the
README's earlier dated inventory snapshot remains explicitly historical. Its 32
relative links and readback were checked on the correct rw NTFS-3G disk.

The ignored `build/oct11-broken-result.json` records fresh runtime evidence; its
timestamps and the portable controller/progress ledgers supersede this static
snapshot. Bulk outputs remain solely on the portable disk or retained IHEP staging.
