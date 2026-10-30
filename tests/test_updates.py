import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from xivgate_revit_bridge.core import updates
from xivgate_revit_bridge.core.installer import manifest_plan


def digest(value):
    return hashlib.sha256(value).hexdigest()


def release(version='0.3.0', data=b'zip'):
    name = f'agr_revit_bridge-{version}.zip'
    return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
            'published_at': '2026-10-30T10:00:00Z',
            'html_url': f'https://github.com/{updates.REPOSITORY}/releases/tag/v{version}',
            'assets': [{'name': name, 'state': 'uploaded', 'size': len(data),
                        'digest': 'sha256:' + digest(data),
                        'browser_download_url': f'https://github.com/{updates.REPOSITORY}/releases/download/v{version}/{name}'}]}


def package_bytes(version='0.3.0', changes=None):
    files = {'__init__.py': f'bl_info = {{"version": {tuple(map(int, version.split(".")))}}}\n'.encode(),
             'VERSION': (version + '\n').encode(), 'core/example.py': b'value = 3\n',
             f'runtime/revit/2027/{version}/XIVgate.RevitBridge.Worker.dll': b'worker-' + version.encode()}
    files.update(changes or {})
    manifest = {'schema': 1, 'id': updates.PACKAGE_ID, 'version': version, 'blender_min': [4, 2, 0],
                'worker': f'runtime/revit/2027/{version}/XIVgate.RevitBridge.Worker.dll',
                'files': {name: digest(value) for name, value in files.items()}}
    files['PACKAGE_MANIFEST.json'] = json.dumps(manifest).encode()
    return files


def multi_worker_bytes(version='0.3.1'):
    files = package_bytes(version)
    manifest = json.loads(files.pop('PACKAGE_MANIFEST.json'))
    manifest['workers'] = {}
    for key in ('2024', '2025', '2025-net10', '2026', '2026-net10', '2027'):
        worker = f'runtime/revit/{key}/{version}/XIVgate.RevitBridge.Worker.dll'
        manifest['workers'][key] = worker
        files[worker] = ('worker-' + key + '-' + version).encode()
    manifest['runtime_files'] = []
    for name in updates._NET48_DEPENDENCIES:
        dependency = f'runtime/revit/2024/{version}/{name}'
        manifest['runtime_files'].append(dependency)
        files[dependency] = ('dependency-' + name).encode()
    manifest['files'] = {name: digest(value) for name, value in files.items()}
    files['PACKAGE_MANIFEST.json'] = json.dumps(manifest).encode()
    return files


