"""Opt-in native startup replay using an already registered, frozen loader.

Does not modify registration, trust, Revit settings, or the input RVT.
Stages only the explicitly supplied versioned worker beside that loader.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xivgate_revit_bridge.core.jobs import JobSession, atomic_write_json, read_json, source_sha256
from xivgate_revit_bridge.core.launcher import WorkerProcess


def select_smoke_ids(rows, limit=32, categories=()):
    """Choose renderable model categories, never the first datum/curve rows."""
    requested = {name.casefold() for name in categories}
    candidates = [row for row in rows if row.get('bbox_min') is not None
                  and (not requested or row.get('category_name', '').casefold() in requested)]
    preferred = [row for row in candidates if row.get('recommendation') == 'include']
    if not preferred:
        # The synthetic native fixture consists of Generic Model DirectShapes.
        generic = {'generic models', 'обобщенные модели', 'обобщённые модели'}
        preferred = [row for row in candidates if row.get('category_name', '').casefold() in generic]
    return [str(row['id']) for row in preferred[:limit]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--registration', required=True)
    parser.add_argument('--revit', required=True)
    parser.add_argument('--timeout', type=float, default=240)
    parser.add_argument('--expect-dynamo-failure', action='store_true')
    parser.add_argument('--no-reuse-prepared', action='store_true')
    parser.add_argument('--discard-temporary', action='store_true')
    parser.add_argument('--overwrite-fixture', action='store_true')
    parser.add_argument('--reference-probe', action='store_true', help='Use only with the test-only native reference-probe DLL')
    parser.add_argument('--category', action='append', default=[])
    parser.add_argument('--limit', type=int, default=32)
    args = parser.parse_args()
    root = Path(args.workspace).resolve(strict=True)
    assert root.drive.lower() == 'd:'
    source = Path(args.source).resolve(strict=True)
    if args.overwrite_fixture and not source.is_relative_to(root / 'fixtures'):
        raise ValueError('Source replacement tests require a disposable RVT inside this workspace/fixtures')
    before = source_sha256(source)
    loader = Path(ET.parse(args.registration).getroot().findtext('AddIn/Assembly')).resolve(strict=True)
    key_root = loader.parents[2]
    assert key_root.name == '2027' and loader.parent.name == '1'
    lock = json.loads((Path(__file__).resolve().parents[1] / 'revit_loader/LOADER_SHA256.json').read_text())
    assert source_sha256(loader) == lock['2027']
    worker_bytes = Path(args.worker).read_bytes()
    digest = hashlib.sha256(worker_bytes).hexdigest()
    target = key_root / args.version / 'XIVgate.RevitBridge.Worker.dll'
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        assert target.read_bytes() == worker_bytes, 'Never overwrite an existing worker version'
    else:
        with target.open('xb') as stream:
            stream.write(worker_bytes)
    session = JobSession.create(root)
    if args.reference_probe:
        (session.directory / 'reference_probe_enabled').write_text('test-only', encoding='utf-8')
    process = WorkerProcess()
    report = {'source_sha256': before, 'directory': str(session.directory), 'worker_sha256': digest}

    def wait_response(limit=None):
        if limit is None:
            limit = args.timeout
        deadline = time.monotonic() + limit
        previous = None
        while time.monotonic() < deadline:
            status = session.read_status()
            if status and (status.get('state'), status.get('message')) != previous:
                previous = status.get('state'), status.get('message')
                print(json.dumps({'state': previous[0], 'message': previous[1]}, ensure_ascii=False), flush=True)
                if previous[0] == 'requires_action':
                    report['blocked_dialog'] = status
                    raise AssertionError('Unexpected modal dialog; inspect worker.log')
            response = session.read_response(session.request_id)
            if response:
                if args.reference_probe:
                    report['reference_probe'] = response
                assert response['success'], response.get('error')
                return response
            assert process.poll() is None, 'Native process exited before response'
            time.sleep(.25)
        raise TimeoutError('Native smoke timed out')

    started = time.monotonic()
    try:
        session.request('scan', str(source), options={'load_links': False, 'reuse_prepared_model': not args.no_reuse_prepared, 'keep_temporary_data': not args.discard_temporary, 'overwrite_source': args.overwrite_fixture})
        process.start(args.revit, session.directory, session.token, {
            'XIVGATE_RBB_SESSION_ID': session.session_id,
            'XIVGATE_RBB_WORKER_VERSION': args.version,
            'XIVGATE_RBB_WORKER_SHA256': digest,
        })
        report['pid'] = process.pid
        scan = wait_response()
        report['scan_seconds'] = time.monotonic() - started
        if args.reference_probe:
            assert scan['rewritten'] > 0 and scan['verified_streams'] > 0, scan
            assert scan['native_seconds'] < 15, scan
            assert source_sha256(source) == before
            report['success'] = True
            deadline = time.monotonic() + 15
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(.25)
            print('NATIVE_REFERENCE_PROBE_PASS', json.dumps(scan, ensure_ascii=False), flush=True)
            return
        catalog = read_json(scan['catalog_path'])
        rows = catalog['elements']
        ids = select_smoke_ids(rows, args.limit, args.category)
        assert ids, 'Fixture must contain geometric elements'
        report['catalog_elements'] = len(rows)
        report['selection_count'] = len(ids)
        report['selected_ids'] = ids
        del rows, catalog
        session.request('export', selection_ids=ids, options={'detail_level': 'fine', 'materials': True, 'textures': True})
        exported = wait_response()
        if args.expect_dynamo_failure:
            assert sum('dynamo_player_startup_failed' in warning for warning in exported.get('warnings', [])) == 1
        report['manifest'] = exported['manifest_path']
        report['warning_records'] = read_json(session.directory / 'warnings.json')
        log = (session.directory / 'worker.log').read_text(encoding='utf-8-sig')
        if args.expect_dynamo_failure:
            assert log.count('optional_addin_failure_closed') == 1, 'Expected real Dynamo failure was not exercised once'
            assert sum(r['id'] == 'dynamo_player_startup_failed' for r in report['warning_records']['records']) == 1
        if not args.overwrite_fixture:
            assert source_sha256(source) == before, 'Original RVT changed'
        else:
            manifest = read_json(exported['manifest_path'])
            assert manifest['source']['source_replaced'] and manifest['source']['sha256'] == source_sha256(source)
            report['source_replaced_verified'] = True
        report['success'] = True
    finally:
        if process.pid and process.poll() is None:
            try:
                if not args.reference_probe:
                    session.request('shutdown')
                    wait_response(20)
                    deadline = time.monotonic() + 15
                    while process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.25)
            finally:
                process.terminate()
        report['source_unchanged'] = source_sha256(source) == before
        atomic_write_json(root / 'native-result.json', report)
    print('NATIVE_OPTIONAL_STARTUP_PASS', json.dumps({
        key: value for key, value in report.items() if key != 'warning_records'
    }, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
