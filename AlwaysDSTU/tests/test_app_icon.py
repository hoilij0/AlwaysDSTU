"""Иконка приложения не влияет на запуск при отсутствии ресурса."""

import struct
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main


class AppIconTests(unittest.TestCase):
    def test_ico_contains_common_windows_sizes(self):
        path = main.ICON_DIR / "icon.ico"
        with path.open("rb") as stream:
            reserved, kind, count = struct.unpack("<HHH", stream.read(6))
            sizes = set()
            for _ in range(count):
                width, height, *_ = struct.unpack("<BBBBHHII", stream.read(16))
                sizes.add((width or 256, height or 256))
        self.assertEqual((reserved, kind), (0, 1))
        self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes)
        self.assertTrue((main.ICON_DIR / "icon.png").is_file())

    def test_windows_icon_is_set_as_default_and_for_main_window(self):
        root = Mock()
        with patch.object(main.sys, "platform", "win32"):
            main._apply_window_icon(root)
        icon = str(main.ICON_DIR / "icon.ico")
        self.assertEqual(root.iconbitmap.call_count, 2)
        root.iconbitmap.assert_any_call(default=icon)
        root.iconbitmap.assert_any_call(icon)

    def test_missing_or_invalid_icon_does_not_stop_the_app(self):
        root = Mock()
        with patch.object(main.sys, "platform", "win32"), patch.object(main, "ICON_DIR", Path("missing-assets")):
            main._apply_window_icon(root)
        root.iconbitmap.assert_not_called()
        root.iconbitmap.side_effect = tk.TclError("invalid icon")
        with patch.object(main.sys, "platform", "win32"):
            main._apply_window_icon(root)


if __name__ == "__main__":
    unittest.main()
