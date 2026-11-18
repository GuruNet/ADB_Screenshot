import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from adb_service import AdbError, PNG_END, PNG_START
from app import write_metadata
from journey_service import JourneyError, finish_journey, start_journey
from ocr_service import OCRError


PNG = PNG_START + b'fixture' + PNG_END


class FakeAdb:
    def __init__(self):
        self.calls = []
        self.fail_capture = False

    def identify_app(self, serial):
        return '티맵', 'com.skt.tmap.ku'

    def capture_screenshot(self, serial, path):
        self.calls.append(Path(path))
        if self.fail_capture:
            raise AdbError('기기 연결 끊김')
        Path(path).write_bytes(PNG)


class FakeTracker:
    def __init__(self):
        self.identity = {'id': 'phone', 'source': 'ro.serialno'}
        self.start = Mock()
        self.read = Mock(return_value={'status': 'recording', 'points': []})
        self.stop = Mock(side_effect=lambda serial, session: {'session_id': session, 'status': 'stopped',
                                                               'points': [], 'provider': 'gps'})

    def device_identity(self, serial):
        return self.identity


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.adb = FakeAdb()
        self.tracker = FakeTracker()
        self.ocr = Mock(return_value={'screen_time': '09:00', 'distance': '5km', 'duration': '15분', 'raw_text': ''})

    def start(self, gps=False):
        return start_journey(self.adb, 'usb', self.tmp.name, write_metadata, enable_gps=gps,
                             recognizer=self.ocr, tracker=self.tracker)

    def finish(self, image, **kwargs):
        return finish_journey(self.adb, 'usb', image.with_suffix('.json'), write_metadata,
                              recognizer=self.ocr, tracker=self.tracker, **kwargs)

    def test_screenshots_are_paired_and_old_fields_retained(self):
        start, metadata = self.start()
        self.assertEqual(metadata['screenshot'], start.name)
        self.assertEqual(metadata['journey']['gps_status'], 'disabled')
        finish, metadata = self.finish(start)
        self.assertTrue(start.is_file() and finish.is_file())
        self.assertNotEqual(start, finish)
        self.assertEqual(metadata['journey']['status'], 'completed')
        self.assertEqual(metadata['journey']['app_match'], 'confirmed')
        self.assertEqual(metadata['journey']['finish']['ocr']['duration'], '15분')
        self.assertEqual(metadata['journey']['finish']['ocr']['estimated_arrival'], '')
        self.assertEqual(metadata['ocr']['distance'], '5km')
        self.assertEqual(len(self.adb.calls), 2)
        self.finish(start)
        self.assertEqual(len(self.adb.calls), 2)

    def test_start_saves_session_before_request_and_failure_keeps_screenshot(self):
        def start_request(serial, session):
            records = list(Path(self.tmp.name).glob('*_start.json'))
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0].read_text())['track']['session_id'], session)
            raise AdbError('보조 앱 없음')
        self.tracker.start.side_effect = start_request
        image, metadata = self.start(gps=True)
        self.assertTrue(image.is_file())
        self.assertEqual(metadata['journey']['gps_status'], 'error')
        self.assertIn('보조 앱 없음', metadata['track']['error'])

    def test_recovery_index_is_written_before_gps_request(self):
        calls = []
        def on_saved(path):
            record = json.loads(path.read_text())
            self.assertEqual(record['journey']['status'], 'in_progress')
            self.assertFalse(self.tracker.start.called)
            calls.append(path)
        image, _ = start_journey(self.adb, 'usb', self.tmp.name, write_metadata, enable_gps=True,
                                  recognizer=self.ocr, tracker=self.tracker, on_saved=on_saved)
        self.assertEqual(calls, [image.with_suffix('.json')])
        self.tracker.start.assert_called_once()

    def test_failed_recovery_index_does_not_start_gps_or_delete_capture(self):
        def fail(path):
            raise OSError('disk full')
        with self.assertRaisesRegex(JourneyError, '출발 기록'):
            start_journey(self.adb, 'usb', self.tmp.name, write_metadata, enable_gps=True,
                          recognizer=self.ocr, tracker=self.tracker, on_saved=fail)
        self.tracker.start.assert_not_called()
        self.assertEqual(len(list(Path(self.tmp.name).glob('*_start.png'))), 1)
        self.assertEqual(len(list(Path(self.tmp.name).glob('*_start.json'))), 1)

    def test_finish_stops_and_preserves_points(self):
        start, metadata = self.start(gps=True)
        point = {'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0}
        metadata['track']['points'] = [point]
        write_metadata(start.with_suffix('.json'), metadata)
        self.tracker.stop.side_effect = lambda serial, session: {'session_id': session, 'status': 'stopped',
                                                                  'started_at': '2026-01-01T00:00:00Z',
                                                                  'points': [point], 'provider': 'gps'}
        _, metadata = self.finish(start)
        self.assertEqual(metadata['journey']['gps_status'], 'stopped')
        self.assertEqual(metadata['track']['points'], [point])
        self.assertEqual(metadata['journey']['status'], 'completed')

    def test_arrival_stops_gps_before_ocr(self):
        start, _ = self.start(gps=True)
        order = []
        def stop(serial, session):
            order.append('stop')
            return {'session_id': session, 'status': 'stopped', 'started_at': '2026-01-01T00:00:00Z',
                    'points': [{'timestamp': '2026-01-01T00:00:01Z', 'latitude': 37.5, 'longitude': 127.0}]}
        def ocr(path):
            order.append('ocr')
            return {'screen_time': '10:00'}
        self.tracker.stop.side_effect = stop
        self.ocr.side_effect = ocr
        _, record = self.finish(start)
        self.assertEqual(order, ['stop', 'ocr'])
        self.assertEqual(record['journey']['finish']['ocr']['screen_time'], '10:00')

    def test_gps_failure_does_not_prevent_arrival_ocr(self):
        start, _ = self.start(gps=True)
        self.tracker.stop.side_effect = AdbError('통신 실패')
        finish, record = self.finish(start)
        self.assertTrue(finish.exists())
        self.assertEqual(record['journey']['status'], 'needs_attention')
        self.assertEqual(record['journey']['finish']['ocr']['duration'], '15분')

    def test_already_stopped_does_not_issue_second_stop(self):
        start, metadata = self.start(gps=True)
        self.tracker.read.return_value = {'session_id': metadata['track']['session_id'],
                                          'status': 'stopped', 'points': [], 'stopped_at': 'original'}
        _, result = self.finish(start)
        self.tracker.stop.assert_not_called()
        self.assertEqual(result['track']['stopped_at'], 'original')

    def test_finish_failure_preserves_image_and_retry_reuses_it(self):
        start, _ = self.start(gps=True)
        self.tracker.stop.side_effect = AdbError('기기 연결 끊김')
        finish, record = self.finish(start)
        self.assertEqual(record['journey']['status'], 'needs_attention')
        self.assertEqual(record['journey']['gps_status'], 'error')
        self.assertEqual(finish.read_bytes(), PNG)
        self.tracker.read.return_value = {'status': 'stopped', 'started_at': '2026-01-01T00:00:00Z',
                                          'points': [{'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0}]}
        _, record = self.finish(start)
        self.assertEqual(record['journey']['status'], 'completed')
        self.assertEqual(len(self.adb.calls), 2)

    def test_unapproved_gps_is_not_reported_as_successfully_recorded(self):
        start, record = self.start(gps=True)
        self.tracker.read.return_value = {'session_id': record['track']['session_id'], 'status': 'stopped',
                                          'started_at': None, 'points': []}
        finish, result = self.finish(start)
        self.assertTrue(finish.exists())
        self.assertEqual(result['journey']['status'], 'completed')
        self.assertEqual(result['journey']['gps_status'], 'not_started')

    def test_started_gps_without_a_fix_is_distinguished(self):
        start, record = self.start(gps=True)
        self.tracker.read.return_value = {'session_id': record['track']['session_id'], 'status': 'stopped',
                                          'started_at': '2026-01-01T00:00:00Z', 'points': []}
        finish, result = self.finish(start)
        self.assertTrue(finish.exists())
        self.assertEqual(result['journey']['status'], 'completed')
        self.assertEqual(result['journey']['gps_status'], 'no_fix')

    def test_missing_requested_gps_session_is_not_reported_as_success(self):
        start, record = self.start(gps=True)
        del record['track']
        write_metadata(start.with_suffix('.json'), record)
        finish, result = self.finish(start)
        self.assertTrue(finish.exists())
        self.assertEqual(result['journey']['status'], 'needs_attention')
        self.assertEqual(result['journey']['gps_status'], 'error')
        self.assertIn('세션', result['journey']['gps_error'])

    def test_manual_tracking_is_stopped_even_if_departure_gps_was_disabled(self):
        start, record = self.start(gps=False)
        record['track'] = {'session_id': 'a' * 32, 'status': 'recording', 'points': []}
        write_metadata(start.with_suffix('.json'), record)
        self.tracker.read.return_value = {'session_id': 'a' * 32, 'status': 'recording', 'points': []}
        point = {'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0}
        self.tracker.stop.side_effect = None
        self.tracker.stop.return_value = {'session_id': 'a' * 32, 'status': 'stopped',
                                          'started_at': '2026-01-01T00:00:00Z', 'points': [point]}
        _, result = self.finish(start)
        self.tracker.stop.assert_called_once_with('usb', 'a' * 32)
        self.assertEqual(result['journey']['gps_status'], 'stopped')
        self.assertEqual(result['track']['points'], [point])

    def test_disabled_gps_with_track_but_no_session_requires_attention(self):
        start, record = self.start(gps=False)
        record['track'] = {'status': 'recording', 'points': []}
        write_metadata(start.with_suffix('.json'), record)
        _, result = self.finish(start)
        self.assertEqual(result['journey']['status'], 'needs_attention')
        self.assertEqual(result['journey']['gps_status'], 'error')

    def test_stale_stopped_flag_does_not_ignore_recording_track(self):
        start, record = self.start(gps=True)
        record['journey']['gps_status'] = 'stopped'
        record['track']['status'] = 'recording'
        write_metadata(start.with_suffix('.json'), record)
        self.tracker.read.return_value = {'session_id': record['track']['session_id'], 'status': 'recording', 'points': []}
        _, result = self.finish(start)
        self.tracker.stop.assert_called_once()
        self.assertEqual(result['journey']['gps_status'], 'not_started')

    def test_existing_gps_points_are_not_replaced_by_shorter_journal(self):
        start, record = self.start(gps=True)
        point = {'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0}
        record['track']['points'] = [point]
        write_metadata(start.with_suffix('.json'), record)
        self.tracker.read.return_value = {'status': 'stopped', 'points': []}
        finish, record = self.finish(start)
        self.assertEqual(record['journey']['status'], 'needs_attention')
        self.assertEqual(record['track']['points'], [point])
        self.assertTrue(finish.is_file())

    def test_wrong_device_does_not_capture_arrival(self):
        start, _ = self.start()
        self.tracker.identity = {'id': 'other-phone', 'source': 'ro.serialno'}
        with self.assertRaisesRegex(JourneyError, 'GPS는 종료되지 않았습니다'):
            self.finish(start)
        self.assertEqual(len(self.adb.calls), 1)

    def test_missing_record_explains_gps_may_need_manual_stop(self):
        with self.assertRaisesRegex(JourneyError, '휴대폰 알림에서 직접 중지'):
            finish_journey(self.adb, 'usb', Path(self.tmp.name) / 'missing.json', write_metadata,
                           recognizer=self.ocr, tracker=self.tracker)
        self.tracker.stop.assert_not_called()

    def test_mismatched_track_identity_never_stops_another_phone(self):
        start, record = self.start(gps=True)
        record['track']['device_identity'] = {'id': 'other-phone', 'source': 'ro.serialno'}
        write_metadata(start.with_suffix('.json'), record)
        finish, result = self.finish(start)
        self.assertTrue(finish.exists())
        self.tracker.read.assert_not_called()
        self.tracker.stop.assert_not_called()
        self.assertEqual(result['journey']['status'], 'needs_attention')
        self.assertIn('기기', result['journey']['gps_error'])

    def test_different_arrival_app_is_recorded_as_mismatch(self):
        start, _ = self.start()
        self.adb.identify_app = lambda serial: ('카카오맵', 'net.daum.android.map')
        finish, record = self.finish(start)
        self.assertTrue(finish.exists())
        self.assertEqual(record['journey']['app_match'], 'mismatch')
        self.assertEqual(record['app'], '티맵')
        self.assertEqual(record['journey']['finish']['app'], '카카오맵')

    def test_unknown_foreground_app_is_not_claimed_as_same(self):
        start, _ = self.start()
        self.adb.identify_app = lambda serial: (None, None)
        _, record = self.finish(start)
        self.assertEqual(record['journey']['app_match'], 'unknown')
        self.assertEqual(record['journey']['finish']['app_detection'], 'uncertain')

    def test_failed_capture_can_be_retried(self):
        start, _ = self.start()
        self.adb.fail_capture = True
        with self.assertRaises(AdbError):
            self.finish(start)
        record = json.loads(start.with_suffix('.json').read_text())
        self.assertEqual(record['journey']['finish']['status'], 'capturing')
        self.adb.fail_capture = False
        finish, record = self.finish(start)
        self.assertTrue(finish.is_file())
        self.assertEqual(record['journey']['status'], 'completed')

    def test_crash_after_capture_recovers_existing_image(self):
        start, _ = self.start()
        attempts = 0
        def writer(path, value):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise OSError('disk full')
            write_metadata(path, value)
        with self.assertRaisesRegex(JourneyError, '보존'):
            finish_journey(self.adb, 'usb', start.with_suffix('.json'), writer,
                           recognizer=self.ocr, tracker=self.tracker)
        finish, record = self.finish(start)
        self.assertEqual(finish.read_bytes(), PNG)
        self.assertTrue(record['journey']['finish']['recovered'])
        self.assertEqual(len(self.adb.calls), 2)

    def test_disk_failure_still_stops_gps_and_preserves_captured_image_for_retry(self):
        start, record = self.start(gps=True)
        session = record['track']['session_id']
        point = {'timestamp': '2026-01-01T00:00:00Z', 'latitude': 37.5, 'longitude': 127.0}
        self.tracker.stop.side_effect = None
        self.tracker.stop.return_value = {'session_id': session, 'status': 'stopped',
                                          'started_at': '2026-01-01T00:00:00Z', 'points': [point]}
        writes = 0
        def writer(path, metadata):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError('disk full')
            write_metadata(path, metadata)
        with self.assertRaisesRegex(JourneyError, 'GPS 종료 상태를 확인'):
            finish_journey(self.adb, 'usb', start.with_suffix('.json'), writer,
                           recognizer=self.ocr, tracker=self.tracker)
        self.tracker.stop.assert_called_once_with('usb', session)
        self.assertEqual(len(self.adb.calls), 2)
        self.tracker.read.return_value = self.tracker.stop.return_value
        image, recovered = self.finish(start)
        self.assertEqual(len(self.adb.calls), 2)
        self.assertTrue(image.exists())
        self.assertEqual(recovered['track']['points'], [point])
        self.assertEqual(recovered['journey']['status'], 'completed')

    def test_failed_capture_and_unreachable_gps_gives_manual_stop_guidance(self):
        start, _ = self.start(gps=True)
        self.adb.fail_capture = True
        self.tracker.read.side_effect = AdbError('연결 끊김')
        self.tracker.stop.side_effect = AdbError('연결 끊김')
        with self.assertRaisesRegex(JourneyError, '휴대폰 알림에서 직접 중지'):
            self.finish(start)
        self.assertTrue(start.exists())
        record = json.loads(start.with_suffix('.json').read_text())
        self.assertEqual(record['journey']['finish']['status'], 'capturing')

    def test_failed_capture_never_stops_mismatched_track(self):
        start, record = self.start(gps=True)
        record['track']['device_identity'] = {'id': 'other-phone', 'source': 'ro.serialno'}
        write_metadata(start.with_suffix('.json'), record)
        self.adb.fail_capture = True
        with self.assertRaisesRegex(JourneyError, '원래 휴대폰 알림'):
            self.finish(start)
        self.tracker.stop.assert_not_called()

    def test_ocr_failure_does_not_discard_screenshots(self):
        self.ocr.side_effect = OCRError('OCR 없음')
        start, record = self.start()
        self.assertEqual(record['ocr']['status'], 'failed')
        finish, record = self.finish(start)
        self.assertTrue(start.exists() and finish.exists())
        self.assertEqual(record['journey']['finish']['ocr']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
