#!/usr/bin/env python3
"""Campaign disk estimate for AD-energy selections from the cluster sample."""
import csv, json, re, sys
from collections import defaultdict
from pathlib import Path

sp = Path(sys.argv[1]); res = sp / "results"
BASELINE_TB = 20.0
combo_files = json.load(open(sp / "combo_counts.json"))
sample = {}
for line in (sp / "sample48.tsv").read_text().splitlines():
    period, hall, run, path = line.split("\t"); sample[path] = f"{period}/{hall}"
conv = {}
for line in (sp / "convert_final.tsv").read_text().splitlines():
    tag, period, hall, run, path = line.split("\t"); conv[tag] = f"{period}/{hall}"

# entry counts per file/detector: [entries, gt03, gt07, hist(100 x 0.1 MeV), overflow, nonpositive]
per_file = defaultdict(dict)
for row in csv.reader(open(res / "counts.tsv"), delimiter="\t"):
    if row[0] == "path": continue
    per_file[row[0]][int(row[1])] = [int(row[2]), int(row[3]), int(row[4]), list(map(int, row[6:106])), int(row[106]), int(row[5])]
AD = lambda d: 1 <= d <= 4

combo = {}
for c in combo_files:
    files = [p for p, cc in sample.items() if cc == c and p in per_file]
    t = sum(v[0] for p in files for v in per_file[p].values())
    ad = sum(v[0] for p in files for d, v in per_file[p].items() if AD(d))
    hist = [0] * 101
    for p in files:
        for d, v in per_file[p].items():
            if AD(d):
                for b in range(100): hist[b] += v[3][b]
                hist[100] += v[4]
    combo[c] = {"files": len(files), "entries_per_file": t / len(files), "f_ad": ad / t, "f_nonad": 1 - ad / t,
                "f_ad_above": lambda thr, hist=hist, t=t: sum(hist[int(round(thr / 0.1)):]) / t,
                "per_file_f03": [sum(v[1] for d, v in per_file[p].items() if AD(d)) / sum(v[0] for v in per_file[p].values()) for p in files]}

# byte measurements: per converted chunk, per variant (kept, bytes)
variants = ["all", "ad03", "ad07", "ad03wp", "ad07wp"]
meas = {}
for tag, c in conv.items():
    m = {}
    for row in csv.reader(open(res / "filt" / f"{tag}.tsv"), delimiter="\t"):
        if row[0] != "tag": m[row[1]] = (int(row[3]), int(row[4]))
    r = re.search(r"entries=(\d+) input_bytes=(\d+) output_bytes=(\d+)", (res / "slim" / f"{tag}.result").read_text())
    m["converter"] = (int(r.group(1)), int(r.group(3))); meas[tag] = m
    n = m["all"][0]
    # two-component compression model: bytes = a * kept_AD + b * kept_nonAD
    a = (m["ad03"][1] + m["ad07"][1]) / (m["ad03"][0] + m["ad07"][0])
    b = (m["ad03wp"][1] - m["ad03"][1]) / (m["ad03wp"][0] - m["ad03"][0])
    n_ad = m["ad03wp"][0] - (m["ad03wp"][0] - m["ad03"][0])  # kept AD>0.3 (same as ad03 kept)
    n_nonad = m["ad03wp"][0] - m["ad03"][0]
    n_ad_all = n - n_nonad
    pred_all = a * n_ad_all + b * n_nonad
    combo[c].update({"tag": tag, "a_ad": a, "b_nonad": b, "n": n, "model_all_ratio": pred_all / m["all"][1],
                     "bytes": {v: m[v][1] for v in variants}, "kept": {v: m[v][0] for v in variants}})

print("== Compression model per converted chunk (bytes = a*AD_kept + b*nonAD_kept) ==")
print(f"{'combo':9} {'tag':7} {'entries':>7} {'all B/ev':>8} {'a_AD':>6} {'b_nonAD':>8} {'model/all':>9}")
for c, k in combo.items():
    print(f"{c:9} {k['tag']:7} {k['n']:7d} {k['bytes']['all']/k['n']:8.2f} {k['a_ad']:6.2f} {k['b_nonad']:8.2f} {k['model_all_ratio']:9.3f}")

