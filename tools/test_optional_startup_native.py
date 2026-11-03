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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--registration', required=True)
    parser.add_argument('--revit', required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve(strict=True)
    assert root.drive.lower() == 'd:'
    source = Path(args.source).resolve(strict=True)
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
    process = WorkerProcess()
    report = {'source_sha256': before, 'directory': str(session.directory), 'worker_sha256': digest}

    def wait_response(limit=240):
        deadline = time.monotonic() + limit
        previous = None
        while time.monotonic() < deadline:
            status = session.read_status()
            if status and (status.get('state'), status.get('message')) != previous:
                previous = status.get('state'), status.get('message')
                print(json.dumps({'state': previous[0], 'message': previous[1]}, ensure_ascii=False), flush=True)
                assert previous[0] != 'requires_action', 'Unexpected modal dialog; inspect worker.log'
            response = session.read_response(session.request_id)
            if response:
                assert response['success'], response.get('error')
                return response
            assert process.poll() is None, 'Native process exited before response'
            time.sleep(.25)
        raise TimeoutError('Native smoke timed out')

    started = time.monotonic()
    try:
        session.request('scan', str(source), options={'load_links': False})
        process.start(args.revit, session.directory, session.token, {
            'XIVGATE_RBB_SESSION_ID': session.session_id,
            'XIVGATE_RBB_WORKER_VERSION': args.version,
            'XIVGATE_RBB_WORKER_SHA256': digest,
        })
        report['pid'] = process.pid
        scan = wait_response()
        report['scan_seconds'] = time.monotonic() - started
        catalog = read_json(scan['catalog_path'])
        rows = catalog['elements']
        ids = [str(row['id']) for row in rows if row.get('bbox_min') is not None
               and row.get('recommendation') != 'exclude'][:32]
        assert ids, 'Fixture must contain geometric elements'
        report['catalog_elements'] = len(rows)
        report['selection_count'] = len(ids)
        session.request('export', selection_ids=ids, options={'detail': 'Fine', 'materials': True, 'textures': True})
        exported = wait_response()
        assert sum('dynamo_player_startup_failed' in warning for warning in exported.get('warnings', [])) == 1
        report['manifest'] = exported['manifest_path']
        report['warning_records'] = read_json(session.directory / 'warnings.json')
        log = (session.directory / 'worker.log').read_text(encoding='utf-8-sig')
        assert 'optional_addin_failure_closed' in log, 'Expected real Dynamo failure was not exercised'
        assert log.count('optional_addin_failure_closed') == 1
        assert sum(r['id'] == 'dynamo_player_startup_failed' for r in report['warning_records']['records']) == 1
        assert source_sha256(source) == before, 'Original RVT changed'
        report['success'] = True
    finally:
        if process.pid and process.poll() is None:
            try:
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
