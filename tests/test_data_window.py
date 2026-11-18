"""Exercise coordinate edits and exporting using a temporary capture."""
import json
import gc
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app import write_metadata
from data_window import DataWindow


class CoordinateWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'GUI display unavailable: {exc}')
        self.root.withdraw()
        self.window = None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.cleanup_gui)
        self.path = Path(self.tmp.name) / 'screen.png'
        self.root.current_path = self.path
        self.root.adb = object()
        self.root.selected_serial = lambda: 'test-device'
        self.root.current_metadata = {'app': '티맵', 'screenshot': self.path.name,
                                      'review': {'status': 'reviewed', 'distance': '2km'}}
        write_metadata(self.path.with_suffix('.json'), self.root.current_metadata)
        self.window = DataWindow(self.root, write_metadata)
        self.window.withdraw()
        for key, value in {'origin_lat': '37.5', 'origin_lon': '127.0',
                           'destination_lat': '37.6', 'destination_lon': '127.1'}.items():
            self.window.values[key].set(value)

    def cleanup_gui(self):
        window, self.window = self.window, None
        root, self.root = self.root, None
        if window is not None:
            if window.worker is not None:
                window.worker.join(timeout=30)
            window.destroy()
        root.destroy()
        del window, root
        gc.collect()

    def wait(self):
        deadline = time.monotonic() + 10
        while self.window.worker and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertIsNone(self.window.worker)

    def test_save_manual_endpoints_preserves_existing_review(self):
        self.window.key.set('DO_NOT_PERSIST')
        self.window.save_endpoints()
        metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['route']['origin']['lat'], 37.5)
        self.assertEqual(metadata['route']['destination']['source'], 'user_input')
        self.assertEqual(metadata['route']['points'], [])
        self.assertEqual(metadata['review']['distance'], '2km')
        self.assertNotIn('DO_NOT_PERSIST', json.dumps(metadata))

    def test_use_track_endpoints_requires_two_points_without_changing_inputs(self):
        self.window._save_field('track', {'status': 'recording', 'points': [
            {'latitude': 37.51, 'longitude': 127.02},
        ]})
        before = {name: value.get() for name, value in self.window.values.items()}
        with patch('data_window.messagebox.showwarning') as warning:
            self.window.use_track_endpoints()
        warning.assert_called_once()
        self.assertEqual({name: value.get() for name, value in self.window.values.items()}, before)

    def test_use_track_endpoints_saves_first_last_with_gps_sources(self):
        self.window.values['origin_name'].set('이전 출발지 이름')
        self.window.values['destination_name'].set('이전 목적지 이름')
        points = [{'latitude': 37.51001, 'longitude': 127.02001},
                  {'latitude': 37.52, 'longitude': 127.03},
                  {'latitude': 37.53003, 'longitude': 127.04003}]
        self.window._save_field('track', {'status': 'stopped', 'points': points})
        self.window.use_track_endpoints()
        self.assertEqual(self.window.values['origin_name'].get(), '')
        self.assertEqual(self.window.values['destination_name'].get(), '')
        self.window.save_endpoints()
        metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['route']['origin']['lat'], points[0]['latitude'])
        self.assertEqual(metadata['route']['origin']['lon'], points[0]['longitude'])
        self.assertEqual(metadata['route']['origin']['source'], 'gps_track_first')
        self.assertEqual(metadata['route']['destination']['lat'], points[-1]['latitude'])
        self.assertEqual(metadata['route']['destination']['lon'], points[-1]['longitude'])
        self.assertEqual(metadata['route']['destination']['source'], 'gps_track_last')
        self.assertEqual(metadata['track']['points'], points)

    def test_search_choice_preserves_source_and_does_not_auto_select(self):
        self.window.values['origin_name'].set('검색어')
        place = {'name': '장소', 'address': '주소', 'lat': 37.51, 'lon': 127.02, 'source': 'provider_search'}
        with patch('route_service.search_places', return_value=[place]):
            self.window.search('origin')
            self.wait()
        self.assertEqual(self.window.values['origin_lat'].get(), '37.5')
        self.window.select_place('origin', 0)
        self.window.save_endpoints()
        metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['route']['origin']['source'], 'provider_search')
        self.assertEqual(metadata['route']['origin']['address'], '주소')

    def test_route_query_saves_result_and_never_stores_key(self):
        self.window.key.set('DO_NOT_PERSIST')
        route = {'provider': 'tmap', 'source': 'official_directions_api_recomputed',
                 'matching': 'unverified', 'origin': {'lat': 37.5, 'lon': 127.0},
                 'destination': {'lat': 37.6, 'lon': 127.1}, 'points': [{'lat': 37.5, 'lon': 127.0}]}
        with patch('data_window.fetch_route', return_value=route):
            self.window.fetch()
            self.wait()
        metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['route']['matching'], 'unverified')
        self.assertEqual(len(metadata['route']['points']), 1)
        self.assertNotIn('DO_NOT_PERSIST', json.dumps(metadata))

    def test_tracking_persists_session_before_request_and_syncs_points(self):
        points = [{'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0,
                   'accuracy_m': 4.0, 'altitude_m': None, 'provider': 'gps'}]
        with patch('gps_tracker.GpsTracker') as tracker_class:
            tracker = tracker_class.return_value
            tracker.device_identity.return_value = {'id': 'test-device', 'source': 'transport'}
            tracker.read.return_value = {'status': 'pending', 'points': []}
            def start(serial, session):
                metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
                self.assertEqual(metadata['track']['session_id'], session)
                return session
            tracker.start.side_effect = start
            self.window.start_tracker()
            self.wait()
            metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
            session = metadata['track']['session_id']
            tracker.read.return_value = {'session_id': session, 'status': 'recording', 'provider': 'gps',
                                         'points': points, 'started_at': '2026-01-01T00:00:00Z'}
            self.window.sync_tracker()
            self.wait()
        metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['track']['points'], points)
        self.assertEqual(metadata['track']['device_serial'], 'test-device')
        self.assertEqual(metadata['review']['distance'], '2km')

    def test_tracking_rejects_replacing_saved_points_with_shorter_journal(self):
        existing = {'session_id': 'a' * 32, 'status': 'recording',
                    'points': [{'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0}]}
        self.window._save_field('track', existing)
        with patch('gps_tracker.GpsTracker') as tracker_class, patch('data_window.messagebox.showerror') as error:
            tracker_class.return_value.device_identity.return_value = {'id': 'test-device', 'source': 'transport'}
            tracker_class.return_value.read.return_value = {'session_id': 'a' * 32, 'status': 'stopped', 'points': []}
            self.window.sync_tracker()
            self.wait()
            error.assert_called_once()
        metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['track'], existing)

    def test_inputs_are_disabled_during_background_work(self):
        gate = threading.Event()
        self.window._run('작업 중', lambda: gate.wait(5), lambda _: None)
        self.assertTrue(self.window.origin_choices.instate(['disabled']))
        gate.set()
        self.wait()
        self.assertFalse(self.window.origin_choices.instate(['disabled']))

    def test_different_device_cannot_stop_or_overwrite_track(self):
        existing = {'session_id': 'a' * 32, 'status': 'recording', 'points': [],
                    'device_identity': {'id': 'original', 'source': 'ro.serialno'}}
        self.window._save_field('track', existing)
        with patch('gps_tracker.GpsTracker') as tracker_class, patch('data_window.messagebox.showerror') as error:
            tracker = tracker_class.return_value
            tracker.device_identity.return_value = {'id': 'other', 'source': 'ro.serialno'}
            self.window.stop_tracker()
            self.wait()
            tracker.stop.assert_not_called()
            error.assert_called_once()
        metadata = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['track'], existing)

    def test_manual_track_keeps_active_journey_gps_state_in_sync(self):
        metadata = self.window._read()
        metadata['journey'] = {'status': 'in_progress', 'gps_status': 'disabled'}
        write_metadata(self.path.with_suffix('.json'), metadata)
        self.window._save_field('track', {'session_id': 'a' * 32, 'status': 'recording', 'points': []})
        self.assertEqual(self.window._read()['journey']['gps_status'], 'recording')
        self.window._save_field('track', {'session_id': 'a' * 32, 'status': 'stopped', 'points': [], 'started_at': '2026-01-01T00:00:00Z'})
        self.assertEqual(self.window._read()['journey']['gps_status'], 'no_fix')

    def test_completed_journey_rejects_new_gps_recording(self):
        metadata = self.window._read()
        metadata['journey'] = {'status': 'completed', 'gps_status': 'disabled'}
        write_metadata(self.path.with_suffix('.json'), metadata)
        with patch('data_window.messagebox.showwarning') as warning, patch('gps_tracker.GpsTracker') as tracker:
            self.window.start_tracker()
            warning.assert_called_once()
            tracker.assert_not_called()
        self.assertNotIn('track', self.window._read())

    def test_arrival_started_rejects_new_gps_recording(self):
        metadata = self.window._read()
        metadata['journey'] = {'status': 'needs_attention', 'gps_status': 'error',
                               'finish': {'status': 'captured', 'screenshot': 'finish.png'}}
        write_metadata(self.path.with_suffix('.json'), metadata)
        with patch('data_window.messagebox.showwarning') as warning, patch('gps_tracker.GpsTracker') as tracker:
            self.window.start_tracker()
            warning.assert_called_once()
            tracker.assert_not_called()
        self.assertNotIn('track', self.window._read())

    def test_active_journey_cannot_start_gps_on_different_phone(self):
        metadata = self.window._read()
        metadata['journey'] = {'status': 'in_progress', 'gps_status': 'disabled',
                               'device_identity': {'id': 'original', 'source': 'ro.serialno'}}
        write_metadata(self.path.with_suffix('.json'), metadata)
        with patch('gps_tracker.GpsTracker') as tracker_class, patch('data_window.messagebox.showerror') as error:
            tracker = tracker_class.return_value
            tracker.device_identity.return_value = {'id': 'other', 'source': 'ro.serialno'}
            self.window.start_tracker()
            self.wait()
            tracker.start.assert_not_called()
            error.assert_called_once()
        self.assertNotIn('track', self.window._read())

    def test_current_capture_exports_endpoint_coordinates(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest('openpyxl is not installed')
        self.window.save_endpoints()
        output = Path(self.tmp.name) / 'result.xlsx'
        with patch('data_window.filedialog.asksaveasfilename', return_value=str(output)):
            self.window.export(False)
            self.wait()
        workbook = load_workbook(output, data_only=True)
        self.assertEqual(workbook['출발 목적지']['D2'].value, 37.5)
        self.assertEqual(workbook['출발 목적지']['E3'].value, 127.1)


if __name__ == '__main__':
    unittest.main()
