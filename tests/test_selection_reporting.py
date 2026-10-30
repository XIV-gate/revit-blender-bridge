import unittest

from xivgate_revit_bridge.core.selection_view import group_state, matches, exclusion_summary
from xivgate_revit_bridge.core.reporting import report_entries, raw_values


class SelectionReportingTests(unittest.TestCase):
    def test_mixed_is_explicit_and_filter_does_not_change_choices(self):
        self.assertEqual(group_state(4710, 5380), 'partial')
        self.assertEqual(group_state(0, 5380), 'none')
        self.assertEqual(group_state(5380, 5380), 'all')
        item = dict(selected=False, category_id='walls', type_name='Вн_200',
                    recommendation='exclude', reason='По имени типа')
        self.assertTrue(matches(item, query='вн_', scope_kind='category', scope_id='walls', status='excluded'))
        self.assertFalse(matches(item, scope_kind='category', scope_id='windows'))
        self.assertFalse(matches(item, status='selected'))
        item['selected'] = True  # User override remains visible as selected.
        self.assertTrue(matches(item, status='selected'))
        self.assertFalse(matches(item, status='excluded'))

    def test_exclusion_summary_distinguishes_recommendation_and_user_choice(self):
        rows = [dict(selected=False, recommendation='exclude', reason='По имени типа'),
                dict(selected=False, recommendation='include', reason='Окно'),
                dict(selected=True, recommendation='exclude', reason='По имени типа')]
        result = exclusion_summary(rows)
        self.assertCountEqual(result, [('По имени типа', 1), ('Исключено пользователем; рекомендация: Окно', 1)])

    def test_no_geometry_is_one_information_entry_with_lossless_details(self):
        prefix = 'Выбранный элемент не дал видимой геометрии (фаза, вариант, отсутствие сетки или неподдерживаемая связь): '
        values = [prefix + str(i) for i in range(2210)] + ['Настоящее предупреждение', prefix + '0']
        entries = report_entries(values)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['severity'], 'INFO')
        self.assertEqual(entries[0]['count'], 2210)
        self.assertIn('2210', entries[0]['message'])
        self.assertEqual(raw_values(entries), list(dict.fromkeys(values)))
        self.assertEqual(report_entries(raw_values(entries)), entries)
        self.assertEqual(entries[1]['severity'], 'WARNING')

    def test_other_warnings_are_not_silenced(self):
        result = report_entries(['Не найдена текстура: a.jpg', 'Не найдена текстура: b.jpg', 'Ошибка Revit'])
        self.assertEqual(len(result), 3)
        self.assertTrue(all(row['severity'] == 'WARNING' for row in result))

