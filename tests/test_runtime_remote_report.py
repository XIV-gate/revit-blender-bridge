import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


def report_function():
    source = Path(__file__).parents[1] / 'xivgate_revit_bridge' / 'runtime.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == '_remote_component_warnings')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_remote_component_warnings']


class RuntimeRemoteReportTests(unittest.TestCase):
    def test_one_aggregate_warning_and_element_reasons_are_replaced_without_losing_base_reason(self):
        function = report_function()
        props = SimpleNamespace(elements=[SimpleNamespace(element_id='floor', reason='Исходная причина'),
                                          SimpleNamespace(element_id='other', reason='Другая причина')])
        report = dict(elements=[dict(id='floor', removed_components=1, removed_triangles=158),
                                dict(id='second', removed_components=2, removed_triangles=12)])
        result = function(props, report, ['Прежнее предупреждение'])
        self.assertEqual(len(result), 2)
        self.assertIn('floor', result[1])
        self.assertIn('second', result[1])
        self.assertIn('Убирать дальние части', result[1])
        self.assertTrue(props.elements[0].reason.startswith('Исходная причина'))
        self.assertIn('158', props.elements[0].reason)
        self.assertEqual(props.elements[1].reason, 'Другая причина')
        self.assertEqual(function(props, report, result), result)
        cleared = function(props, {}, result)
        self.assertEqual(cleared, ['Прежнее предупреждение'])
        self.assertEqual(props.elements[0].reason, 'Исходная причина')


if __name__ == '__main__':
    unittest.main()
