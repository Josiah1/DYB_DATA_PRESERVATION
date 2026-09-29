# All-event IHEP-mini compression benchmark

This is a bounded benchmark, not a production campaign or a change of defaults.
Every source event is retained (`--ad-selection off`), along with all 19
required source fields, their original primitive types, generated `runNumber`,
and per-source duration/provenance metadata. No rounding or reduced precision
is used.

## Result

**Share with caveats:** the test passed on all 48 files (36,262,542 events)
and all 100 paired alternative outputs, with zero value or duration mismatches.
The projected current-ROOT size is **19.02 TB**, or **16.69 TB with LZMA**;
current conversion projects to **65.45 CPU-days**. The sample does not establish
archive-wide completeness or a statistical confidence interval. See the
[comparison and recommendation](../../BENCHMARK.md#current-all-event-estimate-ihep-minis-2026-09-27)
for all six configurations and CPU trade-offs. Defaults remain unchanged.

## Evidence and method

- `plan.json`: the 48 input paths, required schema, fixed-catalog population
  weights, and converter/catalog-manifest hashes. Generating it rechecks every
  catalog checksum and uniqueness of all 831,334 source paths.
- `results.jsonl`: source-by-source byte counts, double-precision wall/CPU
  seconds, original timing TSV rows, full-column bitwise hashes, and paired
  alternative-format measurements.
- `summary.json`: independently reproducible weighted size/CPU projections.
- `benchmark.py` and `run.sh`: the complete bounded experiment. The runner
  builds the existing converter from source in a private temporary directory,
  validates its hash, and removes the temporary payload and test outputs on
  exit. Only the small result log needs to be retained.

Inputs reuse the [48-file branch-check sample](../mini-branches-2026-09-27/README.md):
up to three run quantiles per data-version/hall/stream stratum, and the middle
listed file of each selected run. All four fixed versions and three halls
are represented; NoTag is included wherever present. There are 20 strata.
One middle-quantile file from each stratum is used for five paired encoding
tests. This is a deterministic, archive-listed sample, not a random sample.

The historical baseline is the `dyb-slim-v3` converter, ROOT compression
setting 507. Alternatives use the same events and fields:

| Variant | Settings |
| --- | --- |
| `root_z7_b256` | ROOT ZSTD level 7, 256 KiB baskets |
| `root_z9_b256` | ROOT ZSTD level 9, 256 KiB baskets |
| `root_lzma7_b256` | ROOT LZMA level 7, 256 KiB baskets |
| `parquet_gzip6_delta_bss` | GZIP 6, integer delta encoding, float byte-stream split |
| `parquet_brotli6_delta_bss` | Brotli 6, the same lossless encodings |

ROOT repacking rewrites baskets, rather than copying already compressed
baskets, and explicitly sets each branch's compression. ROOT's level scale
is 1–9; it is not interchangeable with native ZSTD's level scale. The initial
smoke test requested levels 12/19, which were effectively capped; those trials
are not used in the final projections. See the
[ROOT compression settings](https://root.cern.ch/doc/v638/structROOT_1_1RCompressionSetting.html).

The pinned cluster PyArrow build has GZIP/Brotli support but no ZSTD support,
so **Parquet+ZSTD was not measured** and no dependencies were installed.
Parquet uses 262,144-row groups, 256 KiB pages, no dictionary/statistics,
page checksums, and an embedded preservation-metadata record. These are
[standard Parquet writer options](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.write_table.html),
not a custom data format.

## Validation and timing boundaries

Each baseline is compared against every value of every retained source
column, in order, with matching types and bitwise SHA-256 hashes. Every
alternative is decoded and compared with those same hashes. The Parquet
self-check includes NaN payloads, signed zeros, infinities, unsigned values
above `INT_MAX`, and signed 16-bit limits. ROOT alternatives also pass the
production structural verifier.

For every source, the test independently derives the minimum/maximum integer
nanosecond timestamps and checks the duration and `entries = keptEntries`.
The original 17-significant-digit timing row is preserved in each result and
in Parquet metadata; ROOT alternatives preserve `Preservation/Sources`.
File size/mtime are checked before and after the read-only source use.

Preserved timing metadata describes the baseline conversion; each alternative's
encoding/decoding times are recorded separately in its result row.
Baseline CPU includes the actual converter child process, including its
structural verification. This experiment builds it with `-O2`; the production
Makefile uses `-O3 -DNDEBUG`. Alternative times cover repacking/encoding only:
ROOT starts from an already-slim ROOT file, and Parquet starts from in-memory
arrays. They are **not alternative end-to-end campaign CPU estimates**.
Decode times include materializing all retained columns but exclude hash
comparison. Tests are serial on the login node, not scheduled worker
benchmarks; cache/load effects and worker hardware can change throughput.

## Projection

For each stratum `g`, let `N[g]` be its fixed-catalog file count and `B[g]`
the mean baseline output bytes of its sampled files. Then:

```
baseline bytes = sum_g N[g] * B[g]
alternative bytes = sum_g N[g] * B[g] * (paired alternative bytes / paired baseline bytes)
baseline CPU-days = sum_g N[g] * mean sampled converter CPU seconds[g] / 86400
```

TB means 10^12 bytes; TiB means 2^40 bytes. The sensitivity envelope replaces
each stratum's mean by its observed minimum/maximum. It is **not a confidence
interval**. Alternative projections use one paired ratio per stratum and
are less well sampled than the baseline. Per-source test files have slightly
more header overhead than production's 16-source chunks.
The uncompressed-array comparison counts 76 primitive bytes/event (including
generated `runNumber`), before file/container overhead; it is not a claim
about compressed NumPy formats.

The projection is for **all 831,334 expected files**, including the 4,087
unlisted in the September 2 archive index. This benchmark does not resolve
their availability. It also does not establish original-to-mini equivalence
outside the separately checked six pairs or TWin end-to-end physics results.
This benchmark predates the compatible output filenames and the separate
[current-GitHub TWin regression](../../TWIN_COMPATIBILITY.md).

## Reproduce

For an exact rerun of this historical benchmark, use a separate checkout of
preservation commit `edd775f7a44ae69de7a82ffa72582b6df7299e50`; the frozen plan
pins the old converter SHA-256. Do not replace its converter with v4 or
overwrite this recorded result with new-default measurements. From that
checkout's repository root, the plan can be regenerated locally:

```bash
python3 estimates/noenergy-2026-09-27/benchmark.py plan . > estimates/noenergy-2026-09-27/plan.json
```

On IHEP, with this repository in a writable working area, run the bounded
experiment (use `1` instead of `48` for a one-file smoke test):

```bash
tar -czf - src/slim_root.cpp estimates/noenergy-2026-09-27/benchmark.py estimates/noenergy-2026-09-27/plan.json \
  | bash estimates/noenergy-2026-09-27/run.sh 48 > estimates/noenergy-2026-09-27/results.jsonl
```

The runner sources the existing LCG 110 EL9/GCC13 view and uses only installed
ROOT, NumPy and PyArrow. It may also be invoked through the SSH operations
`remote-run.sh` helper with that same tar stream, as in this experiment.
Copy the named result log locally, then calculate the report without ROOT:

```bash
python3 estimates/noenergy-2026-09-27/benchmark.py summarize \
  estimates/noenergy-2026-09-27/plan.json estimates/noenergy-2026-09-27/results.jsonl \
  > estimates/noenergy-2026-09-27/summary.json
make test
```

The summarizer refuses missing baseline samples, incomplete paired-stratum
coverage, failed comparisons, or a missing successful terminal record.
`tests/test_benchmark.py`, included in `make test`, independently checks the
recorded projections using rational per-file weights, rechecks the timing
rows, and confirms that truncated or failed result sets are rejected.
