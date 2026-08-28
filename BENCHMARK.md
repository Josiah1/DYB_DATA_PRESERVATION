# Storage and throughput estimate

Measured on `dayabay1` on 2026-08-28 with ROOT from
`LCG_110/x86_64-el9-gcc13-opt`, GCC 13, ZSTD level 7, and the schema in
`dyb-slim-v2`. This was a bounded direct pilot: no HepJob task was submitted,
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

## Campaign estimate

The complete checksum-verified snapshot contains 4,009 run catalogs, 831,334
source paths, and 98,092,666 bytes. It was generated from the four authoritative
fixed-version sync master lists. All 3,874 jixp per-run copies readable during
the complete comparison were byte-identical to the generated snapshot; zero
mismatches were found.

| Resource | Best estimate | Planning value |
| --- | ---: | ---: |
| Slim archive | 19–21 TB decimal | **25 TB available** |
| CPU | 72.0 CPU-days from 7.486 s/source | **80–100 CPU-days** |
| Array tasks at 16 sources/task | 53,821 | 16 sources/task initially |
| Largest observed run | 1,091 sources, 69 tasks | about 25–30 GB peak staging |
| Typical task compute | about 2 minutes | `short` 30-minute queue |

Raw NumPy arrays for the retained primitive fields were estimated near 45 TB
before container/compression overhead, so NumPy is not the space-efficient
choice. Compressed ROOT stays directly usable by the existing analyses and is
the selected format.

The pilot transfer delivered the 75.9 MB ROOT payload at 56.37 MB/s; an earlier
repeat measured 36.99 MB/s. At 19.4 TB this corresponds to roughly 4.0–6.1 days
of continuous payload transfer. Queue latency, 4,009 sequential per-run
transactions, hashing, and operational interruptions dominate calendar time;
budget roughly 2–4 weeks rather than treating raw transfer time as the campaign
duration.

These are capacity estimates, not a production completion claim. Projected
bytes and task sizing should be refreshed after the explicit one-run production
pilot is complete.
