# Single-disk energy-threshold benchmark, 2026-09-29

The lowest cut in the requested 0.1-MeV grid that fits with the chosen reserve is **raw AD energy
strictly greater than 0.6 MeV**, keeping every non-AD trigger. The projected
archive is **11.838 TB**; the sample sensitivity envelope reaches **12.352 TB**.
The destination's free space was **13.999655 TB**, giving a 90% budget of
**12.599690 TB**. TB here means 10^12 bytes, not TiB.

| Raw AD cut (MeV) | Weighted estimate (TB) | Upper sample envelope (TB) | Fits 90% budget |
| --- | ---: | ---: | --- |
| Off | 18.568 | 19.268 | No |
| > 0.3 | 17.667 | 18.338 | No |
| > 0.4 | 15.995 | 16.649 | No |
| > 0.5 | 13.679 | 14.242 | No |
| **> 0.6** | **11.838** | **12.352** | **Yes** |
| > 0.7 | 10.672 | 11.151 | Yes |

The mounted destination is `/media/josiah/LAYJ萤捷1`, currently `/dev/sdb3`,
NTFS3, UUID **2A4F48F8BDFEE355**, total 14,000,187,224,064 bytes. Initially it
held only `System Volume Information`. The other similarly named disk has a
different UUID and existing data. Production pins the UUID before and after
each transfer.

## Measurement and validation

The frozen 48-file sample from `../noenergy-2026-09-27/plan.json` covers 20
period/hall/stream strata and all four periods and three halls. File counts
come from all 831,334 fixed-catalog inputs. For each threshold, the estimate
is `sum(stratum_file_count * mean_sample_output_bytes)`. The upper envelope
uses each stratum's largest observed file instead of its mean. These
deterministic extrema are a sensitivity check, **not a confidence interval**.

HepJob array **81751364** ran on 34 workers with the installed IHEP LCG 110
Python/ROOT environment. All **288 conversions** passed. Each output was
reopened; all 20 event columns, including `runNumber`, were compared bitwise
with the selected input rows in order. Per-AD discarded counts, source entry
accounting, timestamp endpoints, and durations were also checked. Source size
and modification time were unchanged. At 0.6 MeV, 27,215,377 of 36,262,542
sample events were retained, including all 16,133,557 non-AD events.

The sweep used production ZSTD level 9, 256 KiB baskets, and one source per
test output. Binaries were built from converter source SHA-256
`df4ae02c45dde4ba787332360dc885cdb7f4c171c53033b52b6a40428f13a1ac`
(commit `5479aef`), changing only the cut and corresponding count labels for
each temporary variant. Production uses the selected 0.6-MeV variant as
`dyb-slim-v5` / schema 4. Its renamed band is `adFrom0p3To0p6MeV`; the
previous 0.7-MeV format remains available at `5479aef` for older archives.

The projected 169 CPU-days at 0.6 MeV include converter input reads,
compression, and its built-in output verification on the sampled workers.
Queue time, transfer, finalization, and local checksum verification are
additional. Single-source headers modestly overestimate the 16-source chunk
format. Neither capacity nor completion time is guaranteed by this sample.

This is a **raw-energy-selected archive**. TWin applies corrections before
its 0.7-MeV normal or 0.3-MeV `--acpo` cuts. Those physics selections are not
proved equivalent to this preservation cut; discarded individual AD events
cannot be reconstructed from the aggregate counts. The campaign also does
not establish availability of every expected input. Production records an
exact-original fallback for unavailable minis; if both copies are unreadable,
their run stops without excluding the file.

## Recheck

The full-run pilot subsequently passed on **P17B/EH1/run21221**, cluster
**81751470**: 217 inputs, 14 ROOT chunks, 164,525,202 input entries and
131,036,010 retained entries. Its 2,683,398,113 artifact bytes (ROOT chunks
plus durations) were copied to the selected disk and all 15 SHA-256 hashes
verified before remote staging cleanup. [pilot.json](pilot.json) records the
measurement. The converter and ROOT integration tests passed on IHEP (12/12).

The 48 named JSONL result files and `summary.json` are the retained evidence.
The local projection requires only the standard library:

```bash
python3 estimates/energy-threshold-2026-09-29/benchmark.py check
python3 estimates/energy-threshold-2026-09-29/benchmark.py summarize \
  estimates/energy-threshold-2026-09-29/results --available 13999655329792
```

To repeat the conversions, stage `src/slim_root.cpp`, this directory's two
scripts, and the previous benchmark's `benchmark.py` and `plan.json` in
private worker-readable scratch. Run `run.sh build`, then submit 48 tasks
with `run.sh INDEX`, indices 0–47. For exact historical reproduction, supply
`src/slim_root.cpp` from `git show 5479aef:src/slim_root.cpp` in the temporary
payload; the current source has the same event selection but a new version.
The summarizer rejects incomplete, mixed-source, or failed measurements.

The threshold grid uses the requested 0.1-MeV steps.
