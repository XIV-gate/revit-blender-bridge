import struct
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
from xivgate_revit_bridge.core.rvt_metadata import parse_basic_info, choose_revit

class RvtMetadataTests(unittest.TestCase):
    def test_header_and_format_years(self):
        value='Autodesk Revit 2024 (Build: test)'
        raw=b'\0'*15+struct.pack('<I',len(value))+value.encode('utf-16le')
        self.assertEqual(parse_basic_info(raw)['year'],2024)
        self.assertEqual(parse_basic_info(b'\1'+"Worksharing: Enabled\r\nFormat: 2027\r\n".encode('utf-16le'))['year'],2027)
        with self.assertRaises(ValueError):parse_basic_info(raw+'\r\nFormat: 2026\r\n'.encode('utf-16le'))
        with self.assertRaises(ValueError):parse_basic_info('Name: project_2025.rvt'.encode('utf-16le'))

    def test_auto_retry_refreshes_transient_read_failure(self):
        from xivgate_revit_bridge.core.rvt_metadata import file_info
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as folder:
            source=Path(folder)/'locked.rvt';source.write_bytes(b'fixture')
            with patch('xivgate_revit_bridge.core.rvt_metadata._read_stream',side_effect=[OSError('locked'), 'Format: 2024\r\n'.encode('utf-16le')]):
                self.assertIsNone(file_info(source)['year'])
                self.assertEqual(file_info(source,refresh=True)['year'],2024)

    def test_auto_select_same_year_then_nearest_newer(self):
        rows=[dict(year=2027,version='27.2',path='27'),dict(year=2024,version='24.1',path='24a'),dict(year=2024,version='24.3',path='24b')]
        self.assertEqual(choose_revit(2024,rows)['path'],'24b')
        self.assertEqual(choose_revit(2019,rows)['path'],'24b')
        self.assertEqual(choose_revit(2026,rows)['path'],'27')
        self.assertIsNone(choose_revit(2028,rows))
        self.assertIsNone(choose_revit(None,rows))
