import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

import preserve


class PipelineTests(unittest.TestCase):
    def test_command_diagnostics_and_bounded_read_only_status_retries(self):
        with self.assertRaises(RuntimeError) as error:
            preserve.run_command([sys.executable, '-c',
                                  'import sys; print("partial output"); sys.stderr.write("quota unavailable"); sys.exit(7)'])
        self.assertIn('command failed (7)', str(error.exception))
        self.assertIn('partial output', str(error.exception))
        self.assertIn('stderr:\nquota unavailable', str(error.exception))
        good = mock.Mock(returncode=0, stdout='{"queue": {"outstanding": 0}}', stderr='')
        bad_json = mock.Mock(returncode=0, stdout='interrupted JSON', stderr='')
        with mock.patch('preserve.remote_call', side_effect=[RuntimeError('temporary status failure'), bad_json, good]) as call, \
                mock.patch('preserve.time.sleep') as sleep:
            result, status = preserve.remote_json('host', '/code', ['_pool'])
        self.assertEqual((result['queue']['outstanding'], status, call.call_count), (0, 0, 3))
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])
        for command, attempts in [('_pool', 3), ('_submit', 1), ('_prepare', 1), ('_cleanup', 1)]:
            with mock.patch('preserve.remote_call', side_effect=RuntimeError('persistent failure')) as call, \
                    mock.patch('preserve.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'persistent failure'):
                    preserve.remote_json('host', '/code', [command])
            self.assertEqual(call.call_count, attempts, 'mutations must never be replayed automatically')

    def test_completed_arrays_reserve_actual_bytes_only_after_jobs_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary) / 'staging'
            run = stage / 'P17B/EH1/run12345'
            (run / 'outputs').mkdir(parents=True)
            (run / 'outputs/chunk.root').write_bytes(b'root')
            preserve.atomic_json(run / preserve.MARKER_NAME,
                                 dict(kind='dyb-preserve-run', run_dir=str(run), chunk_count=16))
            with mock.patch('preserve.scheduler_jobs', return_value=[]) as jobs, \
                    mock.patch('preserve.scratch_quota', return_value={}), \
                    mock.patch('preserve.run_status', return_value=dict(state='RUNNING', running=1)) as status:
                self.assertEqual(preserve.pool_status(str(stage))['reserved_bytes'], 16 * preserve.GIB)
                status.return_value = dict(state='COMPLETE', running=0)
                jobs.return_value = [dict(Iwd=str(run), JobStatus=2, RequestCpus=1)]
                self.assertEqual(preserve.pool_status(str(stage))['reserved_bytes'], 16 * preserve.GIB)
                jobs.return_value = []
                self.assertEqual(preserve.pool_status(str(stage))['reserved_bytes'], 4 + 16 * 1024 ** 2)

    def test_admission_quota_and_scheduler_fail_closed(self):
        pool = dict(queue=dict(outstanding=284), reserved_bytes=284 * preserve.GIB,
                    output_bytes=20 * preserve.GIB,
                    quota=dict(free_bytes=480 * preserve.GIB, used_files=5000, limit_files=200000))
        check = lambda n: preserve.admission_reason(pool, n, 300, 400 * preserve.GIB, 1000 * preserve.GIB)
        self.assertIsNone(check(16))
        self.assertEqual(check(17), "job cap")
        pool['queue']['outstanding'] = 0
        pool['reserved_bytes'] = 390 * preserve.GIB
        self.assertEqual(check(16), "staging reservation budget")
        pool['reserved_bytes'] = 284 * preserve.GIB
        pool['quota']['free_bytes'] = 300 * preserve.GIB
        self.assertEqual(check(16), "scratch quota headroom")
        response = mock.Mock(returncode=0, stdout='[]', stderr='failed to query one schedd')
        with mock.patch('preserve.subprocess.run', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'scheduler query failed'):
                preserve.scheduler_jobs('/stage')
        response.stderr = ''
        response.stdout = '\n'
        with mock.patch('preserve.subprocess.run', return_value=response):
            self.assertEqual(preserve.scheduler_jobs('/stage'), [])
        response.stdout = '[{"Cmd":"/stage/run/array-job.sh","JobStatus":1,"RequestCpus":1}]\n[]\n'
        with mock.patch('preserve.subprocess.run', return_value=response):
            self.assertEqual(preserve.queue_counts(preserve.scheduler_jobs('/stage'))['idle'], 1)
        response.stdout = json.dumps([dict(Cmd='/stage/run/array-job.sh', JobStatus=2, RequestCpus=4)])
        with mock.patch('preserve.subprocess.run', return_value=response):
            with self.assertRaisesRegex(ValueError, 'CPU request'):
                preserve.scheduler_jobs('/stage')
        quota = mock.Mock(stdout='Disk quotas:\n /scratchfs2 1024 524288000 532466892 - 4000 200000 200000 -\n')
        with mock.patch('preserve.run_command', return_value=quota), \
                mock.patch('preserve.shutil.disk_usage', return_value=mock.Mock(free=1000 * preserve.GIB)):
            self.assertEqual(preserve.scratch_quota('/scratchfs2/stage')['limit_bytes'], 500 * preserve.GIB)
            quota.stdout = quota.stdout.replace('/scratchfs2 ', '/scratchfs2/long/staging/path\n   ')
            self.assertEqual(preserve.scratch_quota('/scratchfs2/long/staging/path')['limit_bytes'], 500 * preserve.GIB)

    def test_submission_intent_recovery_and_no_duplicate_or_cap_overshoot(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / 'run'
            run.mkdir()
            marker = dict(chunk_count=16)
            invoke = lambda: preserve.submit_locked(run, marker, temporary, 'dyw', 'short', 1024, False, 300)
            with mock.patch('preserve.scheduler_jobs', return_value=[dict(Cmd='/other')] * 285), \
                    mock.patch('preserve.subprocess.run') as submit:
                with self.assertRaisesRegex(RuntimeError, 'cap'):
                    invoke()
                submit.assert_not_called()
            preserve.atomic_json(run / 'submission-intent.json', dict(submitted_at=preserve.utc_now(), chunk_count=16))
            own = dict(Cmd=str(run / 'array-job.sh'), ClusterId=42)
            with mock.patch('preserve.scheduler_jobs', return_value=[own]), \
                    mock.patch('preserve.subprocess.run') as submit:
                self.assertEqual(invoke()['cluster_id'], 42)
                self.assertEqual(invoke()['cluster_id'], 42)
                submit.assert_not_called()
            preserve.atomic_json(run / 'submission-intent.json', {})
            with mock.patch('preserve.scheduler_jobs', return_value=[]):
                with self.assertRaisesRegex(RuntimeError, 'ambiguous submission'):
                    invoke()

    def test_expired_incomplete_arrays_retry_only_after_jobs_exit_and_within_attempt_limit(self):
        record = preserve.inventory_records()[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'provenance').mkdir()
            local = root / 'data'
            local.mkdir()
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--files-per-job', '2000', '--skip-missing-minis'])
            marker = dict(record, run_dir='/stage/run', code_root='/code', chunk_count=1,
                          files_per_job=2000, selection=preserve.selection_record('off'),
                          input_source='mini', skip_missing_minis=True)
            for state, queued, attempts, expired, retries, error in (
                    ('PENDING', False, 1, True, 1, 'snapshot boundary'),
                    ('RUNNING', False, 1, True, 1, 'snapshot boundary'),
                    ('PENDING', False, 1, False, 0, 'snapshot boundary'),
                    ('RUNNING', True, 1, True, 0, 'wait timeout'),
                    ('PENDING', False, 2, True, 0, 'exhausted submission attempts')):
                with self.subTest(state=state, queued=queued, attempts=attempts, expired=expired):
                    submissions = [dict(cluster_id=i, submitted_at=(
                        '2000-01-01T00:00:00+00:00' if expired else preserve.utc_now()))
                        for i in range(attempts)]
                    jobs = [dict(Iwd=marker['run_dir'], JobStatus=2, RequestCpus=1)] if queued else []
                    pool = dict(runs=[dict(marker=marker, submissions=submissions, intent=False,
                                          status=dict(state=state, running=int(state == 'RUNNING'), failed=0))],
                                jobs=jobs, queue=preserve.queue_counts(jobs), reserved_bytes=preserve.GIB,
                                output_bytes=0, quota=dict(free_bytes=500 * preserve.GIB, used_files=10,
                                                         limit_files=200000))
                    queries, submitted = [], []

                    def remote(alias, code, arguments):
                        if arguments[0] == '_pool':
                            if queries:
                                raise RuntimeError('snapshot boundary')
                            queries.append(True)
                            return pool, 0
                        self.assertEqual(arguments[0], '_submit', 'no finalization or cleanup of incomplete data')
                        self.assertIn('--resubmit', arguments)
                        submitted.append(arguments)
                        return dict(cluster_id=100), 0

                    with mock.patch('preserve.remote_json', side_effect=remote), \
                            mock.patch('preserve.archive_progress', return_value=({}, [])), \
                            mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                            mock.patch('preserve.time.sleep'):
                        with self.assertRaisesRegex(RuntimeError, error):
                            preserve.run_pipeline(args, [record], {}, local)
                    self.assertEqual(len(submitted), retries)

    def test_pipeline_adopts_inflight_refills_during_transfer_and_only_cleans_verified(self):
        records = preserve.inventory_records()[:5]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'provenance').mkdir()
            local = root / 'data'
            local.mkdir()
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--max-jobs', '2', '--max-staged-gib', '3', '--files-per-job', '2000',
                '--skip-missing-minis', '--delete-remote-after-sync'])
            args.poll_seconds = 0.001
            staged, jobs, submitted, verified, cleaned = {}, {}, [], set(), []
            transferring = threading.Event()
            refilled = threading.Event()
            peak = [0]

            def prepare(record):
                marker = dict(record, run_dir='/stage/' + record['stem'], code_root='/code',
                              chunk_count=1, files_per_job=2000, selection=preserve.selection_record('off'),
                              input_source='mini', skip_missing_minis=True)
                staged[marker['run_dir']] = dict(marker=marker, submissions=[], intent=False,
                                                status=dict(state='PENDING', running=0, failed=0))
                return marker

            first = prepare(records[0])
            staged[first['run_dir']]['submissions'] = [dict(cluster_id=100, submitted_at=preserve.utc_now())]
            jobs[first['run_dir']] = dict(Iwd=first['run_dir'], JobStatus=2, RequestCpus=1)

            def remote(alias, code, arguments):
                command = arguments[0]
                options = dict(zip(arguments[1::2], arguments[2::2]))
                directory = options.get('--run-dir')
                if command == '_pool':
                    if len(submitted) >= 1:
                        for path in list(jobs):
                            if path == first['run_dir'] or refilled.is_set():
                                jobs.pop(path)
                                staged[path]['status'] = dict(state='COMPLETE', running=0, failed=0)
                    return copy.deepcopy(dict(runs=list(staged.values()), jobs=list(jobs.values()),
                        queue=preserve.queue_counts(list(jobs.values())), reserved_bytes=len(staged) * preserve.GIB,
                        output_bytes=0, quota=dict(free_bytes=500 * preserve.GIB, used_files=10, limit_files=200000))), 0
                if command == '_prepare':
                    self.assertLess(len(staged), 3, 'staging backpressure must include completed transfers')
                    return prepare(next(r for r in records if r['catalog'] == options['--catalog'])), 0
                if command == '_submit':
                    self.assertNotEqual(directory, first['run_dir'], 'must adopt existing cluster')
                    self.assertNotIn(directory, submitted)
                    submitted.append(directory)
                    jobs[directory] = dict(Iwd=directory, JobStatus=2, RequestCpus=1)
                    peak[0] = max(peak[0], len(jobs))
                    self.assertLessEqual(len(jobs), 2)
                    submission = dict(cluster_id=100 + len(submitted), submitted_at=preserve.utc_now())
                    staged[directory]['submissions'] = [submission]
                    if len(submitted) == 2:
                        self.assertTrue(transferring.wait(2), 'first transfer must already be running')
                        refilled.set()
                    return submission, 0
                if command == '_finalize':
                    self.assertNotIn(directory, jobs, 'must wait until array exits scheduler')
                    return staged[directory]['marker'], 0
                if command == '_cleanup':
                    self.assertIn(directory, verified, 'must verify disk copy before deletion')
                    cleaned.append(directory)
                    del staged[directory]
                    return dict(deleted=directory), 0
                raise AssertionError(command)

            def sync(alias, marker, *unused):
                if marker['run_dir'] == first['run_dir']:
                    transferring.set()
                    self.assertTrue(refilled.wait(5), 'submission must continue during transfer')
                verified.add(marker['run_dir'])
                return dict(files=1, bytes=10, directory=marker['run_dir'])

            with mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync):
                preserve.run_pipeline(args, records, {}, local)
            self.assertEqual((len(submitted), len(cleaned), peak[0]), (4, 5, 2))
            self.assertEqual(set(cleaned), verified)
            # A failed disk checksum retains staging and surfaces the error to monitoring.
            marker = prepare(records[0])
            staged[marker['run_dir']]['status']['state'] = 'COMPLETE'
            cleaned.clear()
            with mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.sync_run', side_effect=ValueError('checksum mismatch')):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    preserve.run_pipeline(args, records[:1], {}, local)
            self.assertEqual(cleaned, [])
            self.assertIn(marker['run_dir'], staged)
            self.assertIn('checksum mismatch', preserve.load_json(root / 'provenance/controller.json')['error'])

            # A fatal status query may overlap a successful copy. Publish its ledger, but retain staging.
            copying, status_failed = threading.Event(), threading.Event()
            verified.discard(marker['run_dir'])
            queries = [0]

            def failing_remote(alias, code, arguments):
                if arguments[0] == '_pool':
                    queries[0] += 1
                    if queries[0] == 2:
                        self.assertTrue(copying.wait(2))
                        status_failed.set()
                        raise RuntimeError('read-only status failed')
                return remote(alias, code, arguments)

            def finishing_copy(*unused):
                copying.set()
                self.assertTrue(status_failed.wait(2))
                verified.add(marker['run_dir'])
                return dict(files=1, bytes=10, directory=marker['run_dir'])

            def reconcile(*unused, **kwargs):
                if not kwargs:
                    self.assertIn(marker['run_dir'], verified)

            with mock.patch('preserve.remote_json', side_effect=failing_remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), \
                    mock.patch('preserve.refresh_input_issues', side_effect=reconcile) as ledger, \
                    mock.patch('preserve.validate_local_mount'), \
                    mock.patch('preserve.sync_run', side_effect=finishing_copy):
                with self.assertRaisesRegex(RuntimeError, 'read-only status failed'):
                    preserve.run_pipeline(args, records[:1], {}, local)
            ledger.assert_called_with(local)
            self.assertEqual(cleaned, [])
            self.assertIn(marker['run_dir'], staged)
            self.assertIn('read-only status failed', preserve.load_json(root / 'provenance/controller.json')['error'])

    def test_explicit_hold_retains_pending_inputs_and_drains_unaffected_retries_and_transfers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / 'data'
            local.mkdir()
            (root / 'provenance').mkdir()
            records, staged = [], {}
            for number in (10001, 10002, 10003):
                record = dict(period='P17B', hall='EH1', stem='run' + str(number),
                              catalog='/catalog/run{}.list'.format(number), catalog_sha256=str(number), input_count=2)
                records.append(record)
                backup = root / 'catalogs/P17B/EH1' / (record['stem'] + '.list')
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text('/tiny/input1.root\n/tiny/input2.root\n')
                marker = dict(record, run_dir='/stage/' + record['stem'], code_root='/code', chunk_count=2,
                              files_per_job=1, selection=preserve.selection_record('on'),
                              input_source='mini', skip_missing_minis=True)
                staged[record['stem']] = dict(marker=marker, intent=False,
                    submissions=[dict(cluster_id=number, submitted_at='2000-01-01T00:00:00+00:00')],
                    status=dict(state='FAILED' if number == 10001 else 'PENDING' if number == 10002 else 'COMPLETE',
                                ok=1 if number != 10003 else 2, failed=int(number == 10001), running=0, chunks=2))
            held_before = copy.deepcopy(staged['run10001'])
            args = preserve.build_parser().parse_args([
                'run', '--local-root', str(local), '--mount-root', str(root),
                '--remote-stage-root', '/stage', '--remote-code-root', '/code',
                '--files-per-job', '1', '--skip-missing-minis', '--ad-selection', 'on',
                '--delete-remote-after-sync', '--hold-run', 'P17B/EH1/run10001'])
            args.poll_seconds = 0.001
            submitted, verified, cleaned = [], set(), []

            def remote(alias, code, arguments):
                command = arguments[0]
                if command == '_pool':
                    return copy.deepcopy(dict(runs=list(staged.values()), jobs=[], queue=preserve.queue_counts([]),
                        reserved_bytes=3 * preserve.GIB, output_bytes=10,
                        quota=dict(free_bytes=500 * preserve.GIB, used_files=10, limit_files=200000))), 0
                stem = Path(arguments[arguments.index('--run-dir') + 1]).name
                self.assertNotEqual(stem, 'run10001', 'held chunks and failure evidence must not be mutated')
                if command == '_submit':
                    self.assertEqual(stem, 'run10002')
                    self.assertIn('--resubmit', arguments)
                    submitted.append(stem)
                    staged[stem]['status'].update(state='COMPLETE', ok=2)
                    return dict(cluster_id=20002), 0
                if command == '_finalize':
                    return staged[stem]['marker'], 0
                self.assertEqual(command, '_cleanup')
                self.assertIn(stem, verified)
                cleaned.append(stem)
                del staged[stem]
                return dict(deleted=stem), 0

            def sync(alias, marker, *unused):
                verified.add(marker['stem'])
                return dict(files=1, bytes=10, directory=marker['run_dir'])

            with mock.patch('preserve.CATALOG_BACKUP_ROOT', root / 'catalogs'), \
                    mock.patch('preserve.remote_json', side_effect=remote), \
                    mock.patch('preserve.archive_progress', return_value=({}, [])), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                    mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync):
                with self.assertRaisesRegex(RuntimeError, 'only held incomplete runs remain'):
                    preserve.run_pipeline(args, records, {}, local)
            self.assertEqual(submitted, ['run10002'])
            self.assertEqual(set(cleaned), {'run10002', 'run10003'})
            self.assertEqual(staged['run10001'], held_before)
            state = preserve.load_json(root / 'provenance/controller.json')
            self.assertEqual(state['held_runs'][0]['run'], 'P17B/EH1/run10001')
            self.assertEqual(state['held_runs'][0]['status']['ok'], 1)
            # Holding staging never adds a completed local run or a missing-file skip.
            preserve.atomic_json(local / 'catalog-inventory.json', dict(records=records))
            progress, saved = preserve.archive_progress(local)
            self.assertEqual((progress['expected_runs'], progress['expected_inputs'], progress['pending_inputs']), (3, 6, 6))
            self.assertEqual((progress['completed_runs'], progress['skipped_inputs'], saved), (0, 0, []))
            self.assertFalse(progress['complete'])

            args.hold_run = ['P17B/EH1/not-in-catalog']
            with self.assertRaisesRegex(ValueError, 'outside the selected fixed catalog'):
                preserve.run_pipeline(args, records, {}, local)
            args.hold_run = ['P17B/EH1/run10001']
            job = dict(Iwd=held_before['marker']['run_dir'], JobStatus=2, RequestCpus=1)
            pool = dict(runs=[held_before], jobs=[job], queue=preserve.queue_counts([job]),
                        quota={}, reserved_bytes=preserve.GIB, output_bytes=10)
            with mock.patch('preserve.remote_json', return_value=(pool, 0)) as call, \
                    mock.patch('preserve.CATALOG_BACKUP_ROOT', root / 'catalogs'), \
                    mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'):
                with self.assertRaisesRegex(RuntimeError, 'scheduler jobs or worker markers'):
                    preserve.run_pipeline(args, records, {}, local)
            self.assertTrue(all(c.args[2][0] == '_pool' for c in call.call_args_list))


if __name__ == '__main__':
    unittest.main()
