import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from xivgate_revit_bridge.core.catalog import annotate_catalog, initial_selection, selection_by_unique_id, set_group_selection
from xivgate_revit_bridge.core.jobs import JobSession, atomic_write_json, contained_path
from xivgate_revit_bridge.core.installer import manifest_plan, install_manifest, manifest_matches
from xivgate_revit_bridge.core.launcher import WorkerProcess
import xml.etree.ElementTree as ET


CATALOG = {'elements': [
    {'id': '9007199254740993', 'unique_id': 'a', 'category_id': 'walls', 'workset_id': '1', 'recommendation': 'include'},
    {'id': '2', 'unique_id': 'b', 'category_id': 'walls', 'workset_id': '2', 'recommendation': 'review'},
    {'id': '3', 'unique_id': 'c', 'category_id': 'doors', 'workset_id': '1', 'recommendation': 'exclude'}]}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='core-', dir=os.environ['XIVGATE_TEST_ROOT'])
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_selection_defaults_and_preservation(self):
        values = initial_selection(CATALOG, {'a': False, 'c': True})
        self.assertEqual(values, {'9007199254740993': False, '2': True, '3': True})
        self.assertEqual(selection_by_unique_id(CATALOG, {'2'}), {'a': False, 'b': True, 'c': False})
        self.assertEqual(set_group_selection(CATALOG, {'3'}, 'category', 'walls', True), {'9007199254740993', '2', '3'})
        self.assertEqual(set_group_selection(CATALOG, {'2', '3'}, 'workset', '1', False), {'2'})

    def test_facade_recommendations_are_conservative_and_do_not_mutate_source(self):
        catalog = {'elements': [
            {'id': '1', 'unique_id': 'facade', 'category_name': 'Стены', 'type_name': 'Фасад бетон',
             'workset_name': 'Фасады', 'function': 'Interior', 'recommendation': 'review'},
            {'id': '2', 'unique_id': 'internal', 'category_name': 'Walls', 'type_name': 'ADSK_Внутренняя 200',
             'workset_name': 'Фасады', 'recommendation': 'review'},
            {'id': '3', 'unique_id': 'window', 'category_name': 'Окна', 'type_name': 'Interior glazing',
             'workset_name': 'Внутренние', 'recommendation': 'review'},
            {'id': '4', 'unique_id': 'pipe', 'category_name': 'Трубы', 'recommendation': 'review'},
            {'id': '5', 'unique_id': 'generic', 'category_name': 'Generic Models', 'recommendation': 'review'},
            {'id': '6', 'unique_id': 'explicit', 'category_name': 'Windows', 'recommendation': 'exclude', 'reason': 'Backend'},
        ]}
        original = json.loads(json.dumps(catalog))
        annotated = annotate_catalog(catalog)
        self.assertEqual([item['recommendation'] for item in annotated['elements']],
                         ['include', 'exclude', 'include', 'exclude', 'review', 'exclude'])
        self.assertEqual(annotated['elements'][5]['reason'], 'Backend')
        self.assertEqual(catalog, original)
        selected = initial_selection(annotated, {'pipe': True})
        self.assertTrue(selected['4'])
        self.assertTrue(selected['5'])
        self.assertFalse(selected['2'])

    def test_internal_type_prefixes_and_mep_names(self):
        internal = ['Внутренняя', 'Вн_100', 'Вн-200', 'Interior 200', 'Internal partition',
                    'ADSK Interior wall', 'ADSK-Вн 100', 'ADSK_Внутренние']
        for name in internal:
            with self.subTest(type=name):
                item = {'id': '1', 'category_name': 'Стены', 'type_name': name, 'workset_name': 'Exterior'}
                self.assertEqual(annotate_catalog({'elements': [item]})['elements'][0]['recommendation'], 'exclude')
        mep = ['Воздуховоды', 'Кабельные лотки', 'Короба', 'Электрооборудование', 'Сантехника',
               'Duct Fittings', 'Pipes', 'Conduits', 'Cable Trays', 'Mechanical Equipment',
               'Electrical Equipment', 'Sprinklers', 'Air Terminals']
        for category in mep:
            with self.subTest(category=category):
                item = annotate_catalog({'elements': [{'id': '1', 'category_name': category}]})['elements'][0]
                self.assertEqual(item['recommendation'], 'exclude')
                self.assertTrue(item['reason'])
        ambiguous = annotate_catalog({'elements': [{'id': '1', 'category_name': 'Walls',
                                                   'type_name': 'Interiorized concrete'}]})
        self.assertEqual(ambiguous['elements'][0]['recommendation'], 'review')

    def test_empty_selection_is_explicit_and_revisions_are_isolated(self):
        session = JobSession.create(self.root)
        source = self.root / 'source.rvt'
        source.write_bytes(b'original')
        first = session.request('scan', str(source))
        second = session.request('export', selection_ids=[])
        self.assertEqual(second['selection']['include_ids'], [])
        self.assertNotEqual(first['output_dir'], second['output_dir'])
        self.assertEqual(first['source_copy'], second['source_copy'])
        self.assertNotEqual(Path(first['source_copy']), source)
        self.assertEqual(source.read_bytes(), b'original')
        self.assertEqual(json.loads((session.directory / 'request.json').read_text()), second)
        other = JobSession.create(self.root)
        self.assertNotEqual(other.directory, session.directory)
        self.assertNotEqual(other.token, session.token)
        rescanned = session.request('scan', str(source))
        self.assertNotEqual(rescanned['source_copy'], first['source_copy'])
        self.assertEqual(Path(rescanned['source_copy']).name, rescanned['request_id'] + '.rvt')
        self.assertEqual(session.source_path, str(source))

    def test_stale_wrong_token_and_partial_responses_are_rejected(self):
        session = JobSession.create(self.root)
        request = session.request('shutdown')
        response = dict(schema=1, session_id=session.session_id, request_id=request['request_id'], success=True)
        target = session.directory / 'response.json'
        for field, value in [('session_id', 'foreign'), ('request_id', 'stale'), ('token', 'wrong')]:
            atomic_write_json(target, {**response, field: value})
            self.assertIsNone(session.read_response(request['request_id']))
        target.write_text('\ufeff' + json.dumps(response), encoding='utf-8')
        self.assertEqual(session.read_response(request['request_id']), response)
        target.write_text('{"success":', encoding='utf-8')
        self.assertIsNone(session.read_response(request['request_id']))
        atomic_write_json(session.directory / 'status.json', {**response, 'request_id': 'stale'})
        self.assertIsNone(session.read_status())

    def test_containment_and_options_isolation(self):
        with self.assertRaises(ValueError):
            contained_path(self.root, '../escape')
        with self.assertRaises(ValueError):
            contained_path(self.root, str(self.root.parent / 'escape'))
        session = JobSession.create(self.root)
        opts = {'nested': {'value': [1]}}
        request = session.request('shutdown', options=opts)
        opts['nested']['value'].append(2)
        self.assertEqual(request['options']['nested']['value'], [1])
        with self.assertRaises(ValueError):
            session.request('scan', str(session.directory / 'input' / 'model.rvt'))

    def test_install_owns_exact_manifest_and_refuses_foreign(self):
        dll = self.root / 'Worker.dll'
        dll.write_bytes(b'payload')
        plan = manifest_plan(str(self.root / 'Revit.exe'), str(dll), appdata_root=self.root)
        self.assertEqual(Path(plan['path']).parent.name, '2027')
        self.assertIn('<AllowLoadIntoExistingSession>False</AllowLoadIntoExistingSession>', plan['content'])
        installed = install_manifest(plan)
        self.assertTrue(installed.exists())
        install_manifest(plan)
        installed.write_text('<RevitAddIns><AddIn Type="Application"><Name>Foreign</Name></AddIn></RevitAddIns>')
        with self.assertRaises(ValueError):
            install_manifest(plan)
        missing = manifest_plan('', str(self.root / 'missing.dll'), appdata_root=self.root)
        with self.assertRaises(FileNotFoundError):
            install_manifest(missing)

    def test_cancel_only_owned_subprocess(self):
        worker = WorkerProcess()
        worker.terminate()
        session = JobSession.create(self.root)
        original_environment = dict(os.environ)
        process = Mock(pid=123)
        process.poll.return_value = None
        with patch('xivgate_revit_bridge.core.launcher.subprocess.Popen', return_value=process) as popen:
            worker.start('Revit.exe', session.directory, session.token, {'EXTRA': 'yes'})
            args, kwargs = popen.call_args
            self.assertEqual(args[0], ['Revit.exe'])
            self.assertEqual(kwargs['env']['XIVGATE_RBB_JOB_ROOT'], str(session.directory))
            self.assertEqual(kwargs['env']['XIVGATE_RBB_SESSION_TOKEN'], session.token)
            self.assertEqual(kwargs['env']['TEMP'], str(session.directory / 'tmp'))
            self.assertEqual(kwargs['env']['TMP'], str(session.directory / 'tmp'))
            self.assertEqual(kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')
            self.assertTrue((session.directory / 'tmp').is_dir())
            with self.assertRaises(RuntimeError):
                worker.start('Revit.exe', session.directory, session.token)
            worker.terminate()
        process.terminate.assert_called_once_with()
        self.assertEqual(worker.pid, 123)
        self.assertEqual(dict(os.environ), original_environment)

    def test_cancel_marker_rejects_another_request(self):
        session = JobSession.create(self.root)
        request = session.request('shutdown')
        with self.assertRaises(ValueError):
            session.cancel('old')
        marker = session.cancel(request['request_id'])
        self.assertEqual(marker['token'], session.token)
        self.assertEqual(json.loads((session.directory / 'cancel.json').read_text()), marker)

    def test_failed_atomic_write_preserves_previous_payload(self):
        target = self.root / 'request.json'
        atomic_write_json(target, {'old': True})
        with patch('xivgate_revit_bridge.core.jobs.os.replace', side_effect=PermissionError('busy')):
            with self.assertRaises(PermissionError):
                atomic_write_json(target, {'new': True})
        self.assertEqual(json.loads(target.read_text()), {'old': True})
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_response_requires_boolean_success_and_status_is_not_response(self):
        session = JobSession.create(self.root)
        request = session.request('shutdown')
        value = dict(schema=1, session_id=session.session_id, request_id=request['request_id'], state='completed')
        atomic_write_json(session.directory / 'status.json', value)
        self.assertEqual(session.read_status(), value)
        self.assertIsNone(session.read_response(request['request_id']))
        atomic_write_json(session.directory / 'response.json', {**value, 'success': 'true'})
        self.assertIsNone(session.read_response(request['request_id']))

    def test_manifest_escapes_payload_path_and_refuses_owned_reconnect(self):
        dll = self.root / 'Worker & Bridge.dll'
        dll.write_bytes(b'payload')
        plan = manifest_plan('', str(dll), appdata_root=self.root)
        xml = ET.fromstring(plan['content'])
        self.assertEqual(xml.findtext('./AddIn/Assembly'), str(dll))
        install_manifest(plan)
        other = self.root / 'Worker2.dll'
        other.write_bytes(b'next payload')
        updated = manifest_plan('', str(other), appdata_root=self.root)
        with self.assertRaisesRegex(ValueError, 'переподключ'):
            install_manifest(updated)
        self.assertEqual(ET.parse(plan['path']).findtext('./AddIn/Assembly'), str(dll))

    def test_manifest_same_binary_at_another_path_needs_no_reconnect(self):
        dll = self.root / 'Worker.dll'
        dll.write_bytes(b'payload')
        plan = manifest_plan('', str(dll), appdata_root=self.root)
        target = install_manifest(plan)
        original = target.read_bytes()
        other = self.root / 'PackagedWorker.dll'
        other.write_bytes(b'payload')
        packaged = manifest_plan('', str(other), appdata_root=self.root)
        self.assertTrue(manifest_matches(packaged))
        self.assertEqual(install_manifest(packaged), target)
        self.assertEqual(target.read_bytes(), original)
        other.write_bytes(b'changed payload')
        self.assertFalse(manifest_matches(packaged))
        with self.assertRaises(ValueError):
            install_manifest(packaged)
        self.assertEqual(target.read_bytes(), original)

    def test_manifest_matches_requires_both_payloads_and_owned_valid_xml(self):
        dll = self.root / 'Worker.dll'
        dll.write_bytes(b'payload')
        plan = manifest_plan('', str(dll), appdata_root=self.root)
        target = install_manifest(plan)
        self.assertTrue(manifest_matches(plan))
        dll.unlink()
        self.assertFalse(manifest_matches(plan))
        dll.write_bytes(b'payload')
        for content in ('<broken', plan['content'].replace('XIVG</VendorId>', 'BAD</VendorId>')):
            target.write_text(content)
            self.assertFalse(manifest_matches(plan))
        target.write_text(plan['content'])
        self.assertFalse(manifest_matches({**plan, 'content': '<broken'}))

    def test_foreign_manifest_with_same_name_or_duplicate_entry_is_refused(self):
        dll = self.root / 'Worker.dll'
        dll.write_bytes(b'payload')
        plan = manifest_plan('', str(dll), appdata_root=self.root)
        target = install_manifest(plan)
        for foreign in [plan['content'].replace('XIVG</VendorId>', 'BAD</VendorId>'),
                        plan['content'].replace('</RevitAddIns>', '<AddIn Type="Application"/></RevitAddIns>'),
                        '<broken']:
            target.write_text(foreign)
            with self.assertRaises(ValueError):
                install_manifest(plan)
            self.assertEqual(target.read_text(), foreign)

    def test_process_exit_does_not_terminate_again_and_reserved_env_is_protected(self):
        session = JobSession.create(self.root)
        worker = WorkerProcess()
        process = Mock(pid=234)
        process.poll.return_value = 0
        with patch('xivgate_revit_bridge.core.launcher.subprocess.Popen', return_value=process) as popen:
            worker.start('Revit.exe', session.directory, session.token,
                         {'XIVGATE_RBB_JOB_ROOT': 'foreign', 'XIVGATE_RBB_SESSION_TOKEN': 'foreign'})
            self.assertEqual(popen.call_args.kwargs['env']['XIVGATE_RBB_JOB_ROOT'], str(session.directory))
            self.assertEqual(popen.call_args.kwargs['env']['XIVGATE_RBB_SESSION_TOKEN'], session.token)
            self.assertEqual(worker.poll(), 0)
            worker.terminate()
        process.terminate.assert_not_called()

    def test_manifest_registration_folder_override_and_ambiguity(self):
        dll = self.root / 'Worker.dll'
        dll.write_bytes(b'payload')
        base = self.root / 'Autodesk' / 'Revit' / 'Addins'
        documented = base / 'Revit 2027'
        documented.mkdir(parents=True)
        plan = manifest_plan('', str(dll), appdata_root=self.root)
        self.assertEqual(Path(plan['path']).parent, documented)
        standard = base / '2027'
        standard.mkdir()
        with self.assertRaises(ValueError):
            manifest_plan('', str(dll), appdata_root=self.root)
        plan = manifest_plan('', str(dll), appdata_root=self.root, addin_folder=standard)
        self.assertEqual(Path(plan['path']).parent, standard)
        self.assertFalse(Path(plan['path']).exists())
        self.assertEqual(list(documented.iterdir()), [])

    def test_racing_foreign_manifest_never_overwritten(self):
        dll = self.root / 'Worker.dll'
        dll.write_bytes(b'payload')
        plan = manifest_plan('', str(dll), appdata_root=self.root)
        target = Path(plan['path'])

        def race(_temporary, destination):
            Path(destination).write_text('foreign manifest')
            raise FileExistsError('racing install')

        with patch('xivgate_revit_bridge.core.installer.os.link', side_effect=race):
            with self.assertRaises(ValueError):
                install_manifest(plan)
        self.assertEqual(target.read_text(), 'foreign manifest')
        self.assertEqual(list(target.parent.iterdir()), [target])


if __name__ == '__main__':
    unittest.main()
