"""Offline independent arithmetic and completeness checks for the bounded pilot."""
import collections
import csv
from fractions import Fraction
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

BASE = Path(__file__).resolve().parent.parent / 'estimates/noenergy-2026-09-27'
SPEC = importlib.util.spec_from_file_location('benchmark', BASE / 'benchmark.py')
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


class BenchmarkTests(unittest.TestCase):
    def test_recorded_projections_and_rejection_of_incomplete_results(self):
        plan = json.loads((BASE / 'plan.json').read_text())
        summary = json.loads((BASE / 'summary.json').read_text())
        rows = [json.loads(s) for s in (BASE / 'results.jsonl').read_text().splitlines()]
        baseline = [r for r in rows if r['kind'] == 'baseline']
        alternatives = [r for r in rows if r['kind'] == 'alternative']
        by_index = {r['index']: r for r in baseline}
        counts = collections.Counter(r['group'] for r in baseline)
        self.assertEqual(len(baseline), 48)
        self.assertEqual(len({r['mini'] for r in baseline}), 48)
        self.assertEqual(len(alternatives), 100)
        self.assertEqual(len({(r['group'], r['variant']) for r in alternatives}), 100)
        self.assertTrue(all(r['passed'] for r in baseline + alternatives))
        self.assertEqual(sum(r['entries'] for r in baseline), 36262542)
        weights = {r['index']: Fraction(plan['groups'][r['group']], counts[r['group']])
                   for r in baseline}
        # Expand per-file rational weights independently of the group-mean summarizer.
        total = sum(r['bytes'] * weights[r['index']] for r in baseline)
        self.assertTrue(math.isclose(float(total), summary['baseline_bytes'], rel_tol=1e-14))
        cpu = math.fsum(r['cpu_seconds'] * float(weights[r['index']]) for r in baseline) / 86400
        self.assertTrue(math.isclose(cpu, summary['baseline_cpu_days'], rel_tol=1e-14))
        for name, result in summary['alternatives'].items():
            paired = {r['group']: r for r in alternatives if r['variant'] == name}
            total = sum(r['bytes'] * weights[r['index']] * Fraction(
                paired[r['group']]['bytes'], by_index[paired[r['group']]['index']]['bytes'])
                for r in baseline)
            self.assertTrue(math.isclose(float(total), result['bytes'], rel_tol=1e-14))
        for r in baseline:
            report = list(csv.DictReader(r['source_report'].splitlines(), delimiter='\t'))
            self.assertEqual(len(report), 1)
            q = report[0]
            self.assertEqual(q['path'], r['mini'])
            self.assertEqual(int(q['entries']), r['entries'])
            self.assertEqual(int(q['keptEntries']), r['entries'])
            ns = ((int(q['lastTriggerTimeSec']) - int(q['firstTriggerTimeSec'])) * 10**9
                  + int(q['lastTriggerTimeNanoSec']) - int(q['firstTriggerTimeNanoSec']))
            self.assertEqual(float(q['dataDurationSeconds']), ns / 1e9)
        damaged = (rows[:-1],
                   [r for r in rows if not (r['kind'] == 'alternative' and r['index'] == 1)],
                   rows + [dict(kind='summary', attempted=48, failures=1)])
        for bad_rows in damaged:
            with self.assertRaises(AssertionError):
                BENCHMARK.summarize(BASE / 'plan.json', SimpleNamespace(
                    read_text=lambda: '\n'.join(json.dumps(r) for r in bad_rows)))


if __name__ == '__main__':
    unittest.main()
