# Storage and throughput estimate

## Single-disk selection: 2026-09-29

The [new worker benchmark](estimates/energy-threshold-2026-09-29/README.md)
tested no cut and AD cuts at 0.3, 0.4, 0.5, 0.6, and 0.7 MeV on the same
48 files. All 288 outputs passed full retained-value and duration checks.
**Raw AD energy > 0.6 MeV** is the lowest tested cut whose upper sample
envelope fits within 90% of the empty disk's 14.00 TB free space: **11.84 TB**
weighted estimate, **12.35 TB** upper envelope. At 0.5 MeV the upper envelope
is 14.24 TB. Every non-AD trigger is retained. Production uses schema 4 /
`dyb-slim-v5`, with the 0.6-MeV band explicitly named in counts and metadata.
This selection is not a lossless substitute for TWin's corrected-energy cuts.

The measured selected conversion projects about 169 CPU-days on the sampled
batch workers, excluding queue time, transfer and final verification. Per-run
timing during production provides the operational estimate.

## Previous lossless benchmark

The current no-energy-cut IHEP-mini benchmark is documented in
[`estimates/noenergy-2026-09-27/`](estimates/noenergy-2026-09-27/README.md).
It measured the then-current ZSTD-7 converter with `--ad-selection off` and
five lossless alternatives. On September 28, ZSTD 9 / 256 KiB baskets and
selection off were adopted as defaults; the historical measurements below
are unchanged. See [TWin compatibility](TWIN_COMPATIBILITY.md) for the new pilot.

## Current all-event estimate: IHEP minis, 2026-09-27

**Without an energy cut, estimate 18.59 TB with the adopted ZSTD-9/256-KiB setting, or
16.69 TB with the tested ROOT/LZMA alternative. Plan for 25 TB of usable
destination space for one archive copy.** A 10 TB target was not achieved
by any tested lossless option; 50 TB is not needed for the compressed copy.
Independent backup capacity is additional.

The bounded test converted **48 files / 36,262,542 events**, covering all four
fixed data versions, three halls, and both streams wherever present. Every
retained source value matched bit-for-bit, in order, and all per-source
durations matched independently derived integer timestamp differences.
All **100 alternative outputs** (five variants on 20 paired files) passed
their full-column round trips. No production jobs were submitted.

The population is the complete fixed-catalog set of **831,334 files**.
Each data-version/hall/stream is weighted by its exact file count; alternatives
use a paired size ratio within each of the 20 strata.

| Lossless configuration, all events retained | Projected TB | Change from old baseline |
| --- | ---: | ---: |
| Previous ROOT, ZSTD 7 | 19.02 | baseline |
| ROOT, ZSTD 7, 256 KiB baskets | 18.85 | -0.9% |
| ROOT, ZSTD 9, 256 KiB baskets (adopted) | **18.59** | -2.3% |
| ROOT, LZMA 7, 256 KiB baskets | **16.69** | **-12.2%** |
| Parquet, Brotli 6, delta/byte-stream-split encoding | 21.08 | +10.8% |
| Parquet, GZIP 6, delta/byte-stream-split encoding | 21.34 | +12.2% |

TB is decimal (10^12 bytes); the old 19.02 TB baseline is **17.30 TiB**.
The observed within-stratum size-sensitivity envelope is **17.85–19.75 TB**
for the old baseline. This is not a statistical confidence interval: samples
were chosen deterministically at run quantiles, and unsampled files may
differ. Alternative estimates use only one paired file per stratum. These
figures include projected capacity for the 4,087 files unlisted in the earlier
archive index; the benchmark does not resolve their availability.

The previous ZSTD-7 conversion projected **65.45 CPU-days**, or 6.80 CPU-seconds per
source, with an **80–100 CPU-day planning allowance** pending an actual
batch-worker pilot; transfer, archive hashing, queue delays and failures are
additional. This measurement used the old source built with `-O2` on the
login node; production uses `-O3 -DNDEBUG` and may run on different hardware.

LZMA saves about **2.33 TB**, but on the 20 matched files its repacking CPU was
**2.27 times** ZSTD-7/256-KiB and its full-column decoding CPU was **2.05 times**
as high. ZSTD-9 repacking took 2.11 times as much CPU for only a small size
gain. These are paired encoding/decoding measurements, not complete ZSTD-9,
LZMA or Parquet production-pipeline timings. The old CPU allowance must be
remeasured for the adopted configuration before production scheduling.

