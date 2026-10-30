"""Installed-loader/update regression, using only isolated profiles and fake Addins.

RBB_PACKAGE_NAME is the candidate ZIP; RBB_LEGACY_PACKAGE_NAME is the real 0.4.7
ZIP. Relative paths resolve below RBB_TEST_ROOT. No Revit assembly is executed.
"""
from contextlib import contextmanager
import copy
import ctypes
from ctypes import wintypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import types
import zipfile

import addon_utils
import bpy

sys.dont_write_bytecode = True
WORK = Path(os.environ['RBB_TEST_ROOT']).resolve()
for variable in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES',
                 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP'):
    assert Path(os.environ[variable]).resolve().is_relative_to(WORK), variable
assert WORK.drive.casefold() == 'd:'
candidate = (WORK / os.environ['RBB_PACKAGE_NAME']).resolve(strict=True)
legacy_zip = (WORK / os.environ['RBB_LEGACY_PACKAGE_NAME']).resolve(strict=True)
run = WORK / ('loader_integration_' + str(os.getpid()))
run.mkdir()
backup = run / 'backups'
backup.mkdir()
report_path = run / 'result.json'
report = dict(status='running', blender=bpy.app.version_string, candidate=str(candidate),
              candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(), legacy_zip=str(legacy_zip))


@contextmanager
def read_locked(path):
    """Windows loaded-library equivalent: readers allowed, writes/deletes denied."""
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        assert api.CloseHandle(handle), 'Failed to release own test loader handle'


def synthetic_future(package, version='0.5.1'):
    old_version = package.manifest['version']

    def remap(name):
        parts = name.split('/')
        if parts[:2] == ['runtime', 'revit']:
            assert len(parts) == 5 and parts[3] == old_version, name
            parts[3] = version
        return '/'.join(parts)

    files = {remap(name): data for name, data in package.files.items()}
    manifest = copy.deepcopy(package.manifest)
    manifest['version'] = version
    manifest['worker'] = remap(manifest['worker'])
    manifest['workers'] = {key: remap(path) for key, path in manifest['workers'].items()}
    manifest['runtime_files'] = [remap(path) for path in manifest.get('runtime_files', [])]
    for worker in manifest['workers'].values():
        # This PE overlay is only a changed-byte fixture. Never execute or ship it.
        files[worker] += b'\nAGR_LOADER_INTEGRATION_SYNTHETIC_NOT_EXECUTABLE_TEST\n'
    init, changed = re.subn(r'([\"\']version[\"\']\s*:\s*)\(\d+,\s*\d+,\s*\d+\)',
                            lambda match: match.group(1) + repr(tuple(map(int, version.split('.')))),
                            files['__init__.py'].decode('utf-8'))
    assert changed == 1
    files['__init__.py'] = init.encode('utf-8')
    files['VERSION'] = (version + '\n').encode('utf-8')
    manifest['files'] = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    assert files['core/loader_payload.py'] == package.files['core/loader_payload.py']
    assert manifest['loader_payloads'] == package.manifest['loader_payloads']
    output = run / ('SYNTHETIC-ONLY-agr_revit_bridge-' + version + '.zip')
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr('xivgate_revit_bridge/' + name, data)
        archive.writestr('xivgate_revit_bridge/PACKAGE_MANIFEST.json', json.dumps(manifest).encode('utf-8'))
    return output


