from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import preserve


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patch = mock.patch('preserve.MINI_ROOT', str(self.root / 'mini'))
        patch.start(); self.addCleanup(patch.stop)
        originals = ['/dybfs/rec/P17B/rec/runs_0071000/runs_0071800/run_0071820/'
                     'recon.Neutrino.0071820.Physics.EH1-Merged.P17B-I._{:04d}.root'.format(i) for i in range(4)]
        for path in preserve.resolve_inputs(originals):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b'root tiny fixture')
        catalog = self.root / 'run71820.list'
        catalog.write_text('\n'.join(originals) + '\n')
        self.stage, self.code = self.root / 'stage', self.root / 'code'
        (self.code / 'scripts').mkdir(parents=True)
        shutil.copyfile(Path(preserve.__file__).parent / 'scripts/worker.sh', self.code / 'scripts/worker.sh')
        self.marker = preserve.prepare_run(str(catalog), 'P17B', 'EH1', str(self.stage), str(self.code), 2,
            require_registered=False, skip_missing_minis=True, ad_selection='on')
        self.run = Path(self.marker['run_dir'])
        self.receipts = [dict(cluster_id=42, chunk_count=2, submitted_at='2026-10-13T00:00:00+00:00')]
        preserve.atomic_json(self.run / 'submissions.json', self.receipts)
        self.good = [self.run / 'status/000000.ok', self.run / 'status/000000.report',
                     self.run / 'reports' / (preserve.output_prefix(self.marker) + '.slim.000000.sources.tsv'),
                     self.run / 'outputs' / (preserve.output_prefix(self.marker) + '.slim.000000.root')]
        for path in self.good:
            path.write_bytes(b'root tiny unchanged evidence')
        self.saved = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.good}
        self.write_failure('000001')

    def write_failure(self, chunk, reason='No such file or directory'):
        (self.run / 'status' / (chunk + '.fail')).write_text(
            'rc=1\nhost=test.ihep.ac.cn\njob=42.{}\nfinished=2026-10-13T00:01:00Z\n'.format(int(chunk)))
        (self.run / 'logs' / (chunk + '.out')).write_text('')
        (self.run / 'logs' / (chunk + '.err')).write_text('{}: line 54: {}/setup.sh: {}\n'.format(
            self.code / 'scripts/worker.sh', preserve.DEFAULT_LCG_VIEW, reason))

    def retry(self):
        return preserve.submit_run(self.run, self.stage, 'dyw', 'short', 1024, True, 300, True, 2)

    def test_exact_startup_proof_rejects_mixed_input_and_unknown_errors(self):
        for reason, eligible in [('No such file or directory', True), ('Transport endpoint is not connected', True),
                                 ('Input/output error', False), ('Permission denied', False)]:
            self.write_failure('000001', reason)
            self.assertEqual(preserve.lcg_startup_errors(self.run, self.stage)['startup'], eligible)
        self.write_failure('000001')
        proof = preserve.lcg_startup_errors(self.run, self.stage)
        self.assertEqual([s['catalog_index'] for s in proof['failures'][0]['sources']], [2, 3])
        before = preserve.run_status(self.run, self.stage)
        changed = dict(before, failure_updates={'000001': [99, 99]})
        with mock.patch('preserve.run_status', side_effect=[before, changed]):
            self.assertTrue(preserve.lcg_startup_errors(self.run, self.stage)['recheck'])
        self.write_failure('000000')
        (self.run / 'logs/000000.err').write_text(' *** Break *** bus error\nworker.sh: line 70: Bus error (core dumped)\n')
        (self.run / 'status/000000.fail').write_text('rc=135\n')
        self.assertFalse(preserve.lcg_startup_errors(self.run, self.stage)['startup'])
        (self.run / 'status/000000.fail').unlink()
        for error in ('ERROR missing required tree: /mini/existing.root\n', 'ERROR checksum mismatch\n'):
            (self.run / 'logs/000001.err').write_text(error)
            self.assertFalse(preserve.lcg_startup_errors(self.run, self.stage)['startup'])
        self.write_failure('000001')
        (self.run / 'logs/000001.out').write_text('converter already ran')
        self.assertFalse(preserve.lcg_startup_errors(self.run, self.stage)['startup'])
        self.write_failure('000001')
        (self.run / 'status/000001.report').write_text('RESULT partial converter work')
        self.assertFalse(preserve.lcg_startup_errors(self.run, self.stage)['startup'])

    def test_identity_catalog_worker_and_io_checks_remain_fatal(self):
        failure = self.run / 'status/000001.fail'
        failure.write_text(failure.read_text().replace('42.1', '43.1'))
        with self.assertRaisesRegex(ValueError, 'submission/chunk'):
            preserve.lcg_startup_errors(self.run, self.stage)
        self.write_failure('000001')
        worker = self.code / 'scripts/worker.sh'
        source = worker.read_bytes(); worker.write_bytes(source + b'\n')
        with self.assertRaisesRegex(ValueError, 'known mini worker'):
            preserve.lcg_startup_errors(self.run, self.stage)
        worker.write_bytes(source)
        chunks = self.run / 'chunks/000001.list'
        original = chunks.read_bytes(); chunks.write_text('/unrelated.root\n')
        with self.assertRaisesRegex(ValueError, 'fixed mini catalog'):
            preserve.lcg_startup_errors(self.run, self.stage)
        chunks.write_bytes(original)
        with mock.patch('preserve.sha256_file', side_effect=OSError(5, 'Input/output error')):
            with self.assertRaises(OSError): preserve.lcg_startup_errors(self.run, self.stage)
        (self.run / 'catalog.list').write_text('/unrelated.root\n')
        with self.assertRaisesRegex(ValueError, 'catalog checksum'):
            preserve.lcg_startup_errors(self.run, self.stage)

    def test_locked_second_attempt_preserves_proof_and_good_chunks_before_overwrite(self):
        failure = (self.run / 'status/000001.fail').read_text()
        error = (self.run / 'logs/000001.err').read_text()
        def hep_sub(*unused, **kwargs):
            journal = preserve.load_json(self.run / 'startup-recovery.json')
            self.assertEqual(journal[0]['previous_submissions'], self.receipts)
            self.assertEqual(journal[0]['proof']['failures'][0]['failure'], failure)
            self.assertEqual(journal[0]['proof']['failures'][0]['stderr'], error)
            self.assertEqual(len(journal[0]['good_files']), 5)
            self.assertFalse((self.run / 'status/000001.fail').exists())
            (self.run / 'logs/000001.err').write_text('new worker can overwrite log now')
            return mock.Mock(returncode=0, stdout='2 job(s) submitted to cluster 43.')
        with mock.patch('preserve.scheduler_jobs', return_value=[]) as query, \
                mock.patch('preserve.subprocess.run', side_effect=hep_sub) as submit:
            result = self.retry()
        self.assertEqual(result['cluster_id'], 43)
        self.assertEqual(query.call_count, 1, 'the typed query failure must remain before all mutations')
        self.assertEqual(submit.call_count, 1)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.saved}, self.saved)
        self.assertEqual(preserve.load_json(self.run / preserve.MARKER_NAME), self.marker, 'no new gaps/metadata changes')
        with mock.patch('preserve.scheduler_jobs', return_value=[]), mock.patch('preserve.scratch_quota', return_value={}):
            staged = preserve.pool_status(self.stage)['runs'][0]
        self.assertEqual(staged['startup_recovery'], preserve.load_json(self.run / 'startup-recovery.json'))
        self.assertIsNone(staged['startup_failure'])
        self.write_failure('000001')
        with mock.patch('preserve.scheduler_jobs', return_value=[]), mock.patch('preserve.subprocess.run') as submit:
            with self.assertRaisesRegex(RuntimeError, 'exhausted'): self.retry()
            submit.assert_not_called()

    def test_writer_intent_query_outage_and_unknown_failures_do_not_mutate(self):
        original_failure = (self.run / 'status/000001.fail').read_bytes()
        for case in ('writer', 'marker', 'intent', 'query', 'unknown', 'ambiguous_submit'):
            with self.subTest(case=case):
                intent = self.run / 'submission-intent.json'
                running = self.run / 'status/000001.running'
                if case == 'marker': running.write_text('old writer')
                if case == 'intent': preserve.atomic_json(intent, dict(uncertain=True))
                if case == 'unknown': (self.run / 'logs/000001.err').write_text('SIGBUS\n')
                jobs = [dict(Cmd=str(self.run / 'array-job.sh'), Iwd=str(self.run))] if case == 'writer' else []
                with mock.patch('preserve.scheduler_jobs', side_effect=(preserve.SchedulerQueryUnavailable('read failure')
                        if case == 'query' else None), return_value=jobs), \
                        mock.patch('preserve.subprocess.run', return_value=mock.Mock(returncode=1, stdout='connection lost')) as submit:
                    with self.assertRaises(RuntimeError): self.retry()
                    self.assertEqual(submit.call_count, int(case == 'ambiguous_submit'))
                if case == 'ambiguous_submit':
                    self.assertTrue(intent.exists(), 'uncertain submission intent must remain')
                    with mock.patch('preserve.scheduler_jobs', return_value=[]), mock.patch('preserve.subprocess.run') as submit:
                        with self.assertRaisesRegex(RuntimeError, 'unresolved intent'): self.retry()
                        submit.assert_not_called()
                    continue
                self.assertEqual((self.run / 'status/000001.fail').read_bytes(), original_failure)
                self.assertFalse((self.run / 'startup-recovery.json').exists())
                if case == 'marker': running.unlink()
                if case == 'intent':
                    self.assertEqual(preserve.load_json(intent), dict(uncertain=True)); intent.unlink()
                self.write_failure('000001')


if __name__ == '__main__':
    unittest.main()
