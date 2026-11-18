"""Image sizing keeps original pixels and adapts each screenshot panel."""
import gc
import hashlib
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

import app as ui
from image_fit import fitted_size, preview_size, render_preview


class SizeTests(unittest.TestCase):
    def test_fitting_preserves_proportions_without_upscaling(self):
        self.assertEqual(fitted_size(1200, 600, 300, 300), (300, 150))
        self.assertEqual(fitted_size(600, 1200, 300, 300), (150, 300))
        self.assertEqual(fitted_size(30, 20, 300, 300), (30, 20))
        with self.assertRaises(ValueError):
            fitted_size(0, 20, 300, 300)

    def test_tk_fallback_remains_within_bounds(self):
        class Source:
            def width(self): return 803
            def height(self): return 401
            def subsample(self, factor_x, factor_y): return factor_x, factor_y
        source = Source()
        with patch('image_fit.Image', None):
            size = preview_size(source, 300, 300)
            factor_x, factor_y = render_preview(source, size, None)
        self.assertEqual((factor_x, factor_y), (3, 3))
        self.assertLessEqual(size[0], 300)
        self.assertLessEqual(size[1], 300)


class ImageResizeUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.enterContext(patch.object(ui.ScreenshotApp, '_schedule_sheet_preview', lambda self: None))
        fake_adb = type('FakeAdb', (), {'list_devices': lambda self: []})()
        try:
            with patch.object(ui, 'AdbService', return_value=fake_adb):
                self.window = ui.ScreenshotApp(state_file=Path(self.tmp.name) / 'state.json')
        except tk.TclError as exc:
            self.skipTest(f'GUI display unavailable: {exc}')
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        window, self.window = self.window, None
        if window.worker is not None:
            window.worker.join(timeout=30)
        if window.sheet_worker is not None:
            window.sheet_worker.join(timeout=30)
        window.destroy()
        del window
        gc.collect()

    def settle(self):
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            self.window.update()
            time.sleep(.01)

    def test_all_panels_fit_grow_and_keep_originals(self):
        start = Path(self.tmp.name) / 'start.png'
        finish = Path(self.tmp.name) / 'finish.png'
        Image.new('RGB', (1080, 1920), 'navy').save(start)
        Image.new('RGB', (1920, 1080), 'teal').save(finish)
        before = [hashlib.sha256(path.read_bytes()).digest() for path in (start, finish)]
        self.window.geometry('1000x720')
        self.window.current_path = start
        self.window.current_metadata = {'screenshot': start.name, 'journey': {'status': 'completed',
            'start': {'screenshot': start.name}, 'finish': {'screenshot': finish.name, 'status': 'captured'}}}
        self.settle()
        self.window._display_image(start, self.window.current_metadata)
        self.window._refresh_journey_view()
        self.settle()
        small = [(self.window.start_thumb_photo.width(), self.window.start_thumb_photo.height()),
                 (self.window.finish_thumb_photo.width(), self.window.finish_thumb_photo.height()),
                 (self.window.photo.width(), self.window.photo.height())]
        self.window.geometry('1400x900')
        self.settle()
        large = [(self.window.start_thumb_photo.width(), self.window.start_thumb_photo.height()),
                 (self.window.finish_thumb_photo.width(), self.window.finish_thumb_photo.height()),
                 (self.window.photo.width(), self.window.photo.height())]
        for before_size, after_size, source_size in zip(small, large, ((1080, 1920), (1920, 1080), (1080, 1920))):
            self.assertGreaterEqual(after_size[0], before_size[0])
            self.assertGreaterEqual(after_size[1], before_size[1])
            self.assertAlmostEqual(after_size[0] / after_size[1], source_size[0] / source_size[1], delta=.02)
        for photo, widget in ((self.window.start_thumb_photo, self.window.start_thumbnail),
                              (self.window.finish_thumb_photo, self.window.finish_thumbnail),
                              (self.window.photo, self.window.preview)):
            self.assertLessEqual(photo.width(), widget.winfo_width(), str(widget))
            self.assertLessEqual(photo.height(), widget.winfo_height(), str(widget))
        self.window.geometry('800x580')
        self.settle()
        self.assertIsNone(self.window.preview_resize_job, 'resize events should settle')
        for photo, widget in ((self.window.start_thumb_photo, self.window.start_thumbnail),
                              (self.window.finish_thumb_photo, self.window.finish_thumbnail),
                              (self.window.photo, self.window.preview)):
            self.assertLessEqual(photo.width(), widget.winfo_width(), str(widget))
            self.assertLessEqual(photo.height(), widget.winfo_height(), str(widget))
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).digest() for path in (start, finish)])


if __name__ == '__main__':
    unittest.main()