module = None
legacy_name = 'xivgate_revit_bridge.core._legacy047_loader_test'
package_transaction = registration_transaction = None
try:
    assert bpy.ops.preferences.addon_install(filepath=str(candidate), overwrite=True) == {'FINISHED'}
    addon_utils.modules_refresh()
    module = addon_utils.enable('xivgate_revit_bridge', default_set=False, persistent=False)
    assert module is not None and tuple(module.bl_info['version']) == (0, 5, 0)
    installed = Path(module.__file__).resolve().parent
    assert installed.is_relative_to(Path(os.environ['BLENDER_USER_SCRIPTS']).resolve())
    updates = importlib.import_module('xivgate_revit_bridge.core.updates')
    loader = importlib.import_module('xivgate_revit_bridge.core.loader')
    installer = importlib.import_module('xivgate_revit_bridge.core.installer')
    package = updates.validate_archive(candidate, '0.5.0', bpy.app.version[:3])
    original_control = (installed / 'PACKAGE_MANIFEST.json').read_bytes()
    with zipfile.ZipFile(legacy_zip) as archive:
        legacy_source = archive.read('xivgate_revit_bridge/core/updates.py')
        assert archive.read('xivgate_revit_bridge/VERSION').decode('utf-8').strip() == '0.4.7'
    legacy = types.ModuleType(legacy_name)
    legacy.__file__ = str(legacy_zip) + '!/xivgate_revit_bridge/core/updates.py'
    legacy.__package__ = 'xivgate_revit_bridge.core'
    sys.modules[legacy_name] = legacy  # dataclass inspects its defining module during exec.
    exec(compile(legacy_source, legacy.__file__, 'exec'), legacy.__dict__)
    old_client_package = legacy.validate_archive(candidate, '0.5.0', bpy.app.version[:3])
    assert old_client_package.files['core/loader_payload.py'] == package.files['core/loader_payload.py']
    report['actual_047_client_accepts_literal_loader_payload'] = True

    paths, identities = {}, {}
    expected = package.manifest['loader_payloads']['sha256']
    assert set(expected) == set(loader.KEYS) == set(package.manifest['workers'])
    for key in sorted(expected):
        path = loader.ensure_loader(installed, key)
        assert path is not None and hashlib.sha256(path.read_bytes()).hexdigest() == expected[key]
        paths[key] = path
        identities[key] = (path.stat().st_mtime_ns, path.stat().st_ino, expected[key])
    entry = paths['2027']
    assert loader.ensure_loader(installed, '2027') == entry
    assert (entry.stat().st_mtime_ns, entry.stat().st_ino) == identities['2027'][:2]
    old_worker = installed / package.manifest['workers']['2027']
    old_worker_hash = hashlib.sha256(old_worker.read_bytes()).hexdigest()
    assert loader.launch_environment(installed, '2027', '0.5.0') == {
        'XIVGATE_RBB_WORKER_VERSION': '0.5.0', 'XIVGATE_RBB_WORKER_SHA256': old_worker_hash}

    fake_appdata = run / 'FakeAppData'
    plan = installer.manifest_plan('', entry, appdata_root=fake_appdata)
    manifest_path = installer.install_manifest(plan)
    assert manifest_path.is_relative_to(run)
    registration_bytes = manifest_path.read_bytes()
    registration_mtime = manifest_path.stat().st_mtime_ns
    assert installer.manifest_matches(plan)
    assert updates.connect_registration(plan, installed, backup) == manifest_path
    assert manifest_path.stat().st_mtime_ns == registration_mtime
    future_zip = synthetic_future(package)
    future = updates.validate_archive(future_zip, '0.5.1', bpy.app.version[:3])
    expected_new_worker = future.manifest['files'][future.manifest['workers']['2027']]
    assert expected_new_worker != old_worker_hash

    with read_locked(entry):
        package_transaction = updates.install_package(future, installed, backup)
        # Match the updater's activation boundary: an explicit later setup action acquires its
        # own OS lock. Rollback safely reacquires it if needed; never recursively acquire it.
        package_transaction.release_lock()
        assert (installed / 'VERSION').read_text().strip() == '0.5.1'
        assert hashlib.sha256(old_worker.read_bytes()).hexdigest() == old_worker_hash
        for key, path in paths.items():
            assert loader.ensure_loader(installed, key) == path
            assert (path.stat().st_mtime_ns, path.stat().st_ino,
                    hashlib.sha256(path.read_bytes()).hexdigest()) == identities[key]
        assert loader.launch_environment(installed, '2027', '0.5.1') == {
            'XIVGATE_RBB_WORKER_VERSION': '0.5.1', 'XIVGATE_RBB_WORKER_SHA256': expected_new_worker}
        assert installer.manifest_matches(plan)
        assert updates.connect_registration(plan, installed, backup) == manifest_path
        assert manifest_path.read_bytes() == registration_bytes and manifest_path.stat().st_mtime_ns == registration_mtime
        report['matching_pointer_needs_no_manifest_update'] = True

        # Exercise registration rollback separately, only below FakeAppData. This deliberately
        # staged worker pointer is never loaded and is restored before package rollback.
        temporary_plan = installer.manifest_plan('', installed / future.manifest['workers']['2027'], appdata_root=fake_appdata)
        registration_transaction = updates.repoint_registration(temporary_plan, registration_bytes.decode('utf-8'), backup, installed)
        assert manifest_path.read_bytes() == temporary_plan['content'].encode('utf-8')
        registration_transaction.rollback()
        registration_transaction = None
        assert manifest_path.read_bytes() == registration_bytes
        package_transaction.rollback()
        package_transaction = None
        assert (installed / 'VERSION').read_text().strip() == '0.5.0'
        assert (installed / 'PACKAGE_MANIFEST.json').read_bytes() == original_control
        assert all((installed / name).read_bytes() == data for name, data in package.files.items())
        future_runtime = set(future.manifest['workers'].values()) | set(future.manifest['runtime_files'])
        assert not any((installed / name).exists() for name in future_runtime)
        assert loader.launch_environment(installed, '2027', '0.5.0')['XIVGATE_RBB_WORKER_SHA256'] == old_worker_hash
        assert installer.manifest_matches(plan)
        for key, path in paths.items():
            assert (path.stat().st_mtime_ns, path.stat().st_ino,
                    hashlib.sha256(path.read_bytes()).hexdigest()) == identities[key]
    report.update(status='passed', all_six_loaders_materialized=True, loaders_immutable_while_locked=True,
                  future_version_pinned=True, loader_payload_unchanged=True,
                  package_and_registration_rollback=True, real_profile_untouched=True,
                  no_revit_execution=True, loader_sha256=expected, old_worker_sha256=old_worker_hash,
                  synthetic_new_worker_sha256=expected_new_worker, installed=str(installed),
                  loaders={key: str(path) for key, path in paths.items()})
except Exception as error:
    report.update(status='failed', error=repr(error))
    raise
finally:
    if registration_transaction is not None:
        registration_transaction.rollback()
    if package_transaction is not None:
        package_transaction.rollback()
    sys.modules.pop(legacy_name, None)
    if module is not None:
        addon_utils.disable('xivgate_revit_bridge', default_set=False)
    assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
    assert hashlib.sha256(candidate.read_bytes()).hexdigest() == report['candidate_sha256']
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('AGR_LOADER_INTEGRATION ' + json.dumps(report, ensure_ascii=False), flush=True)
