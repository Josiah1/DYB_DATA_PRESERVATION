"""End-to-end checks using small synthetic ROOT files."""
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parent.parent
CONVERTER = PROJECT / "bin" / "slim-root"
AD_MIN, BAND_LOW = 0.6, 0.3
MACRO = r'''
#include <TDirectory.h>
#include <TFile.h>
#include <TLeaf.h>
#include <TTree.h>
#include <cassert>
#include <fstream>
#include <string>
void make_source(const char* table, const char* output) {
  TFile file(output, "RECREATE");
  TDirectory* event = file.mkdir("Event");
  TDirectory* rec = event->mkdir("Rec");
  TDirectory* data = event->mkdir("Data");
  Short_t site = 1, detector = 0;
  UInt_t triggerType = 0x10001002, triggerNumber = 0;
  Int_t sec = 0, nano = 0, nHit = 0;
  Float_t energy = 0, x = 0, y = 0, z = 0, unused = 0, stat = 0;
  rec->cd();
  TTree simple("AdSimpleNL", "synthetic");
  simple.Branch("site", &site, "site/S");
  simple.Branch("detector", &detector, "detector/S");
  simple.Branch("triggerType", &triggerType, "triggerType/i");
  simple.Branch("triggerNumber", &triggerNumber, "triggerNumber/i");
  simple.Branch("triggerTimeSec", &sec, "triggerTimeSec/I");
  simple.Branch("triggerTimeNanoSec", &nano, "triggerTimeNanoSec/I");
  simple.Branch("energy", &energy, "energy/F");
  simple.Branch("x", &x, "x/F");
  simple.Branch("y", &y, "y/F");
  simple.Branch("z", &z, "z/F");
  simple.Branch("unusedBranch", &unused, "unusedBranch/F");
  data->cd();
  TTree stats("CalibStats", "synthetic");
  stats.Branch("nHit", &nHit, "nHit/I");
  for (const char* name : {"MiddleTimeRMS", "Quadrant", "Q1", "Q2", "MaxQ", "MaxQ_2inchPMT",
                           "Kurtosis", "time_PSD_local_RMS"})
    stats.Branch(name, &stat, (std::string(name) + "/F").c_str());
  std::ifstream in(table);
  int det; std::string e; long s, n;
  while (in >> det >> e >> s >> n) {
    detector = det; energy = std::stof(e); sec = s; nano = n; ++triggerNumber;
    x = triggerNumber; y = -x; z = 0.5f * x; nHit = 10 + det; stat = 0.25f * det;
    simple.Fill(); stats.Fill();
  }
  rec->cd(); simple.Write();
  data->cd(); stats.Write();
  file.Close();
}
void check_format(const char* path) {
  TFile file(path);
  assert(!file.IsZombie() && file.GetCompressionSettings() == 509);
  for (const char* name : {"Event/Rec/AdSimpleNL", "Event/Data/CalibStats"}) {
    auto* tree = file.Get<TTree>(name);
    assert(tree && tree->GetAutoFlush() == 0 && tree->GetAutoSave() == 0);
    for (auto* object : *tree->GetListOfBranches()) {
      auto* branch = static_cast<TBranch*>(object);
      assert(branch->GetBasketSize() == 262144);
      assert(branch->GetCompressionSettings() == 509);
      assert(branch->GetEntryOffsetLen() == 0);
      assert(branch->GetBasketEntry()[0] == 0);
      assert(branch->GetBasketEntry()[branch->GetWriteBasket()] == tree->GetEntries());
    }
  }
  auto* sources = file.Get<TTree>("Preservation/Sources");
  for (const char* name : {"dataDurationSeconds", "processingWallSeconds", "processingCpuSeconds"})
    assert(std::string(sources->GetLeaf(name)->GetTypeName()) == "Double_t");
}
'''


def find_root():
    candidates = [shutil.which("root")]
    for base in (os.environ.get("ROOTSYS"),):
        if base:
            candidates.append(str(Path(base) / "bin" / "root"))
    return next((c for c in candidates if c and os.access(c, os.X_OK)), None)


def parse_result_line(text):
    for line in text.splitlines():
        if line.startswith("RESULT "):
            return dict(token.split("=", 1) for token in line.split()[1:] if "=" in token)
    raise AssertionError("no RESULT line in converter output")


