import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import preserve


class BrokenMiniTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patch = mock.patch('preserve.MINI_ROOT', str(self.root/'mini'))
        patch.start();self.addCleanup(patch.stop)
        self.originals = ['/dybfs/rec/P17B/rec/runs_0051000/runs_0051200/run_0051237/'
                          'recon.Neutrino.0051237.Physics.EH1-Merged.P17B-I._{:04d}.root'.format(i)
                          for i in range(8)]
        self.minis = preserve.resolve_inputs(self.originals)
        for i, path in enumerate(self.minis):
            if i == 1:continue
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b'root tiny fixture')
        catalog = self.root/'run51237.list'
        catalog.write_text('\n'.join(self.originals)+'\n')
        verifier=self.root/'code/bin/slim-root';verifier.parent.mkdir(parents=True)
        verifier.write_text('#!/bin/sh\nexit 0\n');verifier.chmod(0o700)
        with mock.patch('preserve.time.sleep'):
            self.marker = preserve.prepare_run(str(catalog), 'P17B', 'EH1', str(self.root/'stage'),
                str(self.root/'code'), 2, require_registered=False, skip_missing_minis=True,
                ad_selection='on')
        self.run = Path(self.marker['run_dir'])
        backup=self.root/'catalogs/P17B/EH1/run51237.list';backup.parent.mkdir(parents=True)
        backup.write_bytes(catalog.read_bytes())
        patch=mock.patch('preserve.CATALOG_BACKUP_ROOT', self.root/'catalogs')
        patch.start();self.addCleanup(patch.stop)
        patch=mock.patch('preserve.catalog_backup_digests',return_value={'P17B/EH1/run51237.list':self.marker['catalog_sha256']})
        patch.start();self.addCleanup(patch.stop)
        self.prefix = preserve.output_prefix(self.marker)
        self.broken = {self.minis[i] for i in (3, 5, 6)}
        for index in (0, 3):self.good(index)
        for index in (1, 2):self.fail(index)

    def proof(self, path):
        return dict(path=path, bytes=Path(path).stat().st_size, sha256=preserve.sha256_file(path),
                    keys=[dict(name='AdEnergy', classname='TNtuple', entries=0)],
                    required={'Event/Rec/AdSimpleNL': False, 'Event/Data/CalibStats': False},
                    readable=True, zombie=False, recovered=False, observed_at='2026-10-11T00:00:00+00:00')

    def probe(self, command):
        paths = command[3:] if command[2:3] == ['--scan'] else command[2:]
        return subprocess.CompletedProcess(command, 0, json.dumps([self.proof(p) for p in paths if p in self.broken]), '')

    def good(self, index):
        paths = (self.run/'chunks'/'{:06d}.list'.format(index)).read_text().splitlines()
        rows = [[str(i), p, '1', '8', '1', '0', '1', '0', '0', '0.01', '0.01', '1']+['0']*8
                for i, p in enumerate(paths)]
        (self.run/'reports'/'{}.slim.{:06d}.sources.tsv'.format(self.prefix,index)).write_text(
            '\t'.join(preserve.SOURCE_REPORT_COLUMNS)+'\n'+''.join('\t'.join(r)+'\n' for r in rows))
        (self.run/'outputs'/'{}.slim.{:06d}.root'.format(self.prefix,index)).write_bytes(b'root tiny output')
        (self.run/'status'/'{:06d}.ok'.format(index)).write_text('RESULT sources={} entries={} kept_entries={}\n'.format(len(paths),len(paths),len(paths)))

    def fail(self, index):
        path = next(p for p in (self.run/'chunks'/'{:06d}.list'.format(index)).read_text().splitlines() if p in self.broken)
        (self.run/'status'/'{:06d}.fail'.format(index)).write_text('rc=1\n')
        (self.run/'logs'/'{:06d}.err'.format(index)).write_text('ERROR missing required tree: '+path+'\n')

    def recover(self):
        with mock.patch('preserve.scheduler_jobs', return_value=[]), mock.patch('preserve.run_command', side_effect=self.probe):
            return preserve.skip_broken_minis(self.run, self.root/'stage')

    def test_stable_chunks_empty_chunk_checksums_and_separate_exports(self):
        saved = {p:p.read_bytes() for directory in ('chunks','outputs','reports','status')
                 for p in (self.run/directory).iterdir() if any(s in p.name for s in ('000000','000003'))}
        catalog = (self.run/'catalog.list').read_bytes()
        new = self.recover()['marker']
        self.assertEqual(preserve.recorded_chunks(self.originals,new),
                         [[self.minis[0],self.minis[2]],[self.minis[4]],[],[self.minis[7]]])
        self.assertEqual((new['input_count'],new['processed_input_count'],new['missing_skipped_input_count'],new['broken_skipped_input_count']), (8,4,1,3))
        self.assertEqual((self.run/'catalog.list').read_bytes(),catalog)
        self.assertEqual({p:p.read_bytes() for p in saved},saved)
        self.assertEqual(len(new['broken_failure_evidence']),2)
        self.assertFalse(list((self.run/'outputs').glob('*000002.root')))
        self.good(1)
        with mock.patch('preserve.verify_remote_root') as verify:
            final = preserve.finalize_run(self.run,self.root/'stage')
        self.assertEqual(verify.call_count,3)
        result = preserve.verify_local_run(self.run)
        self.assertEqual((result['processed_inputs'],result['missing_skipped_inputs'],result['broken_skipped_inputs']), (4,1,3))
        self.assertFalse(result['catalog_complete'])
        rows = (self.run/'durations.tsv').read_text().splitlines()[1:]
        self.assertEqual([r.split('\t')[1] for r in rows],['0','0','1','3'])
        bad = rows[-1].split('\t');bad[1]='1'
        (self.run/'durations.tsv').write_text('\t'.join(preserve.DURATION_COLUMNS)+'\n'+'\n'.join(rows[:-1]+['\t'.join(bad)])+'\n')
        with self.assertRaisesRegex(ValueError,'chunk layout'):preserve.verify_local_run(self.run)
        (self.run/'durations.tsv').write_text('\t'.join(preserve.DURATION_COLUMNS)+'\n'+'\n'.join(rows)+'\n')
        local=self.root/'archive/data';destination=local/'P17B/EH1/run51237'
        destination.parent.mkdir(parents=True);shutil.move(str(self.run),str(destination))
        preserve.atomic_json(local/'catalog-inventory.json',dict(records=[self.marker]))
        progress=preserve.refresh_input_issues(local)
        self.assertEqual((progress['pending_inputs'],progress['missing_skipped_inputs'],progress['broken_skipped_inputs']), (0,1,3))
        provenance=local.parent/'provenance'
        self.assertEqual(set((provenance/'broken_files.list').read_text().splitlines()),self.broken)
        self.assertEqual(len(preserve.load_json(provenance/'broken-input-issues.json')['issues']),3)
        self.assertNotIn(self.minis[3], (provenance/'INPUT_ISSUES.md').read_text())
        historical=dict(current_input_policy='hold pending user decision',decision_needed='old unanswered question',
                        held_run=dict(run='P17B/EH1/run51237',evidence={'stderr':'original error'}),
                        scope_samples=[dict(path=self.minis[3],bytes=17)])
        issue=dict(self.proof(self.minis[3]),period='P17B',hall='EH1',run='run51237',catalog_index=3,
                   catalog_sha256=self.marker['catalog_sha256'])
        preserve.atomic_json(provenance/'pending-input-issues.json',dict(historical,issues=[issue],held_runs=[]))
        preserve.refresh_pending_holds(local,set(),{}, {},True,skip_broken_minis=True)
        pending=preserve.load_json(provenance/'pending-input-issues.json')
        self.assertEqual(pending['historical_hold_evidence'],historical)
        self.assertNotIn('decision_needed',pending);self.assertNotIn('held_run',pending);self.assertNotIn('scope_samples',pending)
        self.assertEqual(pending['held_runs'],[])
        self.assertEqual(pending['issues'][0]['disposition'],'broken_skipped')
        self.assertIn('broken gaps separately',pending['current_input_policy'])
        gap=preserve.load_json(destination/'input-gaps.json');gap['broken_skipped_input_count']=2
        preserve.atomic_json(destination/'input-gaps.json',gap)
        with self.assertRaisesRegex(ValueError,'gap report'):preserve.verify_local_run(destination)

    def test_recovery_never_changes_writers_good_outputs_or_reuses_changed_source_lists(self):
        before=(self.run/preserve.MARKER_NAME).read_bytes()
        with mock.patch('preserve.scheduler_jobs',return_value=[dict(Iwd=str(self.run))]):
            with self.assertRaisesRegex(RuntimeError,'writers'):preserve.skip_broken_minis(self.run,self.root/'stage')
        self.assertEqual((self.run/preserve.MARKER_NAME).read_bytes(),before)
        self.good(1)
        with self.assertRaisesRegex(ValueError,'verified OK marker|saved output'):self.recover()
        self.assertEqual((self.run/preserve.MARKER_NAME).read_bytes(),before)
        (self.run/'broken-recovery.json').unlink()
        (self.run/'status/000001.ok').unlink()
        (self.run/'outputs'/(self.prefix+'.slim.000001.root')).unlink()
        old_report=self.run/'reports'/(self.prefix+'.slim.000001.sources.tsv')
        old_report.unlink()
        new=self.recover()['marker']
        self.good(1)
        old_report.write_text(old_report.read_text().replace(self.minis[4], self.minis[3]))
        with self.assertRaisesRegex(ValueError,'fixed catalog'):preserve.check_chunk_sources(self.run,new,1)

    def test_recovery_journal_resumes_without_duplicate_gaps_or_shifting_good_chunks(self):
        atomic=preserve.atomic_json
        def interrupted(path,value):
            if path==self.run/preserve.MARKER_NAME:raise OSError('simulated interruption')
            return atomic(path,value)
        with mock.patch('preserve.atomic_json',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'interruption'):self.recover()
        self.assertTrue((self.run/'broken-recovery.json').exists())
        new=self.recover()['marker']
        self.assertFalse((self.run/'broken-recovery.json').exists())
        self.assertEqual((new['broken_skipped_input_count'],len(new['broken_failure_evidence'])),(3,2))
        self.assertEqual(preserve.recorded_chunks(self.originals,new)[3],[self.minis[7]])

    def test_category_proof_catalog_and_count_trust_boundaries(self):
        new=self.recover()['marker']
        def mutate(check_key,value):
            changed=copy.deepcopy(new)
            next(i for i in changed['skipped_inputs'] if i.get('category')=='broken')['mini_check'][check_key]=value
            return changed
        variants=[mutate('sha256','invalid'),mutate('recovered',True),mutate('zombie',True),mutate('readable',False),
                  mutate('keys',[]),mutate('path',self.originals[3]),mutate('status','missing'),
                  dict(new,broken_skipped_input_count=2),dict(new,skip_broken_minis=False),
                  dict(new,processed_input_count=5),dict(new,input_source='recon')]
        for record in variants:
            with self.subTest(record=record):
                with self.assertRaises(ValueError):preserve.recorded_inputs(self.originals,record)
        unknown=copy.deepcopy(new);unknown['skipped_inputs'][0]['category']='io_error'
        with self.assertRaisesRegex(ValueError,'unknown'):preserve.recorded_inputs(self.originals,unknown)

    def test_changing_failure_snapshot_defers_broken_mutation_without_discarding_evidence(self):
        before=preserve.run_status(self.run,self.root/'stage')
        changed=dict(before,failure_updates={'000001':[2,2],'000002':[2,2]})
        proof=dict(issues=[dict(path=self.minis[3]),dict(path=self.minis[5])],failure_updates=before['failure_updates'])
        marker=(self.run/preserve.MARKER_NAME).read_bytes()
        with mock.patch('preserve.confirmed_empty_mini_errors',return_value=proof), \
             mock.patch('preserve.run_status',side_effect=[before,before,changed]):
            result=self.recover()
        self.assertTrue(result['recheck'])
        self.assertEqual((self.run/preserve.MARKER_NAME).read_bytes(),marker)
        self.assertFalse((self.run/'broken-recovery.json').exists())
        self.assertEqual(len(list((self.run/'status').glob('*.fail'))),2)

    def test_zero_usable_inputs_has_only_proved_gaps_and_no_invented_root_data(self):
        for index in (0,3):
            for path in (self.run/'outputs').glob('*{:06d}.root'.format(index)):path.unlink()
            for path in (self.run/'reports').glob('*{:06d}.sources.tsv'.format(index)):path.unlink()
            (self.run/'status'/'{:06d}.ok'.format(index)).unlink()
        self.broken=set(self.minis)-{self.minis[1]}
        for index in range(4):self.fail(index)
        new=self.recover()['marker']
        self.assertEqual(preserve.run_status(self.run,self.root/'stage')['state'],'COMPLETE')
        with mock.patch('preserve.verify_remote_root') as verify:
            result=preserve.finalize_run(self.run,self.root/'stage')
        verify.assert_not_called()
        self.assertEqual((result['output_count'],result['processed_input_count'],result['broken_skipped_input_count']),(0,0,7))
        self.assertEqual((self.run/'durations.tsv').read_text(),'\t'.join(preserve.DURATION_COLUMNS)+'\n')
        checked=preserve.verify_local_run(self.run)
        self.assertEqual((checked['processed_inputs'],checked['skipped_inputs'],checked['expected_inputs']),(0,8,8))

    def test_pipeline_waits_for_writers_then_recovers_and_retries_without_holds_or_lost_transfers(self):
        for unknown in (False,True):
            with self.subTest(unknown=unknown):
                local=self.root/('unknown' if unknown else 'local')/'data';local.mkdir(parents=True)
                (local.parent/'provenance').mkdir()
                record=dict(self.marker)
                other=dict(record,stem='run51238',run_number=51238,run_dir=str(self.run.with_name('run51238')))
                stages={m['stem']:dict(marker=m,status=dict(state=state,running=0,failed=int(state=='FAILED'),
                           ok=0,chunks=4,failure_updates={'000001':[1,1]}),submissions=[dict(submitted_at=preserve.utc_now())],intent=False)
                        for m,state in ((record,'FAILED'),(other,'COMPLETE'))}
                calls=[];verified=set();polls=0
                def remote(alias,code,arguments):
                    nonlocal polls
                    cmd=arguments[0];calls.append(cmd)
                    if cmd=='_pool':
                        polls+=1
                        jobs=[dict(Iwd=str(self.run),JobStatus=2,RequestCpus=1)] if polls==1 else []
                        return copy.deepcopy(dict(runs=list(stages.values()),jobs=jobs,queue=preserve.queue_counts(jobs),
                            quota=dict(free_bytes=500*preserve.GIB,used_files=1,limit_files=200000),
                            reserved_bytes=10*preserve.GIB,output_bytes=1)),0
                    stem=Path(arguments[arguments.index('--run-dir')+1]).name
                    if cmd=='_content_errors':return dict(issues=[{}],failure_updates={'000001':[1,1]}),0
                    if cmd=='_skip_broken':
                        if unknown:raise RuntimeError('unrecognized worker failure: I/O error')
                        self.assertGreater(polls,1)
                        m=dict(record,broken_recovery_submissions=1,missing_skipped_input_count=1,
                               broken_skipped_input_count=3,processed_input_count=4)
                        stages[stem]['marker']=m;stages[stem]['status'].update(state='PENDING',failed=0)
                        return dict(marker=m,status=stages[stem]['status']),0
                    if cmd=='_submit':
                        self.assertIn('--resubmit',arguments)
                        stages[stem]['submissions'].append(dict(submitted_at=preserve.utc_now()))
                        stages[stem]['status'].update(state='COMPLETE');return dict(cluster_id=123),0
                    if cmd=='_finalize':return stages[stem]['marker'],0
                    self.assertEqual(cmd,'_cleanup');self.assertIn(stem,verified)
                    del stages[stem];return dict(deleted=stem),0
                def sync(alias,marker,*unused):
                    verified.add(marker['stem']);return dict(files=1,bytes=1,directory=marker['run_dir'])
                args=preserve.build_parser().parse_args(['run','--local-root',str(local),'--mount-root',str(self.root),
                    '--remote-stage-root',str(self.root/'stage'),'--remote-code-root',record['code_root'],
                    '--files-per-job','2','--skip-missing-minis','--skip-broken-minis','--ad-selection','on',
                    '--max-attempts','1','--delete-remote-after-sync'])
                args.poll_seconds=0.001
                with mock.patch('preserve.remote_json',side_effect=remote), \
                     mock.patch('preserve.archive_progress',return_value=({},[])), \
                     mock.patch('preserve.CATALOG_BACKUP_ROOT',self.root), \
                     mock.patch('preserve.parse_catalog_inputs',return_value=self.originals), \
                     mock.patch('pathlib.Path.read_bytes',return_value=b''), \
                     mock.patch('preserve.recorded_inputs'), mock.patch('preserve.refresh_input_issues'), \
                     mock.patch('preserve.validate_local_mount'),mock.patch('preserve.sync_run',side_effect=sync):
                    if unknown:
                        with self.assertRaisesRegex(RuntimeError,'I/O error'):preserve.run_pipeline(args,[record,other],{},local)
                    else:preserve.run_pipeline(args,[record,other],{},local)
                state=preserve.load_json(local.parent/'provenance/controller.json')
                self.assertTrue(state['new_run_admission'])
                self.assertEqual(state['held_runs'],[])
                if not unknown:
                    self.assertEqual(verified,{'run51237','run51238'})
                    self.assertEqual(calls.count('_submit'),1)
                else:self.assertIn('I/O error',state['error'])

    @unittest.skipUnless(shutil.which('root') and importlib.util.find_spec('ROOT') and
                         (Path(os.environ.get('LCG_VIEW') or preserve.DEFAULT_LCG_VIEW)/'setup.sh').is_file(),
                         'configured IHEP LCG/PyROOT required')
    def test_real_root_proof_skip_resume_preserves_good_chunk_and_verifies_schema4(self):
        import ROOT
        from test_converter import MACRO
        code=Path(preserve.__file__).parent
        self.marker['code_root']=str(code)
        preserve.atomic_json(self.run/preserve.MARKER_NAME,self.marker)
        preserve.atomic_json(self.run/'manifest.json',self.marker)
        macro=self.root/'fixture.C';macro.write_text(MACRO)
        for i,p in enumerate(self.minis):
            if i==1:continue
            if p in self.broken:
                f=ROOT.TFile(p,'RECREATE');tree=ROOT.TNtuple('AdEnergy','empty','energy');tree.Write();f.Close()
            else:
                table=self.root/'events.tsv';table.write_text('1 0.5 1 0\n1 1.0 2 0\n5 0.0 3 0\n')
                ROOT.gROOT.ProcessLine('.L '+str(macro)) if i==0 else None
                ROOT.make_source(str(table),p)
        for directory in ('outputs','reports','status','logs'):
            for p in (self.run/directory).iterdir():p.unlink()
        def convert(index):
            out=self.run/'outputs'/'{}.slim.{:06d}.root'.format(self.prefix,index)
            report=self.run/'reports'/'{}.slim.{:06d}.sources.tsv'.format(self.prefix,index)
            result=preserve.run_command([str(code/'bin/slim-root'),'--input-list',str(self.run/'chunks'/'{:06d}.list'.format(index)),
                '--output',str(out),'--source-report',str(report),'--run-number','51237','--ad-selection','on'],check=False)
            (self.run/'logs'/'{:06d}.err'.format(index)).write_text(result.stderr)
            (self.run/'status'/'{:06d}.{}'.format(index,'fail' if result.returncode else 'ok')).write_text(
                'rc={}\n'.format(result.returncode) if result.returncode else result.stdout)
            return result.returncode
        for index in range(4):self.assertEqual(convert(index),1 if index in (1,2) else 0)
        good=self.run/'outputs'/(self.prefix+'.slim.000003.root');digest=preserve.sha256_file(good)
        with mock.patch('preserve.scheduler_jobs',return_value=[]):
            recovered=preserve.skip_broken_minis(self.run,self.root/'stage')
        self.assertEqual(recovered['marker']['broken_skipped_input_count'],3)
        self.assertEqual(convert(1),0)
        preserve.finalize_run(self.run,self.root/'stage')
        result=preserve.verify_local_run(self.run,root_verifier=code/'bin/slim-root')
        self.assertEqual((result['processed_inputs'],result['kept_entries'],result['broken_skipped_inputs']),(4,8,3))
        self.assertEqual(preserve.sha256_file(good),digest)


if __name__=='__main__':unittest.main()
