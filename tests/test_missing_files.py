import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.export_missing_files import ROOT, missing_rows


class MissingFileListTest(unittest.TestCase):
    def test_only_unique_catalog_matched_enoent_omissions_are_exported(self):
        ledger = json.loads((ROOT / "input-issues.json").read_text())
        issue = next(item for item in ledger["issues"] if item["status"] == "skipped")
        sample = dict(issues=[copy.deepcopy(issue)], summary=dict(skipped_inputs=1))
        self.assertEqual(len(missing_rows(sample)), 1)

        bad = copy.deepcopy(sample)
        bad["issues"][0]["mini_check"]["attempts"][-1]["errno"] = 5
        with self.assertRaisesRegex(ValueError, "ENOENT"):
            missing_rows(bad)

        bad = copy.deepcopy(sample)
        bad["issues"][0]["catalog_path"] = "/unrelated.root"
        with self.assertRaisesRegex(ValueError, "fixed catalog"):
            missing_rows(bad)

        bad = copy.deepcopy(sample)
        bad["issues"].append(copy.deepcopy(issue))
        bad["summary"]["skipped_inputs"] = 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            missing_rows(bad)


if __name__ == "__main__":
    unittest.main()
