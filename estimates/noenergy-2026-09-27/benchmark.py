#!/usr/bin/env python3
"""Bounded all-event size benchmark; no production defaults or source data edits."""
import argparse
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path
import re
import resource
import socket
import subprocess
import time


def emit(kind, **values):
    print(json.dumps(dict(kind=kind, **values), sort_keys=True, allow_nan=False), flush=True)


def plan(repo):
    evidence = [json.loads(s) for s in
                (repo / 'estimates/mini-branches-2026-09-27/results.jsonl').read_text().splitlines()]
    manifest = repo / 'catalog_backups/SHA256SUMS'
    counts = collections.Counter()
    expected = set()
    for line in manifest.read_text().splitlines():
        digest, relative = line.split()
        data = (repo / 'catalog_backups' / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, relative
        period, hall, _ = relative.split('/')
        for source in data.decode().splitlines():
            assert source not in expected
            expected.add(source)
            counts['/'.join((period, hall, Path(source).name.split('.')[1]))] += 1
    samples = []
    for row in evidence:
        if row['kind'] != 'sample':
            continue
        assert row['passed'] and row['source'] in expected
        samples.append(dict(group='/'.join((row['period'], row['hall'], row['tag'])),
                            source=row['source'], mini=row['path'],
                            run=int(re.search(r'/run_(\d+)/', row['source'])[1])))
    groups = sorted(counts)
    assert len(samples) == 48 and len(expected) == 831334
    assert {s['group'] for s in samples} == set(groups)
    # One existing middle-quantile sample per stratum for paired codec tests.
    for group in groups:
        indices = [i for i, s in enumerate(samples) if s['group'] == group]
        for i in indices:
            samples[i]['alternatives'] = i == indices[len(indices) // 2]
    required = next(r['required'] for r in evidence if r['kind'] == 'environment')
    emit('plan', expected_files=len(expected), groups=dict(counts), samples=samples,
         required=required, converter_sha256=hashlib.sha256((repo / 'src/slim_root.cpp').read_bytes()).hexdigest(),
         catalog_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
         sampling='Reuse 48 archive-listed samples: up to three run quantiles per period/hall/stream; middle file of each run. Deterministic, not random.')


def cpu(child=False):
    r = resource.getrusage(resource.RUSAGE_CHILDREN if child else resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def timed(function, child=False):
    wall, used = time.monotonic(), cpu(child)
    value = function()
    return value, dict(wall_seconds=time.monotonic()-wall, cpu_seconds=cpu(child)-used)


def command(args):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=180)
    if p.returncode:
        raise RuntimeError('command failed: ' + repr(args) + '\n' + p.stdout + p.stderr)
    return p.stdout


def signatures(arrays):
    return {name: dict(dtype=str(a.dtype), entries=len(a), sha256=hashlib.sha256(a).hexdigest())
            for name, a in arrays.items()}


def root_arrays(path, required, run=None):
    import ROOT
    with ROOT.TFile.Open(str(path), 'READ') as f:
        assert f and not f.IsZombie(), path
        arrays, counts = {}, []
        for tree_path, fields in required.items():
            fields = dict(fields)
            if run is not None and tree_path.endswith('AdSimpleNL'):
                fields['runNumber'] = 'Int_t'
            tree = f.Get(tree_path)
            assert tree, tree_path
            counts.append(int(tree.GetEntries()))
            for name, dtype in fields.items():
                leaf = tree.GetLeaf(name)
                assert leaf and leaf.GetTypeName() == dtype and leaf.GetLen() == 1, name
            arrays.update(ROOT.RDataFrame(tree).AsNumpy(list(fields)))
        assert len(set(counts)) == 1 and counts[0] > 0
        if run is not None:
            assert (arrays['runNumber'] == run).all()
        return arrays


def repack_root(source, dest, settings):
    import ROOT
    with ROOT.TFile.Open(str(source), 'READ') as old, ROOT.TFile.Open(str(dest), 'RECREATE', '', settings) as new:
        assert new.GetCompressionSettings() == settings
        for tree_path in ('Event/Rec/AdSimpleNL', 'Event/Data/CalibStats', 'Preservation/Sources'):
            directory = new
            for part in tree_path.split('/')[:-1]:
                directory = directory.GetDirectory(part) or directory.mkdir(part)
            directory.cd()
            original = old.Get(tree_path)
            copied = original.CloneTree(0)
            copied.SetAutoFlush(0)
            copied.SetAutoSave(0)
            copied.SetBasketSize('*', 262144)
            for branch in copied.GetListOfBranches():
                branch.SetCompressionSettings(settings)
                assert branch.GetCompressionSettings() == settings
            assert copied.CopyEntries(original) >= 0
            assert copied.GetEntries() == original.GetEntries()
            copied.Write()
        new.GetDirectory('Preservation').cd()
        old.Get('Preservation/selection').Write()


def write_parquet(arrays, dest, metadata, codec, level):
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pa.table({name: pa.array(a, from_pandas=False) for name, a in arrays.items()})
    table = table.replace_schema_metadata({b'preservation': metadata})
    encoding = {name: 'BYTE_STREAM_SPLIT' if a.dtype.kind == 'f' else 'DELTA_BINARY_PACKED'
                for name, a in arrays.items()}
    pq.write_table(table, dest, compression=codec, compression_level=level,
                   use_dictionary=False, column_encoding=encoding, write_statistics=False,
                   row_group_size=262144, data_page_size=262144, write_page_checksum=True)


def read_parquet(path, metadata):
    import pyarrow.parquet as pq
    table = pq.read_table(path, use_threads=False, page_checksum_verification=True)
    assert table.schema.metadata[b'preservation'] == metadata
    return {name: table[name].combine_chunks().to_numpy() for name in table.column_names}


def self_check(work):
    import numpy as np
    # Include NaN payloads, negative zero, infinities and uint32 values above INT_MAX.
    arrays = dict(f=np.array([0, 0x80000000, 0x7fc12345, 0x7f800000, 0xff800000], dtype='u4').view('f4'),
                  u=np.array([0, 1, 2**31, 2**32-1, 7], dtype='u4'),
                  s=np.array([-32768, -1, 0, 1, 32767], dtype='i2'))
    changed = {k: v.copy() for k, v in arrays.items()}
    changed['f'][0] = -0.
    assert signatures(changed) != signatures(arrays)
    path = work / 'selfcheck.parquet'
    for codec in ('gzip', 'brotli'):
        write_parquet(arrays, path, b'round-trip', codec, 6)
        assert signatures(read_parquet(path, b'round-trip')) == signatures(arrays)
    path.unlink()


def run(plan_path, converter, work, limit):
    import numpy as np
    import ROOT
    ROOT.PyConfig.DisableRootLogon = True
    ROOT.gROOT.SetBatch(True)
    import pyarrow
    p = json.loads(plan_path.read_text())
    assert len(p['samples']) == 48 and 1 <= limit <= 48, 'bounded test: choose 1..48 files'
    self_check(work)
    emit('environment', utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
         host=socket.gethostname(), root=ROOT.gROOT.GetVersion(), numpy=np.__version__,
         pyarrow=pyarrow.__version__, self_check='passed', converter_version=command([converter, '--version']).strip(),
         converter_sha256=p['converter_sha256'], sample_limit=limit,
         note='One process, no energy/event selection. Baseline uses production converter; alternatives are paired encoding experiments, not deployed converters.')
    failures = 0
    for index, sample in enumerate(p['samples'][:limit]):
        emit('start', index=index, **sample)
        record = dict(index=index, **sample, passed=False)
        source = Path(sample['mini'])
        baseline, report = work / 'baseline.root', work / 'sources.tsv'
        input_list = work / 'input.list'
        try:
            before = source.stat()
            input_list.write_text(str(source)+'\n')
            output, timing = timed(lambda: command([converter, '--input-list', input_list,
                '--output', baseline, '--source-report', report, '--run-number', sample['run'],
                '--ad-selection', 'off']), child=True)
            record.update(timing, bytes=baseline.stat().st_size, source_bytes=before.st_size,
                          source_report=report.read_text(), converter_output=output)
            arrays, read_timing = timed(lambda: root_arrays(source, p['required']))
            record['source_read'] = read_timing
            record['entries'] = len(arrays['energy'])
            arrays['runNumber'] = np.full(record['entries'], sample['run'], dtype='i4')
            expected = signatures(arrays)
            assert signatures(root_arrays(baseline, p['required'], sample['run'])) == expected
            row = next(csv.DictReader(record['source_report'].splitlines(), delimiter='\t'))
            assert int(row['entries']) == int(row['keptEntries']) == record['entries']
            timestamps = arrays['triggerTimeSec'].astype('i8')*1000000000 + arrays['triggerTimeNanoSec']
            assert float(row['dataDurationSeconds']) == int(timestamps.max()-timestamps.min())/1e9
            assert (int(row['firstTriggerTimeSec'])*1000000000+int(row['firstTriggerTimeNanoSec'])) == int(timestamps.min())
            assert (int(row['lastTriggerTimeSec'])*1000000000+int(row['lastTriggerTimeNanoSec'])) == int(timestamps.max())
            record['branch_signatures'] = expected
            record['passed'] = True
            emit('baseline', **record)
            if sample['alternatives'] or limit == 1:
                metadata = json.dumps(dict(source_report=record['source_report'], required=p['required'],
                    selection='off', run=sample['run']), sort_keys=True).encode()
                variants = [('root_z7_b256', 507), ('root_z9_b256', 509), ('root_lzma7_b256', 207),
                            ('parquet_gzip6_delta_bss', ('gzip', 6)),
                            ('parquet_brotli6_delta_bss', ('brotli', 6))]
                for name, setting in variants:
                    dest = work / (name + ('.root' if name.startswith('root') else '.parquet'))
                    result = dict(index=index, group=sample['group'], variant=name, passed=False)
                    try:
                        if name.startswith('root'):
                            _, t = timed(lambda: repack_root(baseline, dest, setting))
                            command([converter, '--verify', dest])
                            decoded, read_t = timed(lambda: root_arrays(dest, p['required'], sample['run']))
                        else:
                            _, t = timed(lambda: write_parquet(arrays, dest, metadata, *setting))
                            decoded, read_t = timed(lambda: read_parquet(dest, metadata))
                        result.update(t, bytes=dest.stat().st_size, read=read_t)
                        assert signatures(decoded) == expected, 'bitwise round-trip mismatch'
                        result['passed'] = True
                        del decoded
                    except Exception as error:
                        result['error'] = repr(error)
                        failures += 1
                    emit('alternative', **result)
                    if dest.exists():
                        dest.unlink()
            after = source.stat()
            assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
            del arrays
        except Exception as error:
            failures += 1
            emit('failure', index=index, group=sample['group'], error=repr(error))
        finally:
            for path in (baseline, report, input_list):
                if path.exists():
                    path.unlink()
    emit('summary', attempted=min(limit, len(p['samples'])), failures=failures)
    raise SystemExit(bool(failures))


def summarize(plan_path, results):
    p = json.loads(plan_path.read_text())
    rows = [json.loads(s) for s in results.read_text().splitlines()]
    baseline = {r['index']: r for r in rows if r['kind'] == 'baseline' and r['passed']}
    assert len(baseline) == len(p['samples']) == 48
    assert sum(r['kind'] == 'baseline' for r in rows) == 48
    for index, sample in enumerate(p['samples']):
        assert all(baseline[index][k] == sample[k] for k in ('group', 'mini', 'source', 'run'))
    assert not any(r['kind'] == 'failure' for r in rows)
    assert rows[-1] == dict(kind='summary', attempted=48, failures=0)
    assert sum(r['kind'] == 'alternative' and r['passed'] for r in rows) == 100
    groups = {g: [r for r in baseline.values() if r['group'] == g] for g in p['groups']}
    means = {g: sum(r['bytes'] for r in rs)/len(rs) for g, rs in groups.items()}
    total = sum(p['groups'][g]*means[g] for g in groups)
    variants = collections.defaultdict(dict)
    for r in rows:
        if r['kind'] == 'alternative' and r['passed']:
            variants[r['variant']][r['group']] = r
    assert len(variants) == 5
    estimates = {}
    for name, values in variants.items():
        assert set(values) == set(groups), name
        projected = sum(p['groups'][g]*means[g]*values[g]['bytes']/baseline[values[g]['index']]['bytes'] for g in groups)
        estimates[name] = dict(bytes=projected, tb=projected/1e12, saving_fraction=1-projected/total,
            paired_files=len(values), encode_cpu_seconds=sum(r['cpu_seconds'] for r in values.values()),
            decode_cpu_seconds=sum(r['read']['cpu_seconds'] for r in values.values()))
    # Output primitives: two int16 columns and eighteen 32-bit columns = 76 B/event.
    emit('estimate', files=p['expected_files'], sample_files=len(baseline),
         sample_entries=sum(r['entries'] for r in baseline.values()),
         sample_source_bytes=sum(r['source_bytes'] for r in baseline.values()),
         sample_output_bytes=sum(r['bytes'] for r in baseline.values()),
         mini_input_tb=sum(p['groups'][g]*sum(r['source_bytes'] for r in rs)/len(rs) for g, rs in groups.items())/1e12,
         uncompressed_primitive_tb=76*sum(p['groups'][g]*sum(r['entries'] for r in rs)/len(rs) for g, rs in groups.items())/1e12,
         baseline_bytes=total, baseline_tb=total/1e12, baseline_tib=total/2**40,
         sensitivity_tb=[sum(p['groups'][g]*f(r['bytes'] for r in groups[g]) for g in groups)/1e12 for f in (min, max)],
         baseline_cpu_days=sum(p['groups'][g]*sum(r['cpu_seconds'] for r in rs)/len(rs) for g, rs in groups.items())/86400,
         groups={g: dict(files=p['groups'][g], samples=len(rs), mean_bytes=means[g]) for g, rs in groups.items()},
         alternatives=estimates,
         caveats='Deterministic stratified sample, not a statistical confidence interval. Sensitivity uses within-stratum extrema. Alternatives scale paired size ratios. Per-source output includes extra file headers versus 16-source chunks. Does not establish archive availability or TWin end-to-end equivalence.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['plan', 'run', 'summarize'])
    parser.add_argument('paths', nargs='+', type=Path)
    parser.add_argument('--limit', type=int, default=48)
    args = parser.parse_args()
    if args.mode == 'plan':
        plan(args.paths[0])
    elif args.mode == 'run':
        run(*args.paths, args.limit)
    else:
        summarize(*args.paths)
