import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.export_missing_files import ROOT, missing_rows
from scripts import export_missing_files


class MissingFileListTest(unittest.TestCase):
    def test_only_unique_catalog_matched_enoent_omissions_are_exported(self):
        ledger = json.loads((ROOT / "input-issues.json").read_text())
        issue = next(item for item in ledger["issues"] if item["status"] == "skipped" and
                     item.get("category", "missing") == "missing")
        sample = dict(issues=[copy.deepcopy(issue)], summary=dict(skipped_inputs=1))
        self.assertEqual(len(missing_rows(sample)), 1)
        broken = dict(copy.deepcopy(issue),category='broken',mini_check=dict(status='broken'))
        mixed = dict(issues=[copy.deepcopy(issue),broken,dict(broken,status='skip_recorded')],
                     summary=dict(skipped_inputs=2,missing_skipped_inputs=1,broken_skipped_inputs=1))
        self.assertEqual(missing_rows(mixed),missing_rows(sample))
        bad = copy.deepcopy(mixed);bad['summary']['missing_skipped_inputs']=0
        with self.assertRaisesRegex(ValueError,'summary'):missing_rows(bad)
        bad = copy.deepcopy(sample);bad['issues'][0]['category']='io_error'
        with self.assertRaisesRegex(ValueError,'category'):missing_rows(bad)

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

        # Main must report the missing-only count even when total skips includes broken inputs.
        mixed.update(updated_at=ledger['updated_at'],disk_uuid=ledger['disk_uuid'])
        mixed['summary'].update(completed_runs=1,expected_runs=2,processed_inputs=1,expected_inputs=4)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'input-issues.json').write_text(json.dumps(mixed))
            with mock.patch.object(export_missing_files,'ROOT',root), \
                 mock.patch('preserve.export_broken_files') as export_broken:
                export_missing_files.main()
            self.assertEqual((root/'missing-input-files.list').read_text(),issue['mini_check']['path']+'\n')
            self.assertIn('**1** omitted',(root/'INPUT_ISSUES.md').read_text())
            export_broken.assert_called_once_with(root,mixed)


if __name__ == "__main__":
    unittest.main()
