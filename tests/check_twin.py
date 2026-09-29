#!/usr/bin/env python3
"""Bounded real-data regression against the pinned, unmodified GitHub TWin.

Run inside the temporary payload prepared by check_twin.sh. No source-data
writes, job submissions, installation, or production staging are performed.
"""
import csv
import datetime
import hashlib
import json
from pathlib import Path
import re
import shlex
import socket
import struct
import subprocess
import sys

import numpy as np
import ROOT

ROOT.PyConfig.DisableRootLogon = True
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "estimates/noenergy-2026-09-27"))
from benchmark import command, root_arrays, signatures

TWIN_REVISION = "d6b1b5ec89e158a353a77c528d2f8511dced76a8"
TWIN = PROJECT / "TWin/Retw_lijj/src_FD"

READOUT_CHECK = r'''
#include "InputReader.h"
#include <cstring>
#include <iostream>
int main(int argc, char** argv) {
  if (argc != 4) return 2;
  InputReader raw({argv[1], argv[2]}), slim({argv[3]});
  Readout a, b;
  long long index = 0;
#define CHECK(field) if (std::memcmp(&a.field, &b.field, sizeof(a.field))) { \
  std::cerr << "entry " << index << " field " #field << ": " \
            << a.field << " != " << b.field << std::endl; return 1; }
  while (raw.Next(a)) {
    if (!slim.Next(b)) return 3;
    CHECK(Run); CHECK(Site); CHECK(Det); CHECK(TrigNum); CHECK(TrigType);
    CHECK(TrigSec); CHECK(TrigNano); CHECK(NPmt); CHECK(E); CHECK(X); CHECK(Y); CHECK(Z);
    CHECK(Quadrant); CHECK(Q1); CHECK(Q2); CHECK(QMax2Sum); CHECK(MaxQ2Inch);
    CHECK(Kurtosis); CHECK(time_PSD_local_RMS);
    ++index;
  }
  if (slim.Next(b)) return 4;
  std::cout << "MATCH readouts=" << index << std::endl;
}
'''


def event_signature(path):
    with ROOT.TFile.Open(str(path), "READ") as file:
        tree = file.Get("Event")
        assert tree and tree.GetEntries() > 0
        values = ROOT.RDataFrame(tree).AsNumpy()
        result = {}
        for name, column in values.items():
            digest = hashlib.sha256()
            if column.dtype.kind == "O":
                for row in column:
                    row = np.asarray(row)
                    digest.update(row.dtype.str.encode())
                    digest.update(struct.pack("<Q", len(row)))
                    digest.update(row.tobytes())
            else:
                digest.update(column.tobytes())
            result[name] = dict(type=tree.GetLeaf(name).GetTypeName(),
                                sha256=digest.hexdigest())
        return dict(entries=int(tree.GetEntries()), branches=result)


