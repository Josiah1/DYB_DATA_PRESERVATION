#!/usr/bin/env python3
"""Measure raw AD cuts with the production converter and the frozen 48-file sample."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shlex
import socket
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "estimates/noenergy-2026-09-27"))
from benchmark import command, emit, root_arrays, signatures, timed

CUTS = ("off", "0.3", "0.4", "0.5", "0.6", "0.7")
PLAN = REPO / "estimates/noenergy-2026-09-27/plan.json"


def build(work):
    work.mkdir(parents=True, exist_ok=True)
    source = (REPO / "src/slim_root.cpp").read_text()
    current = re.search(r"constexpr double kAdMinEnergyMeV = ([0-9.]+);", source).group(1)
    flags = shlex.split(command(["root-config", "--cflags", "--libs"]))
    builds = {}
    for cut in CUTS[1:]:
        # Disposable, explicitly labelled benchmark binaries; never deployed.
        text = source.replace(current, cut).replace(current.replace(".", "p"), cut.replace(".", "p"))
        cpp = work / ("cut-" + cut + ".cpp")
        cpp.write_text(text)
        binary = work / ("cut-" + cut)
        command(["g++", "-O3", "-DNDEBUG", "-std=c++17", cpp, "-o", binary, *flags])
        builds[cut] = hashlib.sha256(text.encode()).hexdigest()
    (work / "build.json").write_text(json.dumps(dict(
        source_sha256=hashlib.sha256(source.encode()).hexdigest(), builds=builds)))
    emit("build", **json.loads((work / "build.json").read_text()))


def run(work, index):
    import numpy as np
    import ROOT
    ROOT.PyConfig.DisableRootLogon = True
    ROOT.gROOT.SetBatch(True)
    plan = json.loads(PLAN.read_text())
    sample = plan["samples"][index]
    source = Path(sample["mini"])
    before = source.stat()
    arrays = root_arrays(source, plan["required"])
    entries = len(arrays["energy"])
    arrays["runNumber"] = np.full(entries, sample["run"], dtype="i4")
    ad = (arrays["detector"] >= 1) & (arrays["detector"] <= 4)
    energy = arrays["energy"].astype("f8")  # Match C++ Float_t vs double comparison.
    stamps = arrays["triggerTimeSec"].astype("i8") * 1000000000 + arrays["triggerTimeNanoSec"]
    emit("start", index=index, host=socket.gethostname(), root=ROOT.gROOT.GetVersion(),
         **sample, **json.loads((work / "build.json").read_text()))
    with tempfile.TemporaryDirectory(dir=work) as temporary:
        tmp = Path(temporary)
        listing = tmp / "input.list"
        listing.write_text(str(source) + "\n")
        for cut in CUTS:
            upper = 0.7 if cut == "off" else float(cut)
            mask = np.ones(entries, dtype=bool) if cut == "off" else (~ad | (energy > upper))
            output, report = tmp / "output.root", tmp / "source.tsv"
            binary = work / ("cut-0.7" if cut == "off" else "cut-" + cut)
            stdout, timing = timed(lambda: command([
                binary, "--input-list", listing, "--output", output, "--source-report", report,
                "--run-number", sample["run"], "--ad-selection", "off" if cut == "off" else "on"
            ]), child=True)
            expected = signatures({name: values[mask] for name, values in arrays.items()})
            assert signatures(root_arrays(output, plan["required"], sample["run"])) == expected
            row = next(csv.DictReader(report.read_text().splitlines(), delimiter="\t"))
            assert int(row["entries"]) == entries and int(row["keptEntries"]) == int(mask.sum())
            assert float(row["dataDurationSeconds"]) == int(stamps.max() - stamps.min()) / 1e9
            assert int(row["firstTriggerTimeSec"]) * 1000000000 + int(row["firstTriggerTimeNanoSec"]) == int(stamps.min())
            assert int(row["lastTriggerTimeSec"]) * 1000000000 + int(row["lastTriggerTimeNanoSec"]) == int(stamps.max())
            for detector in range(1, 5):
                dropped = (arrays["detector"] == detector) & ~(energy > upper)
                assert int(row[f"ad{detector}Below0p3MeV"]) == int((dropped & ~(energy > 0.3)).sum())
                band = "0p7" if cut == "off" else cut.replace(".", "p")
                assert int(row[f"ad{detector}From0p3To{band}MeV"]) == int((dropped & (energy > 0.3)).sum())
            emit("measurement", index=index, group=sample["group"], cut=cut, bytes=output.stat().st_size,
                 entries=entries, kept=int(mask.sum()), non_ad=int((~ad).sum()), passed=True,
                 source_report=row, branch_signatures=expected, converter_output=stdout, **timing)
            output.unlink()
            report.unlink()
    after = source.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    emit("complete", index=index, variants=len(CUTS), passed=True)


def project(groups, measurements):
    result = {}
    for cut in CUTS:
        selected = {g: [r for r in measurements if r["cut"] == cut and r["group"] == g] for g in groups}
        assert all(selected.values()), "missing threshold/stratum"
        result[cut] = dict(
            bytes=sum(n * sum(r["bytes"] for r in selected[g]) / len(selected[g]) for g, n in groups.items()),
            low_bytes=sum(n * min(r["bytes"] for r in selected[g]) for g, n in groups.items()),
            high_bytes=sum(n * max(r["bytes"] for r in selected[g]) for g, n in groups.items()),
            cpu_days=sum(n * sum(r["cpu_seconds"] for r in selected[g]) / len(selected[g]) for g, n in groups.items()) / 86400,
            kept=sum(n * sum(r["kept"] for r in selected[g]) / len(selected[g]) for g, n in groups.items()))
    return result


def choose(projections, available):
    # Fixed 10% space reserve; observed within-stratum maxima must also fit.
    return next((cut for cut in CUTS if projections[cut]["high_bytes"] <= available * 0.90), None)


def summarize(results, available):
    plan = json.loads(PLAN.read_text())
    measurements = []
    hashes = set()
    for index, sample in enumerate(plan["samples"]):
        rows = [json.loads(s) for s in (results / f"{index:03d}.jsonl").read_text().splitlines()]
        assert rows[0]["kind"] == "start" and rows[0]["index"] == index
        assert all(rows[0][key] == value for key, value in sample.items())
        hashes.add(rows[0]["source_sha256"])
        assert rows[-1] == dict(kind="complete", index=index, variants=len(CUTS), passed=True)
        data = rows[1:-1]
        assert len(data) == len(CUTS) and {r["cut"] for r in data} == set(CUTS)
        assert all(r["passed"] and r["index"] == index and r["group"] == sample["group"] for r in data)
        assert len({r["entries"] for r in data}) == len({r["non_ad"] for r in data}) == 1
        measurements.extend(data)
    assert len(hashes) == 1
    projections = project(plan["groups"], measurements)
    emit("estimate", files=plan["expected_files"], samples=len(plan["samples"]),
         source_sha256=hashes.pop(), available_bytes=available, budget_bytes=available * 0.9,
         selected_cut=choose(projections, available), projections=projections,
         caveat="Deterministic sample; extrema are a sensitivity envelope, not a confidence interval. Raw AD energy selection; no TWin corrected-energy equivalence claim.")


def check():
    rows = [dict(group=g, cut=cut, bytes=b, cpu_seconds=1, kept=2)
            for cut in CUTS for g, b in (("a", 10), ("a", 20), ("b", 100))]
    result = project({"a": 100, "b": 1}, rows)
    assert result["0.7"]["bytes"] == 1600 and result["0.7"]["high_bytes"] == 2100
    assert choose(result, 2400) == "off" and choose(result, 2000) is None
    result["0.5"]["high_bytes"] = 1000
    assert choose(result, 2000) == "0.5"
    try:
        project({"missing": 1}, rows)
    except AssertionError:
        pass
    else:
        raise AssertionError("missing strata accepted")
    print("threshold projection and selection checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "run", "summarize", "check"))
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--index", type=int)
    parser.add_argument("--available", type=int)
    args = parser.parse_args()
    if args.mode == "build":
        build(args.path)
    elif args.mode == "run":
        run(args.path, args.index)
    elif args.mode == "summarize":
        summarize(args.path, args.available)
    else:
        check()
