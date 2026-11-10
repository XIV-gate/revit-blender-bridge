"""Opt-in synthetic selective-export regression through the frozen registered loader.

Creates only its own synthetic document. Never opens an input RVT or changes registration.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xivgate_revit_bridge.core.jobs import JobSession, atomic_write_json, read_json, source_sha256
from xivgate_revit_bridge.core.launcher import WorkerProcess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--registration', required=True)
    parser.add_argument('--revit', required=True)
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    root = Path(args.workspace).resolve(strict=True)
    assert root.is_dir() and root.drive.lower() == 'd:'
    loader = Path(ET.parse(args.registration).getroot().findtext('AddIn/Assembly')).resolve(strict=True)
    key_root = loader.parents[2]
    assert key_root.name == '2027' and loader.parent.name == '1'
    lock = read_json(Path(__file__).resolve().parents[1] / 'revit_loader/LOADER_SHA256.json')
    assert source_sha256(loader) == lock['2027'], 'Registered loader differs from frozen ABI1'
    assert args.version and '/' not in args.version and '\\' not in args.version and args.version not in ('.', '..')
    worker = Path(args.worker).resolve(strict=True).read_bytes()
    digest = hashlib.sha256(worker).hexdigest()
    target = key_root / args.version / 'XIVgate.RevitBridge.Worker.dll'
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        assert target.read_bytes() == worker, 'Never overwrite a different worker at an existing version'
    else:
        with target.open('xb') as stream:
            stream.write(worker)
    session = JobSession.create(root)
    atomic_write_json(session.directory / 'native_regression.json', dict(
        schema=1, session_id=session.session_id, request_id=str(uuid.uuid4()), token=session.token,
        kind='native_view_regression', run_native_fixture=True))
    process = WorkerProcess()
    result = dict(directory=str(session.directory), worker_sha256=digest, success=False)
    try:
        process.start(args.revit, session.directory, session.token, {
            'XIVGATE_RBB_SESSION_ID': session.session_id,
            'XIVGATE_RBB_WORKER_VERSION': args.version,
            'XIVGATE_RBB_WORKER_SHA256': digest,
        })
        result['pid'] = process.pid
        print(json.dumps(result), flush=True)
        deadline = time.monotonic() + args.timeout
        previous = None
        while time.monotonic() < deadline:
            status_path = session.directory / 'native_status.json'
            if status_path.exists():
                status = read_json(status_path)
                current = status.get('state'), status.get('message')
                if current != previous:
                    print(json.dumps(dict(state=current[0], message=current[1]), ensure_ascii=False), flush=True)
                    previous = current
                assert current[0] != 'requires_action', 'Native fixture requires action; no dialog was overridden'
            report_path = session.directory / 'native_report.json'
            if report_path.exists():
                report = read_json(report_path)
                if report.get('status') in ('passed', 'failed', 'error', 'startup_error'):
                    result['native_report'] = str(report_path)
                    result['status'] = report['status']
                    result['cases'] = len(report.get('cases', []))
                    assert report['status'] == 'passed', report.get('error') or 'Native geometry/rollback gate failed; inspect report'
                    assert report.get('owned_document_closed_without_saving') and report.get('exit_requested')
                    exit_deadline = time.monotonic() + 20
                    while process.poll() is None and time.monotonic() < exit_deadline:
                        time.sleep(.25)
                    assert process.poll() is not None, 'Owned fixture process did not exit normally'
                    result['success'] = True
                    print('NATIVE_SELECTIVE_EXPORT_PASS', json.dumps(result), flush=True)
                    return
            assert process.poll() is None, 'Owned native process exited before its final report'
            time.sleep(.25)
        raise TimeoutError('Native selective regression timed out')
    finally:
        # This launcher never opens an input model; only its own child process is owned.
        # Unexpected dialogs are reported without keyboard/mouse/activation automation.
        if process.poll() is None:
            process.terminate()
        atomic_write_json(root / 'native-selective-result.json', result)


if __name__ == '__main__':
    main()