LZMA changes the codec, not the tree/branch layout. No defaults were changed
by the September 27 benchmark itself. The September 28 implementation adopts
ZSTD 9, larger baskets, all events and TWin-compatible filenames; the current
GitHub TWin reader already uses the two retained trees without AdScaledNL.
The [ROOT codec choices](https://root.cern.ch/manual/io/#compression-and-performance)
do not by themselves establish analysis compatibility.

Uncompressed arrays with these 20 output scalars would occupy about **48.34
TB** before container overhead; this is not an estimate for compressed NumPy.
The two tested Parquet settings were larger than ROOT, but this is not a claim
about every possible Parquet encoding. Parquet+ZSTD was unavailable in the
installed PyArrow build and remains unmeasured. Lossy quantization or event
removal could reduce storage further, but neither was attempted or validated.

Reproduction instructions, limitations and per-file double-precision timings
are in the [experiment record](estimates/noenergy-2026-09-27/README.md);
[`summary.json`](estimates/noenergy-2026-09-27/summary.json) contains the
machine-readable projections. Independent rational-arithmetic size checks,
CPU reweighting, timing-row checks and negative completeness tests passed.

## Adopted-format compatibility pilot: September 28

The new `dyb-slim-v4` converter processed two adjacent P17B/EH1 files,
1,509,445 events, into one ZSTD-9/256-KiB chunk: 100,089,201 input bytes to
40,919,903 output bytes. End-to-end conversion used **37.4112 CPU seconds /
37.6355 wall seconds** on the IHEP login node, built with `-O3 -DNDEBUG`.
These two files are a compatibility sample, not the stratified population
sample; do not multiply this timing into an archive-wide CPU budget. The
[result](estimates/twin-compatibility.json) also retains 17-digit per-file
timings and [TWin input/physics checks](TWIN_COMPATIBILITY.md). Production was
not started and cluster analysis source copies were not changed.

## Historical original-reconstruction pilot

The original-reconstruction measurements below are retained as historical
evidence, not as current mini-input throughput measurements. The one-file
local macOS/arm64 mini smoke test is also not a cluster projection
(`estimates/mini-branches-2026-09-27/conversion-check.json`).

Measured on `dayabay1` on 2026-08-28 with ROOT from
`LCG_110/x86_64-el9-gcc13-opt`, GCC 13, ZSTD level 7, and the schema in
`dyb-slim-v2`, which wrote every entry; it corresponds to `dyb-slim-v3` with
`--ad-selection off`. This was a bounded direct pilot: no HepJob task was submitted,
no production staging path was used, and no source data was modified.

## Real-data pilot

One representative P17B source from each experimental hall was compressed in
a single three-source test chunk:

| Hall | Run/file suffix | Entries | Input bytes | Data duration (s) | Wall (s) | CPU (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| EH1 | 0048731 / `_0071.root` | 756,795 | 697,752,723 | 603.01409791200001 | 7.5730714800000003 | 7.5311190000000003 |
| EH2 | 0048610 / `_0157.root` | 750,641 | 695,803,061 | 743.59818258799999 | 7.3961734730000002 | 7.3740859999999993 |
| EH3 | 0048236 / `_0071.root` | 841,061 | 765,743,090 | 1333.089663687 | 7.4180082619999999 | 7.3825330000000005 |
| **Total** | 3 files | **2,348,497** | **2,159,298,874** | — | **22.6433** | **22.4587** |

The slim ROOT output was 75,892,644 bytes, or 3.515% of the original ROOT byte
size for these samples. The finalized preservation artifacts, including the
run-level duration table, were 75,893,566 bytes. Timing metadata is negligible
relative to event data.

## Correctness checks passed

- the converter's structural reopen checked all required trees and branches,
  equal event-tree entry counts, all three source rows, and source/event totals;
- 171 values were compared between original and compressed files: 19 retained
  analysis branches at first, middle, and last entry of each source;
- all 12 independent duration checks passed: event count, earliest timestamp,
  latest timestamp, and nanosecond-derived double duration for each source;
- all three 11-column source timing rows parsed and validated;
- the production finalizer reopened the ROOT output under the pinned LCG
  environment, aggregated `durations.tsv`, and wrote SHA-256 metadata;
- the actual worker script processed a one-source chunk, published its ROOT,
  timing report, and success marker, then reopened and skipped it on a second
  invocation without leaving failure or running markers;
- `rsync` transferred the finalized run locally; source and destination hashes
  matched; `verify-all` reported one complete run, three inputs, and two exact
  artifacts;
- zero value or timing mismatches were observed.

## Historical campaign estimate

The complete checksum-verified snapshot contains 4,009 run catalogs, 831,334
source paths, and 98,092,666 bytes. It was generated from the four authoritative
fixed-version sync master lists. All 3,874 jixp per-run copies readable during
the complete comparison were byte-identical to the generated snapshot; zero
mismatches were found.

| Resource | Best estimate | Planning value |
| --- | ---: | ---: |
| Slim archive, historical AD selection on | about 12 TB (11.5–12.0 TB) | **25 TB available** |
| Slim archive, selection off | 19–21 TB decimal | 25 TB available |
| CPU | 72.0 CPU-days from 7.486 s/source | **80–100 CPU-days** |
| Array tasks at 16 sources/task | 53,821 | 16 sources/task initially |
| Largest observed run | 1,091 sources, 69 tasks | about 25–30 GB peak staging |
| Typical task compute | about 2 minutes | `short` 30-minute queue |

Raw NumPy arrays for the retained primitive fields were estimated near 45 TB
before container/compression overhead, so NumPy is not the space-efficient
choice without compression. At the time of this historical pilot, the old
TWin reader depended on AdScaledNL and the output filenames lacked its run
field. The current GitHub reader and new filenames resolve those differences;
this does not update old cluster copies.
See the [source audit](estimates/mini-branches-2026-09-27/README.md#residual-flasher-inputs-in-src_fd).

The pilot transfer delivered the 75.9 MB ROOT payload at 56.37 MB/s; an earlier
repeat measured 36.99 MB/s. At 19.4 TB this corresponds to roughly 4.0–6.1 days
of continuous payload transfer. Queue latency, 4,009 sequential per-run
transactions, hashing, and operational interruptions dominate calendar time;
budget roughly 2–4 weeks rather than treating raw transfer time as the campaign
duration.

The selection study selected 48 sources, four per period and hall; 44 were
readable for counting, and 12 (one per period/hall combination) were actually
converted and rewritten with the production compression. AD entries compress
to about 47 bytes each and water-pool/RPC
entries to 11–20 bytes each, so keeping AD entries above 0.7 MeV plus every
other detector retains 57–60% of the bytes. The raw counts, converter results,
and the re-runnable analysis are under `estimates/adsel-2026-09-03/`.

These are capacity estimates, not a production completion claim. Projected
bytes and task sizing should be refreshed after the explicit one-run production
pilot is complete.
