#!/usr/bin/env python3
"""Render one eight-panel canvas entirely from the locally stored histogram data."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT / "histograms.json").read_text())
totals = data["totals"]
groups = (
    ("all_ad", "All AD triggers", "#777777", ":", 1.2),
    ("preselected", "Energy + standard flasher preselection", "#0072B2", "-", 1.6),
    ("residual_flag", "Residual-cut flag within preselection", "#D55E00", "--", 1.6),
)
labels = {
    "q_ratio": (r"$Q_1/Q_2$", []),
    "Kurtosis": ("Kurtosis", []),
    "time_PSD_local_RMS": ("time_PSD_local_RMS [native units]", [14]),
    "MaxQ": ("MaxQ (= QMax2Sum)", []),
    "z_m": (r"$z$ [m]", [2.2, 2.4]),
    "radius_m": (r"$R=\sqrt{x^2+y^2}$ [m]", [1]),
    "Quadrant": ("Quadrant", []),
    "MaxQ_2inchPMT": ("MaxQ_2inchPMT [native units]", []),
}
style = ROOT / "publication.mplstyle"
settings = {
    "font.family": "DejaVu Sans", "font.size": 11, "axes.labelsize": 11,
    "axes.titlesize": 12, "xtick.labelsize": 10, "ytick.labelsize": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": "white", "savefig.facecolor": "white",
    "pdf.fonttype": 42, "savefig.bbox": None,
}
with plt.style.context(str(style)), plt.rc_context(settings):
    fig, axes = plt.subplots(2, 4, figsize=(17, 9.4))
    fig.subplots_adjust(left=.062, right=.985, top=.77, bottom=.12, wspace=.31, hspace=.44)
    for index, (axis, hist) in enumerate(zip(axes.flat, data["histograms"])):
        edges = np.asarray(hist["edges"])
        assert np.all(np.diff(edges) > 0)
        maximum = 1
        for group, _, color, line, width in groups:
            record = hist[group]
            counts = np.asarray(record["counts"])
            assert len(counts) + 1 == len(edges)
            assert (counts >= 0).all()
            assert int(counts.sum()) + record["underflow"] + record["overflow"] + record["nonfinite"] == totals[group]
            maximum = max(maximum, counts.max())
            axis.stairs(np.where(counts > 0, counts, np.nan), edges, baseline=None,
                        color=color, linestyle=line, linewidth=width)
        assert np.all(np.asarray(hist["residual_flag"]["counts"]) <= np.asarray(hist["preselected"]["counts"]))
        label, guides = labels[hist["variable"]]
        axis.set(xscale="linear", yscale="log", xlim=(edges[0], edges[-1]),
                 ylim=(.7, maximum * 8), xlabel=label, ylabel="Triggers / bin (log scale)")
        axis.set_title("({}) {}".format(chr(97+index), label.split(" [")[0]), loc="left", pad=9)
        axis.grid(axis="y", which="major", linewidth=.45, color="#dddddd")
        axis.tick_params(which="both", direction="out")
        for guide in guides:
            axis.axvline(guide, color="#555555", linestyle="--", linewidth=.8, zorder=0)
        details = []
        for group, short in (("all_ad", "raw"), ("preselected", "pre"), ("residual_flag", "flag")):
            record = hist[group]
            details.append("{} {:,} / {:,}".format(short, record["underflow"] + record["overflow"], record["nonfinite"]))
        axis.text(.98, .965, "Outside / nonfinite\n" + "\n".join(details),
                  transform=axis.transAxes, ha="right", va="top", fontsize=7.6,
                  color="#333333", bbox=dict(facecolor="white", edgecolor="none", pad=1.8))
        if hist["variable"] == "MaxQ_2inchPMT":
            axis.text(.98, .60, "Cut: < 100\n(outside displayed range)",
                      transform=axis.transAxes, ha="right", fontsize=8.5)
    fig.suptitle("Variables used by the residual-flasher cuts", x=.062, y=.975,
                 ha="left", fontsize=22, fontweight="bold")
    fig.text(.062, .925, "P21A / EH3  |  3 mini-files  |  Runs 81743, 82379, 82989  |  AD detectors 1–4", fontsize=12)
    handles = [Line2D([0], [0], color=color, linestyle=line, linewidth=width,
                      label="{}: {:,}".format(label, totals[group]))
               for group, label, color, line, width in groups]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(.057, .908),
               ncol=1, fontsize=11, frameon=False, labelspacing=.4)
    fig.text(.062, .056, "Preselection: AD energy > 0.7 MeV, standard ellipse < 1, MaxQ_2inchPMT < 100. No muon-veto or coincidence selection.", fontsize=10)
    fig.text(.062, .032, "Raw counts, no normalization or smoothing. Zero bins are omitted on log axes. Outside = underflow + overflow; native units are retained where unverified.", fontsize=9.2)
    fig.text(.062, .010, "Gray guides mark coordinates used in cuts, not independent acceptance boundaries. The residual flag is a cut condition, not a confirmed-flasher label.", fontsize=9.2)
    for extension in ("png", "pdf"):
        fig.savefig(ROOT / ("residual_flasher." + extension), dpi=200)
    plt.close(fig)

print(json.dumps(dict(totals=totals,
                      residual_fraction_of_preselected=totals["residual_flag"] / totals["preselected"],
                      matplotlib=matplotlib.__version__, numpy=np.__version__), sort_keys=True))
