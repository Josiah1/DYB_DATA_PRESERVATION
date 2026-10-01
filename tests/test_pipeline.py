import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import preserve


class PipelineTests(unittest.TestCase):
    def test_completed_arrays_reserve_actual_bytes_only_after_jobs_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / 'P17B/EH1/run12345'
            (run / 'outputs').mkdir(parents=True)
            (run / 'outputs/chunk.root').write_bytes(b'root')
            preserve.atomic_json(run / preserve.MARKER_NAME,
                                 dict(kind='dyb-preserve-run', run_dir=str(run), chunk_count=16))
            with mock.patch('preserve.scheduler_jobs', return_value=[]) as jobs, \
                    mock.patch('preserve.scratch_quota', return_value={}), \
                    mock.patch('preserve.run_status', return_value=dict(state='RUNNING', running=1)) as status:
                self.assertEqual(preserve.pool_status(temporary)['reserved_bytes'], 16 * preserve.GIB)
                status.return_value = dict(state='COMPLETE', running=0)
                jobs.return_value = [dict(Iwd=str(run), JobStatus=2, RequestCpus=1)]
                self.assertEqual(preserve.pool_status(temporary)['reserved_bytes'], 16 * preserve.GIB)
                jobs.return_value = []
                self.assertEqual(preserve.pool_status(temporary)['reserved_bytes'], 4 + 16 * 1024 ** 2)

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


if __name__ == '__main__':
    unittest.main()