def as_float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


def synthetic_table(seed, count):
    rng = random.Random(seed)
    specials = [0.3, 0.6, 0.7, 0.30000001, 0.59999995, 0.60000005, 0.0, -0.5, float("nan"), 15.0]
    rows = []
    base = 1500000000 + 1000 * seed
    for _ in range(count):
        detector = rng.choice([1, 1, 2, 2, 3, 4, 5, 5, 6, 6, 7])
        if detector <= 4:
            energy = rng.choice(specials) if rng.random() < 0.2 else rng.uniform(0, 3)
        else:
            energy = rng.uniform(0, 5)
        rows.append((detector, energy, base + rng.randint(0, 600), rng.randint(0, 999999999)))
    return rows


def expectation(rows):
    kept = 0
    below = [0] * 4
    band = [0] * 4
    for detector, energy, _, _ in rows:
        value = as_float32(energy)
        if 1 <= detector <= 4 and not value > AD_MIN:
            (band if value > BAND_LOW else below)[detector - 1] += 1
        else:
            kept += 1
    stamps = [sec * 1000000000 + nano for _, _, sec, nano in rows]
    first, last = min(stamps), max(stamps)
    return {
        "entries": len(rows), "kept": kept,
        "counts": [c for pair in zip(below, band) for c in pair],
        "first": divmod(first, 1000000000), "last": divmod(last, 1000000000),
        "duration": (last - first) / 1e9,
    }


@unittest.skipUnless(find_root() and os.access(str(CONVERTER), os.X_OK),
                     "needs a ROOT installation and bin/slim-root")