print("\n== Entry fractions (48-file count; per-file AD>0.3 fraction spread) ==")
for c, k in combo.items():
    print(f"{c:9} files={k['files']} AD={k['f_ad']:.1%} AD>0.3={k['f_ad_above'](0.3):.1%} AD>0.7={k['f_ad_above'](0.7):.1%} "
          f"nonAD={k['f_nonad']:.1%}  per-file AD>0.3: {', '.join(f'{x:.1%}' for x in k['per_file_f03'])}")

def campaign(thr, keep_wp, method):
    total = 0.0
    for c, k in combo.items():
        N = combo_files[c]["files"]
        if method == "A":  # direct: converted chunk bytes x file count
            key = {(0.3, False): "ad03", (0.7, False): "ad07", (0.3, True): "ad03wp", (0.7, True): "ad07wp", (None, True): "all"}[(thr, keep_wp)]
            total += N * k["bytes"][key]
        else:              # C: per-combo fractions x per-combo compression coefficients
            f_ad = k["f_ad"] if thr is None else k["f_ad_above"](thr)
            total += N * k["entries_per_file"] * (f_ad * k["a_ad"] + (k["f_nonad"] * k["b_nonad"] if keep_wp else 0))
    return total

allA, allC = campaign(None, True, "A"), campaign(None, True, "C")
print(f"\n== Campaign totals, 831,334 files (A = direct chunk bytes, C = fractions x coefficients) ==")
print(f"all events: A {allA/1e12:.2f} TB, C {allC/1e12:.2f} TB  (documented baseline 19-21 TB, user baseline 20 TB)")
print(f"{'selection':28} {'A TB':>6} {'C TB':>6} {'A/all':>6} {'C/all':>6} {'A->20TB':>8} {'C->20TB':>8}")
rows = []
for thr, keep_wp, label in [(0.3, False, "AD only, E>0.3"), (0.7, False, "AD only, E>0.7"),
                            (0.3, True, "AD E>0.3 + all WP/RPC"), (0.7, True, "AD E>0.7 + all WP/RPC")]:
    A, C = campaign(thr, keep_wp, "A"), campaign(thr, keep_wp, "C")
    print(f"{label:28} {A/1e12:6.2f} {C/1e12:6.2f} {A/allA:6.1%} {C/allC:6.1%} {BASELINE_TB*A/allA:8.2f} {BASELINE_TB*C/allC:8.2f}")

print("\n== Threshold curve (method C, scaled to the 20 TB baseline) ==")
for thr in (0.0, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0):
    ad_only, with_wp = campaign(thr, False, "C"), campaign(thr, True, "C")
    print(f"E > {thr:3.1f} MeV: AD only {BASELINE_TB*ad_only/allC:5.2f} TB   AD + WP/RPC {BASELINE_TB*with_wp/allC:5.2f} TB")

# sanity: pooled AD energy spectrum, 0-3 MeV in 0.1 MeV bins, as % of AD entries
tot_ad = sum(v[0] for p in per_file for d, v in per_file[p].items() if AD(d))
nonpos = sum(v[5] for p in per_file for d, v in per_file[p].items() if AD(d))
hist = [sum(v[3][b] for p in per_file for d, v in per_file[p].items() if AD(d)) for b in range(100)]
print(f"\nAD spectrum sanity (% of AD entries per 0.1 MeV bin, 0-3 MeV); E<=0: {nonpos/tot_ad:.2%}; >10 MeV: "
      f"{sum(v[4] for p in per_file for d, v in per_file[p].items() if AD(d))/tot_ad:.2%}")
print(" ".join(f"{100*h/tot_ad:.1f}" for h in hist[:30]))
