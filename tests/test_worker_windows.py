import unittest
from unittest.mock import Mock, patch
from xivgate_revit_bridge.core.launcher import choose_main_window


class WorkerWindowsTests(unittest.TestCase):
    def test_only_own_main_window_not_runtime_helpers(self):
        rows = [dict(hwnd=1, pid=7, title='Autodesk Revit 2026.4', class_name='Main', area=800000, tool=False),
                dict(hwnd=2, pid=7, title='.NET-BroadcastEventWindow.1', class_name='WindowsForms', area=900000, tool=False),
                dict(hwnd=3, pid=7, title='Revit', class_name='Dialog', area=100000, tool=False),
                dict(hwnd=4, pid=8, title='Autodesk Revit 2027', class_name='Main', area=2000000, tool=False),
                dict(hwnd=5, pid=7, title='Revit helper', class_name='Main', area=3000000, tool=True)]
        self.assertEqual(choose_main_window(rows, 7), 1)

    def test_never_reveal_unknown_hidden_windows(self):
        rows = [dict(hwnd=1, pid=7, title='SystemResourceNotifyWindow', class_name='Main', area=500, tool=False),
                dict(hwnd=2, pid=7, title='Revit', class_name='NotificationUIHandler', area=800000, tool=False)]
        self.assertIsNone(choose_main_window(rows, 7))

    def test_native_command_shows_only_main_and_visible_owned_popup(self):
        import ctypes
        from ctypes import wintypes
        from xivgate_revit_bridge.core.launcher import show_main_window
        api = Mock()
        titles = {1: 'Autodesk Revit 2026.4', 2: '.NET-BroadcastEventWindow', 3: 'NotificationUIHandler'}
        def enum(callback, _):
            for hwnd in titles:
                callback(hwnd, 0)
            return True
        def owner(hwnd, pointer):
            ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = 7
            return 1
        def title(hwnd, buffer, _):
            buffer.value = titles[hwnd]
            return len(buffer.value)
        def name(hwnd, buffer, _):
            buffer.value = 'Main' if hwnd == 1 else 'NotificationUIHandler'
            return len(buffer.value)
        def rect(hwnd, pointer):
            value = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
            value.left, value.top, value.right, value.bottom = 0, 0, 1000, 800
            return True
        api.EnumWindows.side_effect = enum
        api.GetWindowThreadProcessId.side_effect = owner
        api.GetWindowTextW.side_effect = title
        api.GetClassNameW.side_effect = name
        api.GetWindowRect.side_effect = rect
        api.GetWindowLongW.return_value = 0
        api.IsIconic.return_value = False
        api.GetLastActivePopup.return_value = 3
        api.IsWindowVisible.return_value = False
        with patch('ctypes.WinDLL', return_value=api):
            show_main_window(7)
        api.ShowWindow.assert_called_once_with(1, 5)
        api.ShowWindow.reset_mock()
        api.GetLastActivePopup.return_value = 4
        api.IsWindowVisible.return_value = True
        with patch('ctypes.WinDLL', return_value=api):
            show_main_window(7)
        self.assertEqual([call.args for call in api.ShowWindow.call_args_list], [(1, 5), (4, 5)])
