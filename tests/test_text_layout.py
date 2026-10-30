import unittest
from xivgate_revit_bridge.core.text_layout import pixel_wrap


def proportional(text):
    return sum(12 if c in 'ШЩMW' else 3 if c in 'il., ' else 7 for c in text)


class TextLayoutTests(unittest.TestCase):
    def test_proportional_measure_not_character_count(self):
        self.assertEqual(pixel_wrap('iiii WWWW', 50, proportional), ['iiii', 'WWWW'])
        self.assertEqual(pixel_wrap('iiii iiii', 50, proportional), ['iiii iiii'])

    def test_wide_and_narrow_russian_status(self):
        text = 'Отсоединение RVT в локальной рабочей копии с закрытыми рабочими наборами'
        narrow = pixel_wrap(text, 170, proportional)
        wide = pixel_wrap(text, 450, proportional)
        self.assertGreater(len(narrow), len(wide))
        self.assertEqual(' '.join(narrow), text)
        self.assertTrue(all(proportional(line) <= 170 for line in narrow))

    def test_paths_and_long_unicode_tokens_are_never_lost_or_clipped(self):
        path = 'C:\\Users\\Denis Lukinykh\\ОченьДлинноеНазваниеБезПробелов.rvt'
        lines = pixel_wrap(path, 105, proportional)
        self.assertEqual(''.join(lines).replace(' ', ''), path.replace(' ', ''))
        self.assertTrue(all(proportional(line) <= 105 for line in lines))
        self.assertTrue(any(line.endswith('\\') for line in lines))

    def test_explicit_newlines_and_spacing(self):
        self.assertEqual(pixel_wrap('  Первая   строка\nВторая\n\nТретья ', 500, proportional),
                         ['Первая строка', 'Вторая', '', 'Третья'])
        self.assertEqual(pixel_wrap('', 10, proportional), [])

    def test_short_russian_preposition_moves_with_following_word(self):
        lines = pixel_wrap('Копия RVT с закрытыми наборами', 90, proportional)
        self.assertIn('с закрытыми', lines)
        self.assertFalse(any(line.endswith(' с') for line in lines))

    def test_tiny_budget_makes_progress(self):
        self.assertEqual(pixel_wrap('ШЩ', 1, proportional), ['Ш', 'Щ'])

if __name__ == '__main__':
    unittest.main()
