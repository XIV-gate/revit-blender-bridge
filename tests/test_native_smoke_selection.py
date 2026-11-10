import unittest
from tools.test_optional_startup_native import select_smoke_ids


class NativeSmokeSelectionTests(unittest.TestCase):
    def test_late_windows_are_selected_instead_of_early_datums_and_area_lines(self):
        rows = [dict(id=str(n), category_name='<Границы зон>', recommendation='review', bbox_min=[0, 0, 0])
                for n in range(40)]
        rows.append(dict(id='9007199254740993', category_name='Окна', recommendation='include', bbox_min=[1, 2, 3]))
        self.assertEqual(select_smoke_ids(rows), ['9007199254740993'])

    def test_tiny_fixture_falls_back_only_to_generic_geometry(self):
        rows = [dict(id='1', category_name='Камеры', recommendation='review', bbox_min=[0, 0, 0]),
                dict(id='2', category_name='Обобщенные модели', recommendation='review', bbox_min=[0, 0, 0])]
        self.assertEqual(select_smoke_ids(rows), ['2'])
        self.assertEqual(select_smoke_ids(rows, categories=['Окна']), [])
