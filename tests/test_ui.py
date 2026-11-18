"""Exercise the capture/review flow with a fake device and OCR engine."""
from __future__ import annotations

import json
import gc
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import app as ui


class FakeAdb:
    def __init__(self, png):
        self.png = png

    def list_devices(self):
        return [("192.168.0.2:5555", "device")]

    def device_info(self, serial):
        return {"model": "Test", "android_version": "14"}

    def identify_app(self, serial):
        return "네이버 지도", "com.nhn.android.nmap"

    def capture_screenshot(self, serial, path):
        Path(path).write_bytes(self.png)


class ScreenshotUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        try:
            self.window = ui.ScreenshotApp(state_file=Path(self.tmp.name) / 'active.json')
        except tk.TclError as exc:
            self.skipTest(f"GUI display not available: {exc}")
        self.window.withdraw()
        self.addCleanup(self.cleanup_window)
        image = tk.PhotoImage(width=2, height=2)
        image.put("red", to=(0, 0, 2, 2))
        source = Path(self.tmp.name) / "source.png"
        image.write(str(source), format="png")
        self.window.adb = FakeAdb(source.read_bytes())
        self.window.output_dir.set(self.tmp.name)
        self.window.refresh_devices()
        self.wait_for(lambda: not self.window.busy and self.window.selected_serial())

    def cleanup_window(self):
        window, self.window = self.window, None
        if window.worker is not None:
            window.worker.join(timeout=30)
        if window.sheet_worker is not None:
            window.sheet_worker.join(timeout=30)
        window.destroy()
        del window
        gc.collect()

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.window.update()
            if condition():
                return
            time.sleep(0.01)
        self.fail("GUI operation timed out")

    def capture(self):
        result = {"raw_text": "14:22 12 km 5분", "screen_time": "14:22", "distance": "12 km", "duration": "5분"}
        with patch.object(ui, "recognize", return_value=result):
            self.window.capture()
            self.wait_for(lambda: self.window.current_path is not None and not self.window.busy)
        self.assertEqual(self.window.distance.get(), "12 km")

    def test_save_reopen_and_dirty_protection(self):
        self.capture()
        self.window.distance.set("13 km")
        self.assertTrue(self.window.save_review())
        metadata_path = self.window.current_path.with_suffix(".json")
        self.window.distance.set("변경 중")
        with patch.object(ui.messagebox, "askyesnocancel", return_value=None):
            self.assertFalse(self.window._allow_switch())
        with patch.object(ui.messagebox, "askyesnocancel", return_value=False), \
             patch.object(ui.filedialog, "askopenfilename", return_value=str(metadata_path)):
            self.window.open_capture()
        self.assertEqual(self.window.distance.get(), "13 km")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["ocr"]["distance"], "12 km")
        self.assertEqual(metadata["review"]["distance"], "13 km")

    def test_failed_ocr_preserves_image_and_allows_manual_save(self):
        self.capture()
        self.window.distance.set("13 km")
        self.assertTrue(self.window.save_review())
        with patch.object(ui, "recognize", side_effect=ui.OCRError("인식 실패")):
            self.window.recognize_again()
            self.wait_for(lambda: not self.window.busy)
        self.assertTrue(self.window.current_path.exists())
        self.assertEqual(self.window.distance.get(), "13 km")
        self.window.distance.set("14 km")
        self.assertTrue(self.window.save_review())
        metadata = json.loads(self.window.current_path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["ocr"]["status"], "failed")
        self.assertEqual(metadata["review"]["distance"], "14 km")

    def test_capture_survives_adb_app_detection_failure(self):
        with patch.object(self.window.adb, "identify_app", side_effect=[
            ui.AdbError("dumpsys unavailable"), ("네이버 지도", "com.nhn.android.nmap")
        ]), patch.object(ui.messagebox, "showerror"):
            self.capture()
        self.assertTrue(self.window.current_path.exists())
        metadata = json.loads(self.window.current_path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["app_detection"], "uncertain")
        self.assertIsNone(metadata["app"])
        self.assertIsNone(metadata["app_before"]["name"])
        self.assertEqual(metadata["app_after"]["name"], "네이버 지도")

    def test_capture_reports_unexpected_app_detection_error(self):
        with patch.object(self.window.adb, "identify_app", side_effect=RuntimeError("unexpected")), \
             patch.object(ui.messagebox, "showerror") as error:
            self.window.capture()
            self.wait_for(lambda: not self.window.busy)
        error.assert_called_once()
        self.assertIsNone(self.window.current_path)

    def test_failed_metadata_replace_keeps_previous_file(self):
        path = Path(self.tmp.name) / "result.json"
        path.write_text('{"previous": true}', encoding="utf-8")
        with patch.object(ui.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                ui.write_metadata(path, {"new": True})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"previous": True})

    def test_data_window_saves_edits_before_export_can_open(self):
        self.capture()
        self.window.distance.set("13 km")
        with patch.object(ui.messagebox, "askyesno", return_value=False), \
             patch('data_window.DataWindow') as dialog:
            self.window.open_data()
            dialog.assert_not_called()
        with patch.object(ui.messagebox, "askyesno", return_value=True), \
             patch('data_window.DataWindow') as dialog:
            self.window.open_data()
            dialog.assert_called_once()
        metadata = json.loads(self.window.current_path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(metadata['review']['distance'], '13 km')

    def test_departure_arrival_keep_separate_images_and_reviews(self):
        ocr = {"raw_text": "14:22 12 km 5분", "screen_time": "14:22", "distance": "12 km",
               "duration": "5분", "toll": "1,400원"}
        self.window.journey_gps.set(False)
        self.assertTrue(self.window.arrival_button.instate(['disabled']))
        with patch.object(ui, 'recognize', return_value=ocr), patch('journey_service.GpsTracker') as tracker:
            tracker.return_value.device_identity.return_value = {'id': 'test', 'source': 'hardware'}
            self.window.start_journey()
            self.wait_for(lambda: self.window.current_path is not None and not self.window.busy)
            start = self.window.current_path
            self.assertEqual(self.window.current_metadata['journey']['status'], 'in_progress')
            self.assertEqual(self.window._active_journey_path(), start.with_suffix('.json').resolve())
            self.window.current_path = None
            self.window.current_metadata = None
            self.window._restore_active_journey()
            self.assertEqual(self.window.current_path, start.resolve())
            self.assertTrue(self.window.departure_button.instate(['disabled']))
            self.assertFalse(self.window.arrival_button.instate(['disabled']))
            self.window.distance.set('13 km')
            self.assertEqual(self.window.toll.get(), '1,400원')
            self.window.toll.set('0원')
            self.window.screen_time.set('23:55')
            self.window.duration.set('15분')
            self.assertEqual(self.window.estimated_arrival.get(), '다음 날 00:10')
            self.window.estimated_arrival.set('다음 날 00:18')
            self.assertTrue(self.window.save_review())
            self.window.finish_journey()
            self.wait_for(lambda: not self.window.busy)
        metadata = json.loads(start.with_suffix('.json').read_text(encoding='utf-8'))
        finish = start.parent / metadata['journey']['finish']['screenshot']
        self.assertTrue(start.is_file())
        self.assertTrue(finish.is_file())
        self.assertEqual(metadata['journey']['status'], 'completed')
        self.assertFalse(self.window.state_file.exists())
        self.assertEqual(self.window.selected_phase, 'finish')
        self.assertEqual(self.window.toll.get(), '1,400원')
        self.assertEqual(self.window.estimated_arrival.get(), '')
        self.assertFalse(self.window.eta_entry.grid_info())
        finish_labels = {w.cget('text') for w in self.window.eta_entry.master.grid_slaves()
                         if w.winfo_class() == 'TLabel'}
        self.assertEqual(finish_labels, {'화면 표시 시간', '주행거리', '소요시간', '실 통행료'})
        self.window.distance.set('14 km')
        self.assertTrue(self.window.save_review())
        self.window.select_journey_phase('start')
        self.assertEqual(self.window.distance.get(), '13 km')
        start_labels = {w.cget('text') for w in self.window.eta_entry.master.grid_slaves()
                        if w.winfo_class() == 'TLabel'}
        self.assertIn('예상 통행료', start_labels)
        self.assertNotIn('실 통행료', start_labels)
        self.assertEqual(self.window.toll.get(), '0원')
        self.assertEqual(self.window.estimated_arrival.get(), '다음 날 00:18')
        self.assertTrue(self.window.eta_entry.grid_info())
        self.window.select_journey_phase('finish')
        self.assertEqual(self.window.distance.get(), '14 km')
        metadata = json.loads(start.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['review']['distance'], '13 km')
        self.assertEqual(metadata['review']['toll'], '0원')
        self.assertEqual(metadata['review']['estimated_arrival'], '다음 날 00:18')
        self.assertTrue(metadata['review']['estimated_arrival_manual'])
        self.assertEqual(metadata['journey']['finish']['review']['distance'], '14 km')
        self.assertEqual(metadata['journey']['finish']['review']['estimated_arrival'], '')
        self.window.open_capture_file(str(finish))
        self.assertEqual(self.window.selected_phase, 'finish')
        self.assertEqual(self.window.distance.get(), '14 km')
        self.assertEqual(self.window.toll.get(), '1,400원')

    def test_legacy_toll_fallback_can_be_cleared_and_eta_tracks_valid_inputs(self):
        self.capture()
        path = self.window.current_path.with_suffix('.json')
        metadata = self.window.current_metadata
        metadata['ocr']['toll'] = '1,400원'
        metadata['review'] = {'status': 'reviewed', 'screen_time': '22:12',
                              'distance': '19km', 'duration': '35분'}
        ui.write_metadata(path, metadata)
        self.window.open_capture_file(path)
        self.assertEqual(self.window.toll.get(), '1,400원')
        self.assertEqual(self.window.estimated_arrival.get(), '22:47')
        self.window.toll.set('')
        with patch.object(ui.messagebox, 'askyesnocancel', return_value=None):
            self.assertFalse(self.window._allow_switch())
        self.window.duration.set('')
        self.assertEqual(self.window.estimated_arrival.get(), '')
        self.assertTrue(self.window.save_review())
        self.window.open_capture_file(path)
        self.assertEqual(self.window.toll.get(), '')
        self.assertEqual(self.window.current_metadata['review']['estimated_arrival'], '')

    def test_empty_arrival_panel_does_not_replace_selected_screenshot(self):
        self.capture()
        original = self.window.preview_path
        with patch.object(ui.messagebox, 'showwarning') as warning:
            self.window.select_journey_phase('finish')
            warning.assert_called_once()
        self.assertEqual(self.window.selected_phase, 'start')
        self.assertEqual(self.window.preview_path, original)
        self.assertEqual(self.window.distance.get(), '12 km')

    def test_manual_eta_survives_rerun_reopen_and_reverts_to_auto_on_clear(self):
        self.capture()
        window = self.window
        path = window.current_path.with_suffix('.json')
        self.assertEqual(window.estimated_arrival.get(), '14:27')
        window.eta_entry.delete(0, 'end')
        self.assertEqual(window.eta_entry.get(), '')
        window.eta_entry.insert(0, '14:27')  # Explicit entry equal to the automatic estimate.
        with patch.object(ui.messagebox, 'askyesnocancel', return_value=None):
            self.assertFalse(window._allow_switch())
        window.duration.set('20분')
        self.assertEqual(window.eta_entry.get(), '14:27')
        window.estimated_arrival.set('14:48')
        self.assertTrue(window.save_review())
        window.open_capture_file(path)
        self.assertEqual(window.eta_entry.get(), '14:48')
        self.assertTrue(window.current_metadata['review']['estimated_arrival_manual'])
        with patch.object(ui, 'recognize', return_value={'screen_time': '14:22', 'duration': '8분'}):
            window.recognize_again()
            self.wait_for(lambda: not window.busy)
        self.assertEqual(window.eta_entry.get(), '14:48')
        window.eta_entry.delete(0, 'end')
        self.assertTrue(window.save_review())
        window.open_capture_file(path)
        self.assertEqual(window.estimated_arrival.get(), '14:30')
        self.assertFalse(window.current_metadata['review'].get('estimated_arrival_manual'))
        window.duration.set('10분')
        self.assertEqual(window.estimated_arrival.get(), '14:32')

    def test_phase_change_cancel_preserves_unsaved_review_and_preview(self):
        self.capture()
        finish = Path(self.tmp.name) / 'finish.png'
        finish.write_bytes(self.window.current_path.read_bytes())
        self.window.current_metadata['journey'] = {'status': 'completed', 'start': {},
            'finish': {'screenshot': finish.name, 'ocr': {'distance': '20 km'}, 'review': {'status': 'needs_review'}}}
        ui.write_metadata(self.window.current_path.with_suffix('.json'), self.window.current_metadata)
        self.window._refresh_journey_view()
        original = self.window.preview_path
        source = self.window.preview_source
        filename = self.window.selected_filename.get()
        selected_style = self.window.start_tab.cget('style')
        unselected_style = self.window.finish_tab.cget('style')
        self.assertNotEqual(selected_style, unselected_style)
        self.window.distance.set('수정 중')
        with patch.object(ui.messagebox, 'askyesnocancel', return_value=None):
            self.window.select_journey_phase('finish')
        self.assertEqual(self.window.selected_phase, 'start')
        self.assertEqual(self.window.preview_path, original)
        self.assertIs(self.window.preview_source, source)
        self.assertEqual(self.window.selected_filename.get(), filename)
        self.assertEqual(self.window.start_tab.cget('style'), selected_style)
        self.assertEqual(self.window.finish_tab.cget('style'), unselected_style)
        self.assertEqual(self.window.distance.get(), '수정 중')
        self.window.select_journey_phase('start')
        self.assertEqual(self.window.distance.get(), '수정 중')

    def test_missing_arrival_image_does_not_redirect_review_save(self):
        self.capture()
        original = self.window.preview_path
        self.window.current_metadata['journey'] = {'status': 'completed', 'start': {},
            'finish': {'screenshot': 'missing.png', 'ocr': {'distance': '99 km'}, 'review': {'status': 'needs_review'}}}
        ui.write_metadata(self.window.current_path.with_suffix('.json'), self.window.current_metadata)
        with patch.object(ui.messagebox, 'showwarning') as warning:
            self.window.select_journey_phase('finish')
            warning.assert_called_once()
        self.assertEqual(self.window.selected_phase, 'start')
        self.assertEqual(self.window.preview_path, original)
        self.assertEqual(self.window.distance.get(), '12 km')
        self.window.distance.set('13 km')
        self.assertTrue(self.window.save_review())
        metadata = json.loads(self.window.current_path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['review']['distance'], '13 km')
        self.assertEqual(metadata['journey']['finish']['review']['status'], 'needs_review')

    def test_render_failure_keeps_original_phase_and_preview_path(self):
        self.capture()
        original = self.window.preview_path
        finish = Path(self.tmp.name) / 'finish.png'
        finish.write_bytes(original.read_bytes())
        self.window.current_metadata['journey'] = {'status': 'completed', 'start': {},
            'finish': {'screenshot': finish.name, 'ocr': {'distance': '99 km'}, 'review': {'status': 'needs_review'}}}
        ui.write_metadata(self.window.current_path.with_suffix('.json'), self.window.current_metadata)
        self.window.deiconify()
        self.window.update()
        with patch.object(ui, 'render_preview', side_effect=tk.TclError('render failed')), \
             patch.object(ui.messagebox, 'showwarning') as warning:
            self.window.select_journey_phase('finish')
            warning.assert_called_once()
        self.assertEqual(self.window.selected_phase, 'start')
        self.assertEqual(self.window.preview_path, original)
        self.assertEqual(self.window.distance.get(), '12 km')
        self.window.distance.set('13 km')
        self.assertTrue(self.window.save_review())
        saved = json.loads(self.window.current_path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['review']['distance'], '13 km')
        self.assertEqual(saved['journey']['finish']['review']['status'], 'needs_review')

    def test_journey_gps_sync_preserves_existing_points_and_checks_device(self):
        self.capture()
        old_point = {'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0}
        identity = {'id': 'same-phone', 'source': 'ro.serialno'}
        metadata = self.window.current_metadata
        metadata['journey'] = {'status': 'in_progress', 'gps_status': 'pending', 'device_identity': identity,
                               'start': {'screenshot': self.window.current_path.name}, 'finish': None}
        metadata['track'] = {'session_id': 'a' * 32, 'points': [old_point], 'status': 'pending'}
        ui.write_metadata(self.window.current_path.with_suffix('.json'), metadata)
        with patch('gps_tracker.GpsTracker') as tracker_class:
            tracker = tracker_class.return_value
            tracker.device_identity.return_value = identity
            tracker.read.return_value = {'session_id': 'a' * 32, 'status': 'recording', 'points': [old_point]}
            self.window.sync_journey_gps()
            self.wait_for(lambda: not self.window.busy)
            self.assertEqual(self.window.current_metadata['journey']['gps_status'], 'recording')
            self.assertIn('기록 중', self.window.journey_gps_status.get())
            tracker.read.return_value = {'session_id': 'a' * 32, 'status': 'recording', 'points': []}
            with patch.object(ui.messagebox, 'showerror') as error:
                self.window.sync_journey_gps()
                self.wait_for(lambda: not self.window.busy)
                error.assert_called_once()
        self.assertEqual(self.window.current_metadata['track']['points'], [old_point])

    def test_missing_active_record_does_not_start_new_journey_without_choice(self):
        missing = Path(self.tmp.name) / 'missing_start.json'
        ui.write_metadata(self.window.state_file, {'metadata_path': str(missing)})
        with patch.object(ui.messagebox, 'askyesno', return_value=False) as confirm, \
             patch('journey_service.start_journey') as start:
            self.window.start_journey()
            confirm.assert_called_once()
            start.assert_not_called()
        self.assertTrue(self.window.state_file.is_file())

    def test_original_view_uses_full_image_without_changing_review(self):
        self.capture()
        self.window.distance.set("13 km")
        viewer = self.window.show_original()
        self.assertIsNotNone(viewer)
        canvas = next(child for child in viewer.winfo_children() if isinstance(child, tk.Canvas))
        self.assertEqual((canvas.image.width(), canvas.image.height()), (2, 2))
        self.assertEqual(tuple(map(int, canvas.cget("scrollregion").split())), (0, 0, 2, 2))
        viewer.destroy()
        self.assertEqual(self.window.distance.get(), "13 km")
        self.assertTrue(self.window.save_review())

    def test_original_view_reports_missing_file(self):
        self.capture()
        self.window.current_path.unlink()
        with patch.object(ui.messagebox, "showwarning") as warning:
            self.assertIsNone(self.window.show_original())
        warning.assert_called_once()

    def test_landscape_preview_fits_right_panel_at_minimum_size(self):
        self.window.deiconify()
        self.window.geometry('800x580')
        image = tk.PhotoImage(master=self.window, width=800, height=400)
        path = Path(self.tmp.name) / 'landscape.png'
        image.write(str(path), format='png')
        self.window.current_path = path
        self.window.current_metadata = {'screenshot': path.name, 'app': '네이버 지도'}
        self.window.update()
        self.window._display_image(path, self.window.current_metadata)
        self.window.update()
        self.assertLessEqual(self.window.photo.width(), self.window.preview.winfo_width())
        self.assertLessEqual(self.window.photo.height(), self.window.preview.winfo_height())

    def test_missing_review_field_highlight_clears_without_inserting_placeholder(self):
        self.capture()
        distance_entry = self.window.review_entries[1]
        regular_style = distance_entry.cget('style')
        self.window.distance.set('')
        self.window.update()
        self.assertEqual(self.window.distance.get(), '')
        self.assertNotEqual(distance_entry.cget('style'), regular_style)
        self.window.distance.set('13 km')
        self.window.update()
        self.assertEqual(distance_entry.cget('style'), regular_style)
        self.assertEqual(self.window.distance.get(), '13 km')

    def test_capture_view_tabs_preserve_unsaved_review_and_selected_image(self):
        self.capture()
        self.window.deiconify()
        self.window.geometry('1400x900')
        self.window.update()
        self.window.distance.set('수정 중')
        original = self.window.preview_path
        source = self.window.preview_source
        original_bytes = original.read_bytes()
        self.window.show_capture_view('screenshot')
        self.wait_for(lambda: self.window.large_preview.winfo_ismapped() and self.window.large_photo is not None)
        self.assertTrue(self.window.preview.winfo_ismapped())
        self.assertEqual(str(self.window.large_preview.cget('image')[0]), str(self.window.large_photo))
        self.assertEqual(str(self.window.preview.cget('image')[0]), str(self.window.photo))
        # Both visible previews display the captured red test image.
        for photo in (self.window.photo, self.window.large_photo):
            self.assertEqual(tuple(self.window.tk.call(str(photo), 'get', 0, 0)), (255, 0, 0))
        self.window.show_capture_view('compare')
        self.window.update()
        self.assertFalse(self.window.large_preview.winfo_ismapped())
        self.assertTrue(self.window.start_thumbnail.winfo_ismapped())
        self.assertTrue(self.window.finish_thumbnail.winfo_ismapped())
        self.assertEqual(self.window.preview_path, original)
        self.assertIs(self.window.preview_source, source)
        self.assertEqual(self.window.distance.get(), '수정 중')
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_connection_badge_clears_after_device_goes_offline_or_disappears(self):
        connected_style = self.window.connection_badge.cget('style')
        connected_label = self.window.connection_state.get()
        self.window._show_devices([('192.168.0.2:5555', 'offline')])
        self.window.update()
        self.assertNotEqual(self.window.connection_badge.cget('style'), connected_style)
        self.assertNotEqual(self.window.connection_state.get(), connected_label)
        self.window._show_devices([])
        self.window.update()
        self.assertNotEqual(self.window.connection_badge.cget('style'), connected_style)
        self.assertNotEqual(self.window.connection_state.get(), connected_label)

    def test_landscape_departure_and_arrival_fit_their_panels(self):
        self.window.deiconify()
        self.window.geometry('800x580')
        image = tk.PhotoImage(master=self.window, width=800, height=400)
        start = Path(self.tmp.name) / 'start.png'
        finish = Path(self.tmp.name) / 'finish.png'
        image.write(str(start), format='png')
        image.write(str(finish), format='png')
        self.window.current_path = start
        self.window.current_metadata = {'screenshot': start.name, 'journey': {'status': 'completed',
            'start': {'screenshot': start.name}, 'finish': {'screenshot': finish.name, 'status': 'captured'}}}
        self.window.update()
        self.window._refresh_journey_view()
        self.window.update()
        self.window._resize_preview()
        self.assertLessEqual(self.window.start_thumb_photo.width(), self.window.start_tab.winfo_width() - 24)
        self.assertLessEqual(self.window.finish_thumb_photo.width(), self.window.finish_tab.winfo_width() - 24)

    def test_sheet_preview_updates_after_review_is_saved(self):
        self.capture()
        self.window.distance.set('13 km')
        self.assertTrue(self.window.save_review())
        self.wait_for(lambda: self.window.sheet_data and self.window.sheet_job is None and
                      self.window.sheet_worker is None and self.window.sheet_pending is None)
        sheet = self.window.sheet_data['수집 목록']
        self.assertEqual(sheet['rows'][0][sheet['headers'].index('경로 거리')], '13 km')
        self.assertIn('저장 폴더 1개', self.window.sheet_note.get())

    def test_sheet_preview_discards_stale_background_result(self):
        started, release = threading.Event(), threading.Event()
        def preview(records, limit, query):
            if query == 'old':
                started.set()
                release.wait(4)
            return {'수집 목록': {'headers': ('앱',), 'rows': [(query,)], 'total_rows': 1}}
        try:
            with patch('export_service.preview_sheets', side_effect=preview), \
                 patch('capture_repository.load_capture_records', return_value=[{'app': 'test'}]):
                self.window.sheet_search.set('old')
                self.wait_for(started.is_set)
                self.window.sheet_search.set('new')
                release.set()
                self.wait_for(lambda: self.window.sheet_data.get('수집 목록', {}).get('rows') == [('new',)])
        finally:
            release.set()

    def test_sheet_selector_displays_actual_saved_gps_rows(self):
        self.capture()
        self.window.current_metadata['track'] = {'provider': 'gps', 'status': 'stopped', 'points': [
            {'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0, 'accuracy_m': 4.0}
        ]}
        ui.write_metadata(self.window.current_path.with_suffix('.json'), self.window.current_metadata)
        self.window._schedule_sheet_preview()
        self.wait_for(lambda: self.window.sheet_data and self.window.sheet_job is None and
                      self.window.sheet_worker is None and self.window.sheet_pending is None)
        self.window.sheet_choice.set('실제 이동 궤적')
        self.window.on_sheet_changed()
        rows = self.window.sheet_table.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.window.sheet_table.item(rows[0], 'values')[3:5], ('37.5', '127.0'))

    def test_sheet_filter_and_selected_export_include_requested_columns(self):
        from openpyxl import load_workbook
        self.capture()
        self.window.current_metadata['track'] = {'provider': 'gps', 'status': 'stopped', 'points': [
            {'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0, 'accuracy_m': 4.0},
            {'timestamp': '2026-01-01T00:00:03Z', 'latitude': 38.5, 'longitude': 128.0, 'accuracy_m': 5.0},
        ]}
        ui.write_metadata(self.window.current_path.with_suffix('.json'), self.window.current_metadata)
        self.window.sheet_choice.set('실제 이동 궤적')
        self.window.sheet_search.set('37.5')
        self.wait_for(lambda: self.window.sheet_data and self.window.sheet_job is None and
                      self.window.sheet_worker is None and self.window.sheet_pending is None)
        self.window.on_sheet_changed()
        self.assertEqual(len(self.window.sheet_table.get_children()), 1)
        self.window.sheet_columns['실제 이동 궤적'] = [1, 3]
        output = Path(self.tmp.name) / 'selected.xlsx'
        with patch.object(ui.filedialog, 'asksaveasfilename', return_value=str(output)):
            self.window.export_filtered_data()
            self.wait_for(lambda: not self.window.busy)
        book = load_workbook(output, data_only=True)
        self.assertEqual(book.sheetnames, ['실제 이동 궤적'])
        self.assertEqual(list(book.active.values), [('순번', '위도'), (1, 37.5)])

    def test_default_filtered_export_matches_visible_route_metric_order(self):
        from openpyxl import load_workbook
        self.capture()
        self.window.toll.set('1,400원')
        self.window.estimated_arrival.set('14:53')
        self.assertTrue(self.window.save_review())
        self.wait_for(lambda: self.window.sheet_data and self.window.sheet_job is None and
                      self.window.sheet_worker is None and self.window.sheet_pending is None)
        table = self.window.sheet_table
        displayed = [table.heading(col, 'text') for col in table.cget('displaycolumns')]
        start = displayed.index('예상 소요시간')
        self.assertEqual(displayed[start:start + 3], ['예상 소요시간', '예상 통행료', '도착 예정시간'])
        output = Path(self.tmp.name) / 'visible.xlsx'
        with patch.object(ui.filedialog, 'asksaveasfilename', return_value=str(output)):
            self.window.export_filtered_data()
            self.wait_for(lambda: not self.window.busy)
        book = load_workbook(output, read_only=True)
        try:
            headers, row = list(book.active.values)
            self.assertEqual(list(headers), displayed)
            self.assertEqual(row[start:start + 3], ('5분', '1,400원', '14:53'))
        finally:
            book.close()

    def test_full_export_loads_all_records_in_folder(self):
        from openpyxl import load_workbook
        self.capture()
        second = Path(self.tmp.name) / 'second.json'
        second.write_text(json.dumps({'screenshot': 'second.png', 'app': '카카오맵'}), encoding='utf-8')
        output = Path(self.tmp.name) / 'all.xlsx'
        with patch.object(ui.filedialog, 'asksaveasfilename', return_value=str(output)):
            self.window.export_all_data()
            self.wait_for(lambda: not self.window.busy)
        book = load_workbook(output, data_only=True)
        self.assertEqual(len(book.sheetnames), 4)
        self.assertEqual(book['수집 목록'].max_row, 3)

    def test_selected_export_uses_the_visible_snapshot_when_disk_changes(self):
        from openpyxl import load_workbook
        self.capture()
        self.wait_for(lambda: self.window.sheet_data and self.window.sheet_job is None and
                      self.window.sheet_worker is None and self.window.sheet_pending is None)
        headers = self.window.sheet_data['수집 목록']['headers']
        self.window.sheet_columns['수집 목록'] = [headers.index('경로 거리')]
        path = self.window.current_path.with_suffix('.json')
        data = json.loads(path.read_text(encoding='utf-8'))
        data['ocr']['distance'] = '999 km'
        ui.write_metadata(path, data)
        output = Path(self.tmp.name) / 'snapshot.xlsx'
        with patch.object(ui.filedialog, 'asksaveasfilename', return_value=str(output)):
            self.window.export_filtered_data()
            self.wait_for(lambda: not self.window.busy)
        self.assertEqual(list(load_workbook(output, data_only=True).active.values), [('경로 거리',), ('12 km',)])

    def test_selected_export_waits_for_preview_after_saving_edits(self):
        self.capture()
        self.wait_for(lambda: self.window.sheet_data and self.window.sheet_job is None and
                      self.window.sheet_worker is None and self.window.sheet_pending is None)
        self.window.distance.set('13 km')
        with patch.object(ui.messagebox, 'askyesno', return_value=True), \
             patch.object(ui.filedialog, 'asksaveasfilename') as dialog:
            self.window.export_filtered_data()
            dialog.assert_not_called()
        self.wait_for(lambda: self.window.sheet_data and self.window.sheet_job is None and
                      self.window.sheet_worker is None and self.window.sheet_pending is None)
        sheet = self.window.sheet_data['수집 목록']
        self.assertEqual(sheet['rows'][0][sheet['headers'].index('경로 거리')], '13 km')

    def test_preview_identifies_open_record_outside_selected_folder(self):
        self.capture()
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        self.window.output_dir.set(str(other))
        self.wait_for(lambda: self.window.sheet_data and self.window.sheet_job is None and
                      self.window.sheet_worker is None and self.window.sheet_pending is None)
        self.assertEqual(self.window.sheet_data['수집 목록']['total_rows'], 1)
        self.assertIn('다른 폴더의 열린 기록', self.window.sheet_note.get())

    def test_discovery_fills_candidate_without_connecting(self):
        found = [{'instance': 'adb-test-X', 'kind': 'connect', 'endpoint': '192.0.2.5:4455',
                  'host': '192.0.2.5', 'port': 4455}]
        with patch.object(self.window.adb, 'discover_wireless', create=True, return_value=found), \
             patch.object(self.window.adb, 'connect', create=True) as connect:
            self.window.discover_wireless()
            self.wait_for(lambda: not self.window.busy)
            connect.assert_not_called()
        self.assertEqual(self.window.host.get(), '')
        self.window.discovered_selector.current(0)
        self.window.on_discovery_selected()
        self.assertEqual(self.window.host.get(), '192.0.2.5')
        self.assertEqual(self.window.port.get(), '4455')

    def test_discovery_connects_and_selects_matching_paired_usb_device(self):
        found = [{'instance': 'adb-USB123-X', 'kind': 'connect', 'endpoint': '192.0.2.5:4455',
                  'host': '192.0.2.5', 'port': 4455}]
        devices = [('USB123', 'device'), ('192.0.2.5:4455', 'device')]
        self.window.devices = [('USB123', 'device')]
        self.window.device_box['values'] = ['USB123  [device]']
        self.window.device_box.current(0)
        identity = {'id': 'same', 'source': 'ro.serialno'}
        with patch.object(self.window.adb, 'discover_wireless', create=True, return_value=found), \
             patch.object(self.window.adb, 'list_devices', return_value=devices), \
             patch.object(self.window.adb, 'connect', create=True, return_value='connected') as connect, \
             patch('gps_tracker.GpsTracker') as tracker:
            tracker.return_value.device_identity.return_value = identity
            self.window.discover_wireless()
            self.wait_for(lambda: not self.window.busy and self.window.selected_serial() == '192.0.2.5:4455')
            connect.assert_called_once_with('192.0.2.5:4455')
        self.assertIn('자동 연결', self.window.discovery_status.get())

    def test_discovery_does_not_select_a_different_hardware_device(self):
        found = [{'instance': 'adb-USB123-X', 'kind': 'connect', 'endpoint': '192.0.2.5:4455',
                  'host': '192.0.2.5', 'port': 4455}]
        self.window.devices = [('USB123', 'device')]
        self.window.device_box['values'] = ['USB123  [device]']
        self.window.device_box.current(0)
        with patch.object(self.window.adb, 'discover_wireless', create=True, return_value=found), \
             patch.object(self.window.adb, 'list_devices', side_effect=[[('USB123', 'device')], [('USB123', 'device'), ('192.0.2.5:4455', 'device')]]), \
             patch.object(self.window.adb, 'connect', create=True, return_value='connected'), \
             patch.object(self.window.adb, 'disconnect', create=True) as disconnect, \
             patch('gps_tracker.GpsTracker') as tracker:
            tracker.return_value.device_identity.side_effect = [
                {'id': 'first', 'source': 'ro.serialno'}, {'id': 'other', 'source': 'ro.serialno'}]
            self.window.discover_wireless()
            self.wait_for(lambda: not self.window.busy)
            disconnect.assert_called_once_with('192.0.2.5:4455')
        self.assertEqual(self.window.selected_serial(), 'USB123')

    def test_discovery_does_not_prefill_single_unmatched_usb_candidate(self):
        found = [{'instance': 'adb-OTHER-X', 'kind': 'pairing', 'endpoint': '192.0.2.6:4477',
                  'host': '192.0.2.6', 'port': 4477}]
        self.window.devices = [('USB123', 'device')]
        self.window.device_box['values'] = ['USB123  [device]']
        self.window.device_box.current(0)
        with patch.object(self.window.adb, 'discover_wireless', create=True, return_value=found):
            self.window.discover_wireless()
            self.wait_for(lambda: not self.window.busy)
        self.assertEqual(self.window.host.get(), '')
        self.window.discovered_selector.current(0)
        with patch.object(ui.messagebox, 'askyesno', return_value=False):
            self.window.on_discovery_selected()
        self.assertEqual(self.window.host.get(), '')

    def test_reports_unauthorized_and_offline_devices(self):
        self.window._show_devices([("usb-1", "unauthorized")])
        self.assertIn("승인 대기", self.window.device_detail.get())
        self.assertIn("USB 디버깅", self.window.device_detail.get())
        self.window.device_box.current(0)
        self.window.inspect_device()
        self.assertIn("승인 대기", self.window.device_detail.get())
        self.window._show_devices([("usb-1", "offline")])
        self.assertIn("오프라인", self.window.device_detail.get())

    def test_pasted_pairing_address_does_not_replace_connect_port(self):
        self.window.host.set("192.168.0.2:39214")
        self.window.pair_port.set("")
        self.window.port.set("43779")
        self.window.pair_code.set("123456")
        with patch.object(self.window.adb, "pair", create=True, return_value="Successfully paired") as pair:
            self.window.pair_device()
            self.wait_for(lambda: not self.window.busy)
        pair.assert_called_once_with("192.168.0.2:39214", "123456")
        self.assertEqual(self.window.host.get(), "192.168.0.2")
        self.assertEqual(self.window.pair_port.get(), "39214")
        self.assertEqual(self.window.port.get(), "43779")

    def test_close_waits_for_running_and_followup_work(self):
        gate = threading.Event()
        completed = []
        def first_done(value):
            self.window._run("후속 작업", lambda: "complete", completed.append)
        self.window._run("작업", lambda: gate.wait(5), first_done)
        try:
            with patch.object(ui.messagebox, "askyesno", return_value=True), \
                 patch.object(self.window, "destroy") as destroy:
                self.assertFalse(self.window._close())
                self.assertFalse(destroy.called)
                self.assertTrue(self.window.close_when_done)
                gate.set()
                self.wait_for(lambda: destroy.called)
                self.assertEqual(completed, ["complete"])
                destroy.assert_called_once()
        finally:
            gate.set()


if __name__ == "__main__":
    unittest.main()