class Response(io.BytesIO):
    def __init__(self, value, url='https://api.github.com'):
        super().__init__(value)
        self.url = url

    def geturl(self):
        return self.url


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='updates-', dir=os.environ['XIVGATE_TEST_ROOT'])
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def archive(self, files=None):
        target = self.root / 'package.zip'
        with zipfile.ZipFile(target, 'w') as output:
            for name, data in (files or package_bytes()).items():
                output.writestr(updates.PACKAGE_ID + '/' + name, data)
        return target

    def installed(self):
        root = self.root / updates.PACKAGE_ID
        root.mkdir()
        for name, data in package_bytes('0.2.0').items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (root / 'preferences.json').write_text('user setting')
        backup = self.root / 'backups'
        backup.mkdir()
        return root, backup

    def test_latest_release_and_no_downgrade(self):
        value = updates.release_info(release(), '0.2.0')
        self.assertEqual(value['version'], '0.3.0')
        self.assertTrue(value['available'])
        self.assertFalse(updates.release_info(release('0.1.0'), '0.2.0')['available'])
        self.assertFalse(updates.release_info(release('0.2.0'), '0.2.0')['available'])

    def test_release_catalog_sorted_unique_channels_and_explicit_downgrade(self):
        early = release('0.3.1')
        early['prerelease'] = True
        payload = [release('0.2.0'), early, release('0.3.0'), early, release('0.10.0')]
        catalog = updates.release_catalog(payload, '0.3.0')
        self.assertEqual([item['version'] for item in catalog], ['0.10.0', '0.3.1', '0.3.0', '0.2.0'])
        self.assertEqual(catalog[1]['channel'], 'early')
        self.assertEqual(catalog[0]['channel'], 'stable')
        self.assertFalse(catalog[2]['installable'])
        self.assertTrue(catalog[3]['installable'])
        self.assertFalse(catalog[3]['available'])
        with patch.object(updates, '_open_url', return_value=Response(b'zip')):
            target = updates.download_release(catalog[3], self.root)
        self.assertEqual(target.read_bytes(), b'zip')
        with self.assertRaises(updates.UpdateError):
            updates.release_info(early, '0.3.0')

    def test_release_catalog_skips_invalid_assets_in_every_channel(self):
        malformed = []
        for prerelease in (False, True):
            for mutate in (lambda item: item.update(draft=True),
                           lambda item: item.update(tag_name='v0.3.1-early'),
                           lambda item: item['assets'][0].update(digest='sha256:wrong'),
                           lambda item: item['assets'][0].update(browser_download_url='https://example.com/file.zip')):
                item = release('0.3.1')
                item['prerelease'] = prerelease
                mutate(item)
                malformed.append(item)
        self.assertEqual(updates.release_catalog(malformed + [release()], '0.2.0')[0]['version'], '0.3.0')
        self.assertEqual(len(updates.release_catalog(malformed + [release()], '0.2.0')), 1)
        with self.assertRaises(updates.UpdateError):
            updates.release_catalog({'message': 'bad response'}, '0.2.0')

    def test_fetch_releases_bounded_pagination_and_offline_failures(self):
        page = [release('0.3.0') for _ in range(100)]
        with patch.object(updates, '_open_url', side_effect=[Response(json.dumps(page).encode()) for _ in range(3)]) as opened:
            catalog = updates.fetch_releases('0.2.0')
        self.assertEqual(len(catalog), 1)
        self.assertEqual(opened.call_count, 3)
        self.assertTrue(opened.call_args_list[0].args[0].full_url.endswith('/releases?per_page=100&page=1'))
        self.assertTrue(opened.call_args_list[2].args[0].full_url.endswith('page=3'))
        with patch.object(updates, '_open_url', return_value=Response(b'[]')) as opened:
            self.assertEqual(updates.fetch_releases('0.2.0'), [])
            self.assertEqual(opened.call_count, 1)
        for error in (urllib.error.URLError('offline'), urllib.error.HTTPError('url', 404, 'missing', {}, None)):
            with patch.object(updates, '_open_url', side_effect=error):
                with self.assertRaises(updates.UpdateError):
                    updates.fetch_releases('0.2.0')
        with patch.object(updates, '_open_url', return_value=Response(b'x' * (updates.MAX_METADATA_BYTES + 1))):
            with self.assertRaises(updates.UpdateError):
                updates.fetch_releases('0.2.0')

    def test_multi_worker_package_validation_and_install_rollback(self):
        files = multi_worker_bytes()
        package = updates.validate_archive(self.archive(files), '0.3.1')
        self.assertEqual(len(package.manifest['workers']), 6)
        root, backup = self.installed()
        transaction = updates.install_package(package, root, backup)
        for worker in package.manifest['workers'].values():
            self.assertEqual((root / worker).read_bytes(), files[worker])
        self.assertTrue((root / 'runtime/revit/2027/0.2.0/XIVgate.RevitBridge.Worker.dll').is_file())
        transaction.rollback()
        for worker in package.manifest['workers'].values():
            self.assertFalse((root / worker).exists())
        self.assertEqual((root / 'VERSION').read_text().strip(), '0.2.0')

    def test_multi_worker_paths_and_all_dlls_are_immutable(self):
        files = multi_worker_bytes()
        package = updates.validate_archive(self.archive(files), '0.3.1')
        root, backup = self.installed()
        for worker in package.manifest['workers'].values():
            target = root / worker
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'conflicting worker')
            with self.subTest(worker=worker), self.assertRaises(updates.UpdateError):
                updates.install_package(package, root, backup)
            self.assertEqual(target.read_bytes(), b'conflicting worker')
            target.unlink()
        for key, value in [('2023', 'runtime/revit/2023/0.3.1/XIVgate.RevitBridge.Worker.dll'),
                           ('2026', 'runtime/revit/2026/0.2.0/XIVgate.RevitBridge.Worker.dll'),
                           ('2027', 'runtime/revit/2027/0.3.1/Unexpected.dll')]:
            files = multi_worker_bytes()
            manifest = json.loads(files['PACKAGE_MANIFEST.json'])
            manifest['workers'][key] = value
            files['PACKAGE_MANIFEST.json'] = json.dumps(manifest).encode()
            with self.subTest(key=key), self.assertRaises(updates.UpdateError):
                updates.validate_archive(self.archive(files), '0.3.1')

    def test_runtime_dependencies_are_explicit_checked_and_immutable(self):
        files = multi_worker_bytes()
        dependency = 'runtime/revit/2024/0.3.1/System.Text.Json.dll'
        files[dependency] = b'approved Microsoft dependency'
        manifest = json.loads(files['PACKAGE_MANIFEST.json'])
        manifest['files'][dependency] = digest(files[dependency])
        manifest['runtime_files'] = list(manifest['workers'].values()) + manifest['runtime_files']
        files['PACKAGE_MANIFEST.json'] = json.dumps(manifest).encode()
        package = updates.validate_archive(self.archive(files), '0.3.1')
        root, backup = self.installed()
        target = root / dependency
        target.parent.mkdir(parents=True)
        target.write_bytes(b'different existing dependency')
        with self.assertRaises(updates.UpdateError):
            updates.install_package(package, root, backup)
        self.assertEqual(target.read_bytes(), b'different existing dependency')
        target.unlink()
        transaction = updates.install_package(package, root, backup)
        self.assertEqual(target.read_bytes(), files[dependency])
        transaction.rollback()
        self.assertFalse(target.exists())
        for bad in ['runtime/revit/2024/0.3.1/RevitAPI.dll',
                    'runtime/revit/2024/0.3.1/Unknown.dll',
                    'runtime/revit/2024/0.3.0/System.Text.Json.dll',
                    'runtime/revit/2027/0.3.1/System.Text.Json.dll']:
            altered = dict(files)
            value = json.loads(files['PACKAGE_MANIFEST.json'])
            value['runtime_files'].append(bad)
            value['files'][bad] = digest(b'bad dependency')
            altered[bad] = b'bad dependency'
            altered['PACKAGE_MANIFEST.json'] = json.dumps(value).encode()
            with self.subTest(path=bad), self.assertRaises(updates.UpdateError):
                updates.validate_archive(self.archive(altered), '0.3.1')

    def test_net48_worker_requires_every_runtime_dependency(self):
        for name in updates._NET48_DEPENDENCIES:
            files = multi_worker_bytes()
            missing = f'runtime/revit/2024/0.3.1/{name}'
            files.pop(missing)
            manifest = json.loads(files['PACKAGE_MANIFEST.json'])
            manifest['runtime_files'].remove(missing)
            manifest['files'].pop(missing)
            files['PACKAGE_MANIFEST.json'] = json.dumps(manifest).encode()
            with self.subTest(name=name), self.assertRaises(updates.UpdateError):
                updates.validate_archive(self.archive(files), '0.3.1')

    def test_release_rejects_foreign_malformed_and_unverified_assets(self):
        for mutate in [lambda p: p.update(draft=True), lambda p: p.update(prerelease=True),
                       lambda p: p.update(published_at=None), lambda p: p.update(tag_name='v0.3.0-beta'),
                       lambda p: p['assets'][0].update(digest='sha256:abc'),
                       lambda p: p['assets'][0].update(size=updates.MAX_ARCHIVE_BYTES + 1),
                       lambda p: p['assets'][0].update(browser_download_url='https://github.com/foreign/repo/releases/download/v0.3.0/agr_revit_bridge-0.3.0.zip')]:
            payload = release()
            mutate(payload)
            with self.subTest(payload=payload), self.assertRaises(updates.UpdateError):
                updates.release_info(payload, '0.2.0')

    def test_fetch_offline_private_and_metadata_bounds(self):
        with patch.object(updates, '_open_url', return_value=Response(json.dumps(release()).encode())):
            self.assertTrue(updates.fetch_release('0.2.0')['available'])
        for error in [urllib.error.URLError('offline'),
                      urllib.error.HTTPError('url', 404, 'missing', {}, None),
                      urllib.error.HTTPError('url', 403, 'rate limit', {}, None)]:
            with patch.object(updates, '_open_url', side_effect=error):
                with self.assertRaises(updates.UpdateError):
                    updates.fetch_release('0.2.0')
        with patch.object(updates, '_open_url', return_value=Response(b'x' * (updates.MAX_METADATA_BYTES + 1))):
            with self.assertRaises(updates.UpdateError):
                updates.fetch_release('0.2.0')

    def test_download_hash_size_and_partial_cleanup(self):
        info = updates.release_info(release(data=b'valid zip'), '0.2.0')
        with patch.object(updates, '_open_url', return_value=Response(b'valid zip')):
            downloaded = updates.download_release(info, self.root)
        self.assertEqual(downloaded.read_bytes(), b'valid zip')
        downloaded.unlink()
        for data in [b'bad', b'valid zip-extra']:
            with patch.object(updates, '_open_url', return_value=Response(data)):
                with self.assertRaises(updates.UpdateError):
                    updates.download_release(info, self.root)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_redirects_are_https_and_trusted_hosts_only(self):
        handler = updates._SafeRedirect(asset=True)
        request = __import__('urllib.request', fromlist=['Request']).Request('https://github.com')
        for url in ['http://github.com/file', 'https://github.com.evil.test/file', 'https://evil.test/file',
                    'https://user@github.com/file']:
            with self.subTest(url=url), self.assertRaises(updates.UpdateError):
                handler.redirect_request(request, None, 302, '', {}, url)

    def test_valid_archive(self):
        package = updates.validate_archive(self.archive(), '0.3.0', (4, 2, 0))
        self.assertEqual(package.manifest['id'], updates.PACKAGE_ID)
        self.assertIn('core/example.py', package.files)
        with self.assertRaises(updates.UpdateError):
            updates.validate_archive(self.archive(), '0.3.0', (4, 1, 0))

    def test_archive_wrong_identity_version_hash_or_python(self):
        for alter in [lambda files: files.update(VERSION=b'0.9.0'),
                      lambda files: files.update({'__init__.py': b'bl_info={"version": (9, 0, 0)}'}),
                      lambda files: files.update({'core/example.py': b'broken('}),
                      lambda files: files.update({'PACKAGE_MANIFEST.json': files['PACKAGE_MANIFEST.json'].replace(b'"xivgate_revit_bridge"', b'"foreign"')}),
                      lambda files: files.update({'extra.py': b'1'})]:
            files = package_bytes()
            alter(files)
            with self.subTest(files=files), self.assertRaises(updates.UpdateError):
                updates.validate_archive(self.archive(files), '0.3.0')

    def test_archive_traversal_case_aliases_symlink_and_foreign_dll(self):
        for name in ['../escape.py', 'a\\escape.py', 'CORE/example.py', 'foreign.dll']:
            files = package_bytes()
            files[name] = b'evil'
            with self.subTest(name=name), self.assertRaises(updates.UpdateError):
                updates.validate_archive(self.archive(files), '0.3.0')
        target = self.archive()
        with zipfile.ZipFile(target, 'a') as output:
            entry = zipfile.ZipInfo(updates.PACKAGE_ID + '/link')
            entry.create_system = 3
            entry.external_attr = (0o120777 << 16)
            output.writestr(entry, '../target')
        with self.assertRaises(updates.UpdateError):
            updates.validate_archive(target, '0.3.0')

    def test_archive_bounds_python_version_and_matching_hashes(self):
        for changes in [{'__init__.py': b'bl_info={"version": (0, 9, 0)}\n'},
                        {'core/example.py': b'invalid python ('},
                        {'VERSION': b'9.0.0\n'}]:
            with self.subTest(changes=changes), self.assertRaises(updates.UpdateError):
                updates.validate_archive(self.archive(package_bytes(changes=changes)), '0.3.0')
        target = self.archive()
        with patch.object(updates, 'MAX_UNPACKED_BYTES', 100):
            with self.assertRaises(updates.UpdateError):
                updates.validate_archive(target, '0.3.0')
        files = package_bytes()
        manifest = json.loads(files['PACKAGE_MANIFEST.json'])
        manifest['version'] = '0.4.0'
        files['PACKAGE_MANIFEST.json'] = json.dumps(manifest).encode()
        with self.assertRaises(updates.UpdateError):
            updates.validate_archive(self.archive(files), '0.3.0')
        target.write_bytes(b'corrupt')
        with self.assertRaises(updates.UpdateError):
            updates.validate_archive(target, '0.3.0')

    def test_install_commit_preserves_unmanaged_and_old_dll(self):
        root, backup = self.installed()
        old = root / 'runtime/revit/2027/0.2.0/XIVgate.RevitBridge.Worker.dll'
        package = updates.validate_archive(self.archive(), '0.3.0')
        original_replace = updates.os.replace

        def no_old_dll(source, destination):
            self.assertNotEqual(Path(destination), old)
            return original_replace(source, destination)

        with patch.object(updates.os, 'replace', side_effect=no_old_dll):
            transaction = updates.install_package(package, root, backup)
        self.assertEqual(old.read_bytes(), b'worker-0.2.0')
        self.assertEqual((root / 'VERSION').read_text().strip(), '0.3.0')
        self.assertEqual((root / 'preferences.json').read_text(), 'user setting')
        transaction.commit()
        self.assertEqual(list(backup.iterdir()), [])

    def test_install_rollback_and_mid_install_failure(self):
        root, backup = self.installed()
        package = updates.validate_archive(self.archive(), '0.3.0')
        transaction = updates.install_package(package, root, backup)
        transaction.rollback()
        self.assertEqual((root / 'VERSION').read_text().strip(), '0.2.0')
        original = updates._atomic_file
        failed = False

        def fail_once(path, data, **kwargs):
            nonlocal failed
            if Path(path).name == '__init__.py' and not failed:
                failed = True
                raise PermissionError('locked')
            return original(path, data, **kwargs)

        with patch.object(updates, '_atomic_file', side_effect=fail_once):
            with self.assertRaises(updates.UpdateError):
                updates.install_package(package, root, backup)
        self.assertEqual((root / 'VERSION').read_text().strip(), '0.2.0')
        self.assertEqual((root / 'preferences.json').read_text(), 'user setting')

    def test_install_lock_blocks_another_process_until_commit_or_rollback(self):
        root, backup = self.installed()
        package = updates.validate_archive(self.archive(), '0.3.0')
        program = ('from pathlib import Path; import sys; '
                   'from xivgate_revit_bridge.core.updates import _InstallLock, UpdateError; '
                   '\ntry:\n lock = _InstallLock.acquire(Path(sys.argv[1]))\n'
                   'except UpdateError:\n sys.exit(9)\n'
                   'lock.release()\n')

        def child():
            return subprocess.run([sys.executable, '-B', '-c', program, str(root)],
                                  capture_output=True, env=dict(os.environ), timeout=15)

        for finish in ('commit', 'rollback'):
            with self.subTest(finish=finish):
                transaction = updates.install_package(package, root, backup)
                blocked = child()
                self.assertEqual(blocked.returncode, 9, blocked.stderr.decode(errors='replace'))
                with self.assertRaisesRegex(updates.UpdateError, 'обновля'):
                    updates.install_package(package, root, backup)
                getattr(transaction, finish)()
                acquired = child()
                self.assertEqual(acquired.returncode, 0, acquired.stderr.decode(errors='replace'))

    def test_install_lock_releases_on_cleanup_failure_and_failed_rollback(self):
        root, backup = self.installed()
        package = updates.validate_archive(self.archive(), '0.3.0')
        transaction = updates.install_package(package, root, backup)
        with patch.object(transaction, '_cleanup', side_effect=PermissionError('cleanup locked')):
            with self.assertRaises(PermissionError):
                transaction.commit()
        lock = updates._InstallLock.acquire(root)
        lock.release()
        self.assertEqual((root / 'VERSION').read_text().strip(), '0.3.0')
        transaction = updates.install_package(package, root, backup)
        (root / '__init__.py').write_bytes(b'user change during update')
        with self.assertRaises(updates.UpdateError):
            transaction.rollback()
        self.assertTrue(transaction.directory.exists())
        lock = updates._InstallLock.acquire(root)
        lock.release()

    def test_install_failure_before_snapshot_releases_lock(self):
        root, backup = self.installed()
        package = updates.validate_archive(self.archive(), '0.3.0')
        (root / 'PACKAGE_MANIFEST.json').write_text('broken json')
        with self.assertRaises(updates.UpdateError):
            updates.install_package(package, root, backup)
        lock = updates._InstallLock.acquire(root)
        lock.release()

    def test_install_refuses_source_checkout_or_different_existing_worker(self):
        root, backup = self.installed()
        package = updates.validate_archive(self.archive(), '0.3.0')
        (root / '.git').write_text('source checkout')
        with self.assertRaises(updates.UpdateError):
            updates.install_package(package, root, backup)
        (root / '.git').unlink()
        worker = root / package.manifest['worker']
        worker.parent.mkdir(parents=True)
        worker.write_bytes(b'different')
        with self.assertRaises(updates.UpdateError):
            updates.install_package(package, root, backup)
        self.assertEqual(worker.read_bytes(), b'different')

    def test_install_guards_symlinks_and_preserves_locally_modified_stale_files(self):
        root, backup = self.installed()
        package = updates.validate_archive(self.archive(), '0.3.0')
        old_control = root / 'PACKAGE_MANIFEST.json'
        old_manifest = json.loads(old_control.read_text())
        stale = root / 'local.py'
        stale.write_bytes(b'user edit')
        old_manifest['files']['local.py'] = digest(b'original')
        old_control.write_text(json.dumps(old_manifest))
        transaction = updates.install_package(package, root, backup)
        self.assertEqual(stale.read_bytes(), b'user edit')
        transaction.rollback()
        other = self.root / 'external'
        other.mkdir()
        link = root / 'link'
        try:
            link.symlink_to(other, target_is_directory=True)
        except OSError:
            return  # Windows may deny creating symbolic links in a standard session.
        with self.assertRaises(updates.UpdateError):
            updates._target(root, 'link/escape.py')

    def registration(self):
        root, backup = self.installed()
        old = root / 'runtime/revit/2027/0.2.0/XIVgate.RevitBridge.Worker.dll'
        new = root / 'runtime/revit/2027/0.3.0/XIVgate.RevitBridge.Worker.dll'
        new.parent.mkdir(parents=True)
        new.write_bytes(b'new')
        old_plan = manifest_plan('', str(old), appdata_root=self.root)
        new_plan = manifest_plan('', str(new), appdata_root=self.root)
        target = Path(old_plan['path'])
        target.parent.mkdir(parents=True)
        target.write_text(old_plan['content'])
        return root, backup, old_plan, new_plan

    def test_repoint_registration_rolls_back_and_refuses_foreign(self):
        root, backup, old, new = self.registration()
        transaction = updates.repoint_registration(new, old['content'], backup, root)
        self.assertEqual(Path(new['path']).read_text(), new['content'])
        transaction.rollback()
        self.assertEqual(Path(old['path']).read_text(), old['content'])
        Path(old['path']).write_text('foreign')
        with self.assertRaises(updates.UpdateError):
            updates.repoint_registration(new, old['content'], backup, root)
        self.assertEqual(Path(old['path']).read_text(), 'foreign')

    def test_repoint_foreign_race_preserves_foreign_and_backup(self):
        root, backup, old, new = self.registration()
        target = Path(old['path'])
        original_link = updates.os.link

        def race(source, destination):
            if Path(destination) == target and not target.exists():
                target.write_text('racing foreign')
            return original_link(source, destination)

        with patch.object(updates.os, 'link', side_effect=race):
            with self.assertRaises(updates.UpdateError):
                updates.repoint_registration(new, old['content'], backup, root)
        self.assertEqual(target.read_text(), 'racing foreign')
        self.assertTrue(any(backup.iterdir()))

    def test_repoint_racing_initial_move_restores_unexpected_content(self):
        root, backup, old, new = self.registration()
        target = Path(old['path'])
        original_rename = updates.os.rename

        def race(source, destination):
            if Path(source) == target:
                target.write_text('foreign replaced before rename')
            return original_rename(source, destination)

        with patch.object(updates.os, 'rename', side_effect=race):
            with self.assertRaises(updates.UpdateError):
                updates.repoint_registration(new, old['content'], backup, root)
        self.assertEqual(target.read_text(), 'foreign replaced before rename')
        self.assertEqual(list(backup.iterdir()), [])

    def test_repoint_rejects_external_worker_and_commit_retains_new_pointer(self):
        root, backup, old, new = self.registration()
        external = self.root / 'foreign.dll'
        external.write_bytes(b'external')
        outside = manifest_plan('', str(external), appdata_root=self.root)
        with self.assertRaises(updates.UpdateError):
            updates.repoint_registration(outside, old['content'], backup, root)
        transaction = updates.repoint_registration(new, old['content'], backup, root)
        transaction.commit()
        self.assertEqual(Path(new['path']).read_text(), new['content'])
        self.assertEqual(list(backup.iterdir()), [])

    def test_connect_registration_migrates_own_unversioned_worker(self):
        root, backup, old, new = self.registration()
        unversioned = root / 'runtime/revit/2027/XIVgate.RevitBridge.Worker.dll'
        unversioned.write_bytes(b'legacy worker')
        legacy = manifest_plan('', str(unversioned), appdata_root=self.root)
        target = Path(legacy['path'])
        target.write_text(legacy['content'])
        self.assertEqual(updates.connect_registration(new, root, backup), target)
        self.assertEqual(target.read_text(), new['content'])
        self.assertEqual(unversioned.read_bytes(), b'legacy worker')
        self.assertEqual(list(backup.iterdir()), [])
        self.assertEqual(updates.connect_registration(new, root, backup), target)

    def test_connect_registration_refuses_foreign_or_external_old_pointer(self):
        root, backup, old, new = self.registration()
        target = Path(old['path'])
        external = self.root / 'External.dll'
        external.write_bytes(b'foreign binary')
        external_plan = manifest_plan('', str(external), appdata_root=self.root)
        for prior in ['foreign registration', external_plan['content']]:
            with self.subTest(prior=prior):
                target.write_text(prior)
                with self.assertRaises(updates.UpdateError):
                    updates.connect_registration(new, root, backup)
                self.assertEqual(target.read_text(), prior)

    def test_connect_registration_in_other_process_respects_install_lock(self):
        root, backup, old, new = self.registration()
        program = ('import json,sys; from pathlib import Path; '
                   'from xivgate_revit_bridge.core.updates import connect_registration,UpdateError; '
                   '\ntry:\n connect_registration(json.loads(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))\n'
                   'except UpdateError:\n sys.exit(9)\n')
        lock = updates._InstallLock.acquire(root)
        try:
            result = subprocess.run([sys.executable, '-B', '-c', program, json.dumps(new), str(root), str(backup)],
                                    capture_output=True, env=dict(os.environ), timeout=15)
            self.assertEqual(result.returncode, 9, result.stderr.decode(errors='replace'))
            self.assertEqual(Path(old['path']).read_text(), old['content'])
        finally:
            lock.release()

    def test_connect_keeps_valid_new_registration_when_cleanup_fails(self):
        root, backup, old, new = self.registration()
        with patch.object(updates.RegistrationTransaction, '_cleanup', side_effect=PermissionError('locked snapshot')):
            with self.assertWarns(RuntimeWarning):
                target = updates.connect_registration(new, root, backup)
        self.assertEqual(target.read_text(), new['content'])
        self.assertTrue(any(backup.iterdir()))
        lock = updates._InstallLock.acquire(root)
        lock.release()


if __name__ == '__main__':
    unittest.main()
