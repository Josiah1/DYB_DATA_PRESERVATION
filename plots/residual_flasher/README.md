# Variables used by the residual-flasher cuts

“Identification variables” means the inputs to the rejection conditions in
`/dybfs2/users/lijj16/TWin/Retw_lijj/src_FD/BuildEvent.C`, not independently
confirmed flasher labels. This is a diagnostic canvas, not a purity,
efficiency, background-rate, or journal-ready result.

![Eight one-dimensional count distributions: gray dotted all-AD triggers,
blue solid preselected triggers, and orange dashed residual-cut-flagged
triggers. Flagged triggers are concentrated at high reconstructed z.
Per-panel annotations report values outside the displayed range and
nonfinite values.](residual_flasher.png)

[Vector PDF](residual_flasher.pdf) · [Histogram data](histograms.json)

## Sample and findings

Read-only collection on `dayabay1` on 2026-09-27 used three P21A/EH3
Neutrino mini-files: run 81743/file 0234, run 82379/file 0092, and
run 82989/file 0128. Exact paths, sizes, entry counts, ROOT version and
collection time are recorded in `histograms.json`. These are a small
diagnostic sample, not a representative estimate over every period/hall.

| Population | Triggers |
| --- | ---: |
| All AD triggers (detector 1–4) | 2,079,464 |
| Energy + standard flasher preselection | 925,747 |
| Residual-cut flag within that preselection | 4,123 |

- The flagged fraction of the preselection is **0.44537%**. No muon
  identification/veto or coincidence selection was applied.
- 4,116 of the 4,123 flagged triggers (99.83%) have reconstructed
  `z > 2200 mm`. This largely reflects the high-z conditions explicitly
  used by groups A and B, not independent evidence of flasher truth.
- Individual conditions flag 3,376 (`A && Aa`), 547 (`A && Ab`),
  2,829 (B), and 7 (C) preselected triggers. These sets overlap; their sum
  must not be used as the total rejected count.
- Raw AD inputs contain nonfinite `Q1/Q2`, `Kurtosis`, and `MaxQ`
  values. All eight plotted variables are finite after the stated
  preselection in this sample. Out-of-range finite tails remain and are
  counted explicitly; they are not clipped into edge bins.
- `MaxQ_2inchPMT` ranges from 0 to 0.82895 in the raw AD sample, so the
  code's `< 100` condition rejects none of these sampled AD triggers.
  Its panel shows `[0, 1)` to resolve the distribution and explicitly notes
  that the threshold lies outside the view. Native units are retained;
  no calibration-unit interpretation is inferred from these values.

## Definitions and plotting choices

Preselection is `AdSimpleNL.energy > 0.7 MeV`,
`sqrt(Quadrant^2 + (MaxQ/0.45)^2) < 1`, and `MaxQ_2inchPMT < 100`.
The residual flag is `(A && Aa) || (A && Ab) || B || C`, copied from the
active Zhiyuan conditions at `BuildEvent.C:108-116`. Every expression is
saved verbatim in the JSON and in `collect.sh`; no cut was fitted to this
sample. `QMax2Sum` in the analysis is `CalibStats.MaxQ`.

The first six panels show residual-cut inputs (`Q1/Q2`, `Kurtosis`,
`time_PSD_local_RMS`, `MaxQ`, `z`, `R`); `Quadrant` and
`MaxQ_2inchPMT` provide the standard-flasher-cut context. Positions are
converted from mm to m. PSD-RMS and 2-inch units are labeled as native
because their calibration units were not checked.

Histograms use unweighted trigger counts, no normalization or smoothing,
linear x axes and base-10 logarithmic y axes. Zero-count bins are gaps;
they are not changed to positive counts. These are observed sample counts,
not an estimator with uncertainty intervals. JSON stores every bin edge,
all counts, underflow, overflow, nonfinite counts, and finite minima/maxima
for each population. Bins are lower-inclusive and upper-exclusive.
Vertical guides mark coordinates in the conditional cuts, not independent
one-dimensional acceptance boundaries. Color is redundant with line style.

## Reproduce and validate

The stored histogram data are sufficient to regenerate the canvas offline:

```bash
~/TOOLs/install/python-current/bin/python3 plots/residual_flasher/plot.py
```

Rendering used Matplotlib 3.11.1 and NumPy 2.5.1. The tracked style is reused
locally, without requiring a skill installation or cluster connection.
Outputs are an opaque 3400 × 1880 PNG (200 dpi) and a one-page 17 × 9.4 inch
vector PDF with embedded TrueType fonts. No publisher requirements are
asserted. The PNG and rendered PDF were visually inspected for readability
and clipping. Rendering asserts that bins + flows + nonfinite values sum
to the population total and that flagged counts are a preselection subset.

To refresh only these three files, from the repository root:

```bash
bash ~/.agents/skills/ssh-ops/scripts/remote-run.sh dayabay1 \
  plots/residual_flasher/collect.sh --pubkey-auth \
  > plots/residual_flasher/histograms.json
~/TOOLs/install/python-current/bin/python3 plots/residual_flasher/plot.py
```

The collector loads the LCG 110 ROOT 6.40.02 view, reads the two aligned
trees, and emits reduced histograms to stdout. It modifies no ROOT input,
starts no production job, and writes no persistent cluster output. Its
collection time is not a conversion-throughput benchmark.

The scientific-visualization skill guided the explicit handling of missing
values, histogram flows, redundant visual encodings and output inspection;
its MIT-licensed publication style is reused here. Software reference:
Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026),
[Scientific Agent Skills: A Library of Procedural Knowledge for Research
Agents](https://doi.org/10.48550/arXiv.2609.00065).
