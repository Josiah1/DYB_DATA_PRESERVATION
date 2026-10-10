import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import preserve


class ContentHoldTests(unittest.TestCase):
    def test_inspection_requires_exact_chunk_and_clean_root_proof_and_defers_changed_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = '/dybfs/rec/P17B/rec/runs_0051000/runs_0051200/run_0051255/recon.Neutrino.0051255.Physics.EH1-Merged.P17B-I._0001.root'
            catalog = root / 'run51255.list'
            catalog.write_text(original + '\n')
            with mock.patch('preserve.MINI_ROOT', str(root / 'mini')):
                mini = preserve.resolve_inputs([original])[0]
                Path(mini).parent.mkdir(parents=True)
                Path(mini).write_bytes(b'root' + b'fixture')
                marker = preserve.prepare_run(str(catalog), 'P17B', 'EH1', str(root / 'stage'),
                                              str(root / 'code'), 1, require_registered=False)
                run = Path(marker['run_dir'])
                (run / 'status/000000.fail').write_text('rc=1\n')
                error = run / 'logs/000000.err'
                error.write_text('ERROR missing required tree: ' + mini + '\n')
                issue = dict(path=mini, bytes=9, keys=[dict(name='AdEnergy', classname='TNtuple', entries=0)],
                             required={'Event/Rec/AdSimpleNL': False, 'Event/Data/CalibStats': False},
                             observed_at='2026-10-10T00:00:00+00:00')
                response = subprocess.CompletedProcess([], 0, json.dumps([issue]), '')
                with mock.patch('preserve.run_command', return_value=response) as probe:
                    proof = preserve.confirmed_empty_mini_errors(run, root / 'stage')
                    self.assertEqual(probe.call_args.args[0][-1], mini)
                    self.assertEqual(proof['issues'][0]['catalog_entry'], original)
                    self.assertEqual(proof['issues'][0]['status'], 'pending')
                    self.assertEqual(proof['issues'][0]['category'], 'content_error')
                with mock.patch('preserve.run_command', return_value=mock.Mock(stdout=response.stdout, stderr='Input/output error')):
                    with self.assertRaisesRegex(RuntimeError, 'ROOT inspection reported errors'):
                        preserve.confirmed_empty_mini_errors(run, root / 'stage')
                with mock.patch('preserve.run_command', return_value=mock.Mock(stdout=json.dumps([dict(issue, path=original)]), stderr='')):
                    with self.assertRaisesRegex(ValueError, 'not confirmed'):
                        preserve.confirmed_empty_mini_errors(run, root / 'stage')
                error.write_text('ERROR missing required tree: ' + original + '\n')
                with mock.patch('preserve.run_command') as probe:
                    with self.assertRaisesRegex(ValueError, 'fixed mini chunk'):
                        preserve.confirmed_empty_mini_errors(run, root / 'stage')
                    probe.assert_not_called()
                error.write_text('ERROR missing required tree: ' + mini + '\n')
                before = preserve.run_status(run, root / 'stage')
                changed = dict(before, failure_updates={'000000': [99, 99]})
                with mock.patch('preserve.run_command', return_value=response), \
                        mock.patch('preserve.run_status', side_effect=[before, changed]):
                    self.assertEqual(preserve.confirmed_empty_mini_errors(run, root / 'stage'),
                                     dict(recheck=True, failure_updates=changed['failure_updates']))
                error.write_text('ERROR cannot read input: I/O error\n')
                with self.assertRaisesRegex(RuntimeError, 'unrecognized worker failure'):
                    preserve.confirmed_empty_mini_errors(run, root / 'stage')

    def test_unaffected_admission_transfer_and_pending_accounting_with_bounded_rechecks(self):
        for first_proof in ('changed_during_inspection', 'differs_from_pool', 'stable', 'unknown_error', 'changes_then_unknown', 'never_settles'):
            with self.subTest(first_proof=first_proof), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                local = root / 'data'
                local.mkdir()
                (root / 'provenance').mkdir()
                records = [dict(period='P17B', hall='EH1', stem='run'+str(n), catalog='/catalog/run{}.list'.format(n),
                                catalog_sha256=str(n), input_count=2) for n in (10001, 10002, 10003)]
                staged = {}
                for record in records:
                    backup = root / 'catalogs/P17B/EH1' / (record['stem']+'.list')
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_text('/tiny/input1.root\n/tiny/input2.root\n')
                def stage(record, state):
                    marker = dict(record, run_dir='/stage/'+record['stem'], code_root='/code', chunk_count=2,
                                  files_per_job=1, selection=preserve.selection_record('on'), input_source='mini',
                                  skip_missing_minis=True)
                    return dict(marker=marker, intent=False, submissions=[], status=dict(state=state,
                                running=0, failed=int(state=='FAILED'), ok=1 if state=='FAILED' else 2,
                                chunks=2, failure_updates={'000000': [1, 1]} if state=='FAILED' else {}))
                staged['run10001'] = stage(records[0], 'FAILED')
                staged['run10002'] = stage(records[1], 'COMPLETE')
                held_before = copy.deepcopy(staged['run10001'])
                args = preserve.build_parser().parse_args(['run', '--local-root', str(local), '--mount-root', str(root),
                    '--remote-stage-root', '/stage', '--remote-code-root', '/code', '--files-per-job', '1',
                    '--skip-missing-minis', '--ad-selection', 'on', '--delete-remote-after-sync', '--hold-empty-minis'])
                args.poll_seconds = 0.001
                inspected, submitted, prepared, verified, cleaned = [], [], [], set(), []
                issue = dict(path='/mini/empty.root', period='P17B', hall='EH1', run='run10001', catalog_index=0,
                             catalog_entry='/tiny/input1.root', observed_at='2026-10-10T00:00:00+00:00',
                             category='content_error', status='pending')
                def remote(alias, code, arguments):
                    cmd = arguments[0]
                    if cmd == '_pool':
                        # A confirmed content hold may coexist with the run's other worker finishing.
                        jobs = [dict(Iwd='/stage/run10001', JobStatus=2, RequestCpus=1)] if not inspected else []
                        return copy.deepcopy(dict(runs=list(staged.values()), jobs=jobs, queue=preserve.queue_counts(jobs),
                            reserved_bytes=3*preserve.GIB, output_bytes=10,
                            quota=dict(free_bytes=500*preserve.GIB, used_files=10, limit_files=200000))), 0
                    if cmd == '_prepare':
                        self.assertEqual(arguments[arguments.index('--catalog')+1], records[2]['catalog'])
                        prepared.append('run10003');staged['run10003'] = stage(records[2], 'PENDING')
                        return staged['run10003']['marker'], 0
                    stem = Path(arguments[arguments.index('--run-dir')+1]).name
                    if cmd == '_content_errors':
                        inspected.append(stem)
                        if first_proof == 'unknown_error' or first_proof=='changes_then_unknown' and len(inspected)>1:
                            raise RuntimeError('unrecognized worker failure: I/O error')
                        if first_proof == 'never_settles' or len(inspected)==1 and first_proof in ('changed_during_inspection','changes_then_unknown'):
                            return dict(recheck=True, failure_updates={'000000': [2, 2]}), 0
                        if len(inspected)==1 and first_proof=='differs_from_pool':
                            return dict(issues=[issue], failure_updates={'000000': [2, 2]}), 0
                        return dict(issues=[issue], failure_updates={'000000': [1, 1]}), 0
                    self.assertNotEqual(stem, 'run10001', 'never submit/finalize/clean the content hold')
                    if cmd == '_submit':
                        submitted.append(stem);staged[stem]['status'].update(state='COMPLETE')
                        return dict(cluster_id=3), 0
                    if cmd == '_finalize':return staged[stem]['marker'], 0
                    self.assertEqual(cmd, '_cleanup');self.assertIn(stem, verified)
                    cleaned.append(stem);del staged[stem];return dict(deleted=stem), 0
                def sync(alias, marker, *unused):
                    verified.add(marker['stem']);return dict(files=1, bytes=10, directory=marker['run_dir'])
                expected = 'unrecognized worker failure' if first_proof in ('unknown_error','changes_then_unknown') else '8 rechecks' if first_proof=='never_settles' else 'only held incomplete runs remain'
                with mock.patch('preserve.CATALOG_BACKUP_ROOT', root/'catalogs'), \
                        mock.patch('preserve.remote_json', side_effect=remote), \
                        mock.patch('preserve.archive_progress', return_value=({}, [])), \
                        mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                        mock.patch('preserve.validate_local_mount'), mock.patch('preserve.sync_run', side_effect=sync):
                    with self.assertRaisesRegex(RuntimeError, expected):preserve.run_pipeline(args, records, {}, local)
                self.assertEqual(staged['run10001'], held_before)
                state = preserve.load_json(root/'provenance/controller.json')
                if first_proof in ('unknown_error','changes_then_unknown'):
                    if first_proof=='unknown_error':self.assertFalse(prepared)
                    self.assertIn('I/O error', state['error']);continue
                self.assertEqual(submitted, ['run10003'])
                self.assertEqual(set(cleaned), {'run10002', 'run10003'})
                self.assertTrue(state['new_run_admission'])
                if first_proof=='never_settles':
                    self.assertEqual(len(inspected), 9);continue
                self.assertEqual(len(inspected), 1 if first_proof=='stable' else 2)
                ledger = preserve.load_json(root/'provenance/pending-input-issues.json')
                self.assertEqual(ledger['issues'], [issue]);self.assertTrue(ledger['held_runs'][0]['automatic'])
                self.assertEqual(ledger['held_runs'][0]['pending_input_count'], 2)
                preserve.atomic_json(local/'catalog-inventory.json', dict(records=records))
                progress, saved = preserve.archive_progress(local)
                self.assertEqual((progress['expected_inputs'], progress['pending_inputs'], progress['skipped_inputs']), (6, 6, 0))

    @unittest.skipUnless(shutil.which('root') and importlib.util.find_spec('ROOT'),
                         'PyROOT runtime required for the real inspection helper')
    def test_real_root_helper_accepts_only_empty_adenergy_and_preserves_inputs(self):
        import ROOT
        ROOT.PyConfig.DisableRootLogon = True
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = root/'empty.root';file = ROOT.TFile(str(empty), 'RECREATE')
            tree = ROOT.TNtuple('AdEnergy', 'empty', 'energy');tree.Write();file.Close()
            other = root/'other.root';file = ROOT.TFile(str(other), 'RECREATE')
            ROOT.TNamed('other', 'not the supported content signature').Write();file.Close()
            nonempty = root/'nonempty.root';file = ROOT.TFile(str(nonempty), 'RECREATE')
            tree = ROOT.TNtuple('AdEnergy', 'populated', 'energy');tree.Fill(1.0);tree.Write();file.Close()
            damaged = root/'damaged.root';damaged.write_bytes(b'root-broken')
            before = preserve.sha256_file(empty)
            helper = Path(__file__).resolve().parent.parent/'scripts/check_empty_mini.sh'
            p = subprocess.run(['bash', str(helper), str(empty)], capture_output=True, text=True)
            self.assertEqual((p.returncode,p.stderr), (0,''));self.assertEqual(json.loads(p.stdout)[0]['path'],str(empty))
            self.assertEqual(preserve.sha256_file(empty),before)
            for path in (other,nonempty,damaged,root/'missing.root'):
                with self.subTest(path=path):
                    p = subprocess.run(['bash',str(helper),str(path)],capture_output=True,text=True)
                    self.assertNotEqual(p.returncode,0)


if __name__ == '__main__':
    unittest.main()