def main():
    ROOT.gROOT.SetBatch(True)
    flags = shlex.split(command(["root-config", "--cflags", "--libs"]))
    converter = PROJECT / "slim-root"
    subprocess.run(["g++", "-O3", "-DNDEBUG", str(PROJECT / "src/slim_root.cpp"),
                    "-o", str(converter), *flags], check=True)
    # The upstream Makefile uses gcc and relies on libdeflate.pc, absent here.
    # Compile the unmodified sources with the C++ driver and installed library.
    sources = ("Retw.C", "InputReader.C", "Fixer.C", "CachedQueueInsert.C",
               "BuildEvent.C", "EventTree.C", "RunInfo.C", "TimeStamp.C")
    twin_command = ["g++", "-O2", "-I" + str(PROJECT / "TWin"),
                    "-I/cvmfs/sft.cern.ch/lcg/views/LCG_110/x86_64-el9-gcc13-opt/include",
                    *map(str, (TWIN / s for s in sources)), *flags, "-ldeflate"]
    subprocess.run([*twin_command, "-o", str(TWIN / "Retw")], check=True, stdout=sys.stderr)
    required = json.loads((PROJECT / "estimates/noenergy-2026-09-27/plan.json").read_text())["required"]
    catalog = PROJECT / "catalog_backups/P17B/EH1/run48734.list"
    # Exactly two adjacent, fixed-catalog files; never expand to the full run.
    original = catalog.read_text().splitlines()[:2]
    paths = [Path(p.replace("/dybfs/rec/P17B/rec/", "/lustrefs/juno26/dyw/rec/All-mini/")
                  .replace("/recon.", "/minirecon.")) for p in original]
    before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    listing = PROJECT / "original.list"
    listing.write_text("\n".join(map(str, paths)) + "\n")
    output = PROJECT / "preserved.0048734.slim.000000.root"
    report = PROJECT / "sources.tsv"
    conversion = command([converter, "--input-list", listing, "--output", output,
                          "--source-report", report, "--run-number", "48734"])
    assert "selection=off" in conversion
    arrays = [root_arrays(p, required) for p in paths]
    expected = {name: np.concatenate([a[name] for a in arrays]) for name in arrays[0]}
    total = len(expected["energy"])
    expected["runNumber"] = np.full(total, 48734, dtype="i4")
    reference = signatures(expected)
    assert signatures(root_arrays(output, required, 48734)) == reference
    rows = list(csv.DictReader(report.read_text().splitlines(), delimiter="\t"))
    assert len(rows) == 2
    with ROOT.TFile.Open(str(output), "READ") as file:
        assert file.GetCompressionSettings() == 509
        for path in required:
            tree = file.Get(path)
            assert tree.GetAutoFlush() == tree.GetAutoSave() == 0
            for branch in tree.GetListOfBranches():
                assert branch.GetBasketSize() == 262144
                assert branch.GetCompressionSettings() == 509
                assert branch.GetEntryOffsetLen() == 0
        sources = file.Get("Preservation/Sources")
        assert sources.GetEntries() == 2
        for index, (row, values) in enumerate(zip(rows, arrays)):
            assert row["path"] == str(paths[index])
            assert int(row["entries"]) == int(row["keptEntries"]) == len(values["energy"])
            stamps = values["triggerTimeSec"].astype("i8") * 1000000000 + values["triggerTimeNanoSec"]
            assert float(row["dataDurationSeconds"]) == int(stamps.max() - stamps.min()) / 1e9
            assert sources.GetEntry(index) > 0
            for field in ("dataDurationSeconds", "processingWallSeconds", "processingCpuSeconds"):
                assert sources.GetLeaf(field).GetTypeName() == "Double_t"
                assert getattr(sources, field) == float(row[field])
    check_source = PROJECT / "check_readouts.cpp"
    check_source.write_text(READOUT_CHECK)
    check = PROJECT / "check_readouts"
    subprocess.run(["g++", "-O2", "-I" + str(TWIN), str(check_source),
                    str(TWIN / "InputReader.C"), "-o", str(check), *flags, "-ldeflate"], check=True)
    readouts = command([check, *paths, output])
    assert "MATCH readouts={}".format(total) in readouts
    print("PASS TWin all input readouts", file=sys.stderr, flush=True)

    def run_twin(tag, input_path, files, options, executable):
        dest = PROJECT / (tag + ".root")
        info = dest.with_suffix(".txt")
        process = subprocess.run([str(executable), str(input_path), str(dest), str(info),
                                  *options], cwd=TWIN, text=True, timeout=180,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
        assert re.search(r"Total events = {} in {} files".format(total, files), process.stdout)
        return event_signature(dest), info.read_bytes()

    native = [run_twin("native-" + tag, path, files, ["H"], TWIN / "Retw")
              for tag, path, files in (("original", listing, 2), ("preserved", output, 1),
                                       ("repeat", listing, 2))]
    assert native[0][1] == native[1][1] == native[2][1]
    assert native[0][0]["entries"] == native[1][0]["entries"] == native[2][0]["entries"]
    changed = [[k for k, v in native[0][0]["branches"].items()
                if v != item[0]["branches"][k]] for item in native[1:]]
    assert changed == [["T2PrevIwsMu"], ["T2PrevIwsMu"]], changed
    native_control = dict(changed_vs_preserved=changed[0], changed_on_identical_input=changed[1],
        entries=native[0][0]["entries"], run_info_equal=True,
        time_branch_hashes=[item[0]["branches"]["T2PrevIwsMu"] for item in native])

    # Only TWin's directly linked gettimeofday calls are wrapped, not ROOT's.
    # Its TimeStamp default initializes previous pool/IWS muons from job time.
    clock_source = PROJECT / "fixed_clock.cpp"
    clock_source.write_text('#include <sys/time.h>\nextern "C" int __wrap_gettimeofday('
                            'timeval* t, void*) { t->tv_sec=1800000000; t->tv_usec=0; return 0; }\n')
    fixed = PROJECT / "Retw-fixed-clock"
    subprocess.run([*twin_command, str(clock_source), "-Wl,--wrap=gettimeofday", "-o", str(fixed)],
                   check=True, stdout=sys.stderr)
    results = []
    # Full Retw, including corrections, muons, residual flashers and coincidences.
    for label, options in (("H", ["H"]), ("Gd", ["Gd"]),
                           ("H-fastn", ["H", "--fastn"]), ("H-acpo", ["H", "--acpo"])):
        pair = [run_twin(label + "-" + kind, path, files, options, fixed)
                for kind, path, files in (("original", listing, 2), ("preserved", output, 1))]
        assert pair[0] == pair[1], label + " physics output or RunInfo differs with fixed clock"
        results.append(dict(mode=label, **pair[0][0],
                            run_info_sha256=hashlib.sha256(pair[0][1]).hexdigest()))
        print("PASS TWin " + label, file=sys.stderr, flush=True)
    assert before == [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    payload = [p for p in PROJECT.rglob("*") if p.is_file() and
               (p.suffix in (".C", ".cpp", ".h", ".py") or p.name == "CaliParameters_withoutaverage.txt")]
    print(json.dumps(dict(passed=True, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        host=socket.gethostname(), root=ROOT.gROOT.GetVersion(), twin_revision=TWIN_REVISION,
        source_files=list(map(str, paths)), entries=total, compression=509, basket_bytes=262144,
        ad_selection="off", output_bytes=output.stat().st_size, converter_output=conversion,
        source_report=report.read_text(), retained_branches=reference, twin_readouts=readouts,
        native_clock_control=native_control, initialization_clock_seconds=1800000000,
        twin_results=results,
        source_sha256={str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(payload)}), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
