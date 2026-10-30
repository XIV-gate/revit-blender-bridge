import unittest

from xivgate_revit_bridge.core.naming import is_generated_name, mesh_name, object_name


class NamingTests(unittest.TestCase):
    def test_readable_category_family_type_and_identifier(self):
        metadata = {'id': '9223372036854775807', 'category': 'Окна', 'family_name': 'Блок',
                    'type_name': '1800', 'name': '1800'}
        value = object_name(metadata)
        self.assertTrue(value.startswith('Окна'))
        self.assertIn('1800', value)
        self.assertTrue(value.endswith('[9223372036854775807]'))
        self.assertLessEqual(len(value.encode('utf-8')), 63)
        self.assertEqual(value.count('1800'), 1)

    def test_numeric_type_stays_meaningful_with_category(self):
        self.assertEqual(object_name({'revit:id': '11', 'revit:category': 'Окна', 'revit:type_name': '1800'}),
                         'Окна — 1800 [11]')
        self.assertEqual(object_name({'id': '11', 'category': 'Окна', 'name': 'Fixture 11'}),
                         'Окна — Fixture 11 [11]')

    def test_unicode_is_clipped_at_character_boundaries_and_retains_suffix(self):
        metadata = {'id': '1234567890123456789', 'category': 'Наружные стены',
                    'family_name': 'Очень длинное название фасадной системы ' * 10,
                    'type_name': '1800', 'name': 'Необязательная подпись'}
        value = object_name(metadata)
        self.assertLessEqual(len(value.encode('utf-8')), 63)
        self.assertEqual(value.encode().decode(), value)
        self.assertTrue(value.endswith('[1234567890123456789]'))
        self.assertIn('1800', value)
        self.assertNotIn('\ufffd', value)

    def test_shared_mesh_variant_and_independent_mesh_identity(self):
        metadata = {'id': '11', 'category': 'Окна', 'family_name': 'Блок', 'type_name': '1800'}
        signature = 'abcdef12' + '0' * 56
        shared = mesh_name(metadata, signature)
        self.assertTrue(shared.endswith('[g:abcdef12]'))
        self.assertIn('1800', shared)
        self.assertNotEqual(shared, mesh_name(metadata, '1' * 64))
        self.assertTrue(mesh_name(metadata, signature, element_id='11').endswith('[11]'))
        self.assertLessEqual(len(shared.encode()), 63)

    def test_generated_marker_distinguishes_real_user_edits_from_legacy_names(self):
        for name in ('e_123', 'e_123.001', 'e_123.001_mesh', 'p_123', 'mesh', 'mesh.001', '123.001'):
            with self.subTest(name=name):
                self.assertTrue(is_generated_name(name))
        self.assertFalse(is_generated_name('Моё окно'))
        self.assertTrue(is_generated_name('Окна — 1800 [11]', 'Окна — 1800 [11]'))
        self.assertFalse(is_generated_name('e_123', 'Окна — 1800 [11]'))

    def test_fallbacks_controls_and_repeated_fields(self):
        value = object_name({'id': '11', 'name': '11'})
        self.assertIn('Элемент Revit', value)
        self.assertTrue(value.endswith('[11]'))
        clean = object_name({'id': '1', 'category': 'Окна\n', 'family_name': 'Окна', 'type_name': '1800\x00\t'})
        self.assertNotIn('\n', clean)
        self.assertNotIn('\x00', clean)
        self.assertEqual(clean.count('Окна'), 1)


if __name__ == '__main__':
    unittest.main()