class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        macro = self.root / "make_source.C"
        macro.write_text(MACRO)
        self.sources = []
        self.expected = []
        for seed, count in ((1, 400), (2, 250)):
            rows = synthetic_table(seed, count)
            table = self.root / "table{}.txt".format(seed)
            table.write_text("".join("{} {} {} {}\n".format(d, repr(e), s, n) for d, e, s, n in rows))
            output = self.root / "source{}.root".format(seed)
            subprocess.run([find_root(), "-n", "-l", "-b", "-q", '{}("{}","{}")'.format(macro, table, output)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertTrue(output.stat().st_size > 0)
            self.sources.append(str(output))
            self.expected.append(expectation(rows))

    def tearDown(self):
        self.temporary.cleanup()

    def convert(self, mode, tag):
        listing = self.root / "{}.list".format(tag)
        listing.write_text("\n".join(self.sources) + "\n")
        output = self.root / "{}.root".format(tag)
        report = self.root / "{}.tsv".format(tag)
        result = subprocess.run(
            [str(CONVERTER), "--input-list", str(listing), "--output", str(output),
             "--source-report", str(report), "--run-number", "7777"] +
            (["--ad-selection", mode] if mode == "on" else []),
            check=True, stdout=subprocess.PIPE, universal_newlines=True)
        return output, report, parse_result_line(result.stdout)

    def check_report(self, report, mode):
        lines = report.read_text().splitlines()
        self.assertEqual(lines[0].split("\t")[:4], ["sourceIndex", "path", "entries", "inputBytes"])
        total_kept = 0
        for index, (line, expected) in enumerate(zip(lines[1:], self.expected)):
            fields = line.split("\t")
            self.assertEqual(fields[:2], [str(index), self.sources[index]])
            entries, kept = int(fields[2]), int(fields[11])
            self.assertEqual(entries, expected["entries"])
            self.assertEqual(kept, expected["kept"] if mode == "on" else expected["entries"])
            self.assertEqual([int(f) for f in fields[12:20]], expected["counts"])
            self.assertEqual(tuple(int(f) for f in fields[4:6]), expected["first"])
            self.assertEqual(tuple(int(f) for f in fields[6:8]), expected["last"])
            self.assertEqual(float(fields[8]), expected["duration"])
            total_kept += kept
        self.assertEqual(len(lines), 1 + len(self.expected))
        return total_kept

    def test_selection_on_and_off_match_the_synthetic_truth(self):
        self.assertTrue(any(e["counts"][1] for e in self.expected), "band must be populated")
        self.assertTrue(any(e["counts"][0] for e in self.expected), "below band must be populated")
        for mode in ("on", "off"):
            output, report, metrics = self.convert(mode, "chunk-" + mode)
            subprocess.run([find_root(), "-n", "-l", "-b", "-q", "-e",
                            '#include "{}"'.format(self.root / "make_source.C"),
                            "-e", 'check_format("{}");'.format(output)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            kept = self.check_report(report, mode)
            self.assertEqual(metrics["selection"], mode)
            self.assertEqual(int(metrics["kept_entries"]), kept)
            self.assertEqual(int(metrics["entries"]), sum(e["entries"] for e in self.expected))
            verify = subprocess.run([str(CONVERTER), "--verify", str(output)], check=True,
                                    stdout=subprocess.PIPE, universal_newlines=True)
            self.assertIn("VERIFY entries={}".format(kept), verify.stdout)
            self.assertIn("source_entries={}".format(metrics["entries"]), verify.stdout)
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run([str(CONVERTER), "--verify", self.sources[0]], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def root_call(self, expression):
        subprocess.run([find_root(), "-n", "-l", "-b", "-q", "-e",
                        '#include "{}"'.format(PROJECT / "tests/muon_fixture.C"),
                        "-e", expression], check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)

    def run_muon(self, tag="muon", extra=()):
        listing = self.root / "muon.list"
        listing.write_text("\n".join(self.sources) + "\n")
        output, report = self.root / (tag + ".root"), self.root / (tag + ".tsv")
        result = subprocess.run(
            [str(CONVERTER), "--input-list", str(listing), "--output", str(output),
             "--source-report", str(report), "--run-number", "7777", "--profile", "muon",
             *extra], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return result, output, report

    def test_muon_values_and_independent_stream_order(self):
        for source in self.sources:
            self.root_call('add_muon("{}");'.format(source))
        result, output, report = self.run_muon()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("profile=muon", result.stdout)
        self.check_report(report, "off")
        self.root_call('compare_muon("{}", "{}", "{}");'.format(output, *self.sources))
        subprocess.run([str(CONVERTER), "--verify", str(output)], check=True,
                       stdout=subprocess.PIPE)
        before = output.read_bytes(), report.read_bytes()
        failed, _, _ = self.run_muon()
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(before, (output.read_bytes(), report.read_bytes()))
        self.root_call('corrupt_muon_provenance("{}");'.format(output))
        self.assertNotEqual(subprocess.run([str(CONVERTER), "--verify", str(output)],
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE).returncode, 0)

    def test_incomplete_muon_input_fails_without_publishing(self):
        self.root_call('add_muon("{}");'.format(self.sources[0]))
        for fault in ("missing", "count", "identity", "type"):
            with self.subTest(fault=fault):
                self.root_call('add_muon("{}", "{}");'.format(self.sources[1], fault))
                result, output, report = self.run_muon(fault)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(output.exists())
                self.assertFalse(report.exists())
                self.assertFalse(list(self.root.glob("*.partial.*")))

    def test_missing_muon_trees_and_selection_conflict(self):
        for args in ((), ("--ad-selection", "on"), ("--run-number", "123junk")):
            result, output, report = self.run_muon(extra=args)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertFalse(output.exists())
            self.assertFalse(report.exists())

    def test_empty_muon_stream_is_valid(self):
        for source in self.sources:
            self.root_call('add_muon("{}", "empty");'.format(source))
        result, output, report = self.run_muon()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.root_call('compare_muon("{}", "{}", "{}");'.format(output, *self.sources))

    def test_all_entries_cut_produces_a_verifiable_empty_file(self):
        rows = [(1, 0.1, 1500000000, 42)]
        table = self.root / "low.txt"
        table.write_text("1 0.1 1500000000 42\n")
        source = self.root / "low.root"
        subprocess.run([find_root(), "-n", "-l", "-b", "-q",
                        '{}("{}","{}")'.format(self.root / "make_source.C", table, source)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.sources, self.expected = [str(source)], [expectation(rows)]
        output, report, metrics = self.convert("on", "empty")
        self.assertEqual(metrics["kept_entries"], "0")
        self.assertEqual(self.check_report(report, "on"), 0)
        subprocess.run([str(CONVERTER), "--verify", str(output)], check=True,
                       stdout=subprocess.PIPE)
