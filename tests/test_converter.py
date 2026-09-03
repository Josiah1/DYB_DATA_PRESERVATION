"""End-to-end converter and completeness check on synthetic sources.

Skipped unless a ROOT installation and the compiled bin/slim-root are available
(`make converter`); the logic tests in test_preserve.py do not need ROOT.
"""
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

import preserve

PROJECT = Path(__file__).resolve().parent.parent
CONVERTER = PROJECT / "bin" / "slim-root"
AD_MIN, BAND_LOW = 0.7, 0.3
MACRO = r'''
#include <TDirectory.h>
#include <TFile.h>
#include <TTree.h>
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
'''


def find_root():
    candidates = [shutil.which("root")]
    for base in (os.environ.get("ROOTSYS"), str(Path.home() / "TOOLs/install/root-current")):
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
    specials = [0.3, 0.7, 0.30000001, 0.70000005, 0.0, -0.5, float("nan"), 15.0]
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
        self.env = dict(os.environ, LCG_VIEW=str(self.root / "lcg"))
        (self.root / "lcg").mkdir()
        (self.root / "lcg" / "setup.sh").write_text("# empty stand-in for the LCG view\n")
        macro = self.root / "make_source.C"
        macro.write_text(MACRO)
        self.sources = []
        self.expected = []
        for seed, count in ((1, 400), (2, 250)):
            rows = synthetic_table(seed, count)
            table = self.root / "table{}.txt".format(seed)
            table.write_text("".join("{} {} {} {}\n".format(d, repr(e), s, n) for d, e, s, n in rows))
            output = self.root / "source{}.root".format(seed)
            subprocess.run([find_root(), "-l", "-b", "-q", '{}("{}","{}")'.format(macro, table, output)],
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
             "--source-report", str(report), "--run-number", "7777", "--ad-selection", mode],
            check=True, stdout=subprocess.PIPE, universal_newlines=True)
        return output, report, parse_result_line(result.stdout)

    def check_report(self, report, mode):
        lines = report.read_text().splitlines()
        self.assertEqual(tuple(lines[0].split("\t")), preserve.SOURCE_REPORT_COLUMNS)
        total_kept = 0
        for index, (line, expected) in enumerate(zip(lines[1:], self.expected)):
            fields = line.split("\t")
            entries, _, kept = preserve.validate_source_fields(fields, self.sources[index], index, mode)
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
            kept = self.check_report(report, mode)
            self.assertEqual(metrics["selection"], mode)
            self.assertEqual(int(metrics["kept_entries"]), kept)
            self.assertEqual(int(metrics["entries"]), sum(e["entries"] for e in self.expected))
            verify = subprocess.run([str(CONVERTER), "--verify", str(output)], check=True,
                                    stdout=subprocess.PIPE, universal_newlines=True)
            values = preserve.parse_verify_output(verify.stdout)
            self.assertEqual(values, {"entries": kept, "sources": 2, "selection": mode,
                                      "source_entries": int(metrics["entries"])})
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run([str(CONVERTER), "--verify", self.sources[0]], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_worker_finalize_and_archive_completeness_chain(self):
        stage = self.root / "stage"
        catalog = self.root / "run7777.list"
        catalog.write_text("\n".join(self.sources) + "\n")
        marker = preserve.prepare_run(str(catalog), "P17B", "EH1", str(stage), str(PROJECT), 1,
                                      require_registered=False)
        run_dir = Path(marker["run_dir"])
        for proc_id in range(marker["chunk_count"]):
            for _ in range(2):  # second pass must reopen, verify, and skip the finished chunk
                subprocess.run(["bash", str(PROJECT / "scripts" / "worker.sh"), str(run_dir),
                                str(proc_id), str(PROJECT)], check=True, env=self.env)
            report = (run_dir / "status" / "{:06d}.ok".format(proc_id)).read_text()
            self.assertIn("SKIP output=", report)
            self.assertFalse(list((run_dir / "status").glob("*.fail")))
        result = preserve.finalize_run(run_dir, stage)
        expected_kept = sum(e["kept"] for e in self.expected)
        self.assertEqual((result["source_kept_entries"], result["source_entries"]),
                         (expected_kept, sum(e["entries"] for e in self.expected)))
        verified = preserve.verify_local_run(run_dir, root_verifier=CONVERTER)
        self.assertEqual((verified["kept_entries"], verified["ad_selection"], verified["outputs"]),
                         (expected_kept, "on", 2))

        (stage / "catalog-inventory.json").write_text(preserve.json.dumps({
            "kind": "dyb-preserve-catalog-inventory",
            "schema_version": preserve.SCHEMA_VERSION,
            "records": [{
                "period": "P17B", "hall": "EH1", "stem": "run7777", "catalog": str(catalog),
                "catalog_sha256": marker["catalog_sha256"], "input_count": 2,
            }],
        }))
        everything = preserve.verify_all(stage, root_verifier=CONVERTER)
        self.assertEqual((everything["runs"], everything["kept_entries"], everything["ad_selection"]),
                         (1, expected_kept, "on"))

        run_json = run_dir / "run.json"
        original = run_json.read_text()
        run_json.write_text(original.replace('"source_kept_entries": {}'.format(expected_kept),
                                             '"source_kept_entries": {}'.format(expected_kept + 1)))
        with self.assertRaisesRegex(ValueError, "do not match run.json"):
            preserve.verify_local_run(run_dir)
        run_json.write_text(original)
        first_chunk = subprocess.run(
            [str(CONVERTER), "--verify", str(run_dir / "outputs" / "run7777.slim.000000.root")],
            check=True, stdout=subprocess.PIPE, universal_newlines=True).stdout
        with self.assertRaisesRegex(ValueError, "does not match its timing rows"):
            preserve.check_verified_chunk(first_chunk, (self.expected[1]["kept"],
                                                        self.expected[1]["entries"], 1), "on")
        preserve.cleanup_run(run_dir, stage, marker["catalog_sha256"])
        self.assertFalse(run_dir.exists())


if __name__ == "__main__":
    unittest.main()
