"""Excel export of screenshots, routes and recorded GPS without real user data."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from export_service import ExportError, build_workbook, export_xlsx, export_filtered_xlsx, preview_sheets


@unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl is not installed")
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "결과.xlsx"
        self.record = {
            "app": "티맵", "captured_at": "2026-11-17T21:05:00+09:00", "screenshot": "sample.png",
            "ocr": {"screen_time": "21:05", "distance": "7.5km", "duration": "46분"},
            "review": {"status": "reviewed", "screen_time": "21:05", "distance": "7.6km", "duration": "46분"},
            "route": {
                "provider": "tmap", "source": "official_directions_api_recomputed", "matching": "unverified",
                "requested_at": "2026-11-17T21:05:05+09:00", "coordinate_system": "WGS84",
                "option": "0", "travel_mode": "driving",
                "origin": {"name": "출발", "lat": 37.50001, "lon": 127.10001},
                "destination": {"name": "=HYPERLINK(\"https://example.invalid\")", "lat": 37.51, "lon": 127.11},
                "points": [{"lat": 37.50001, "lon": 127.10001}, {"lat": 37.51, "lon": 127.11}],
                "waypoints": [{"lat": 37.505, "lon": 127.105}],
            },
            "track": {
                "status": "stopped", "provider": "gps", "session_id": "session-1",
                "started_at": "2026-11-17T12:05:59Z", "stopped_at": "2026-11-17T12:06:06Z",
                "points": [
                    {"timestamp": "2026-11-17T12:06:00Z", "latitude": 37.500001, "longitude": 127.100001,
                     "accuracy_m": 5.5, "altitude_m": 15.1},
                    {"timestamp": "2026-11-17T12:06:05Z", "latitude": 37.500005, "longitude": 127.100008,
                     "accuracy_m": 6, "altitude_m": None},
                ],
            },
        }

    def test_multiple_records_keep_source_order_and_literal_strings(self):
        from openpyxl import load_workbook

        original = json.dumps(self.record, ensure_ascii=False, sort_keys=True)
        older = {"screenshot": "old.png", "ocr": {"distance": "1km"}}
        self.assertEqual(export_xlsx([self.record, older], self.path), self.path)
        self.assertEqual(json.dumps(self.record, ensure_ascii=False, sort_keys=True), original)
        book = load_workbook(self.path, data_only=False)
        self.assertEqual(book.sheetnames, ["수집 목록", "출발 목적지", "추천 경로 좌표", "실제 이동 궤적"])
        summary = list(book["수집 목록"].values)
        self.assertEqual(summary[1][0], 1)
        self.assertEqual(summary[1][5], "7.6km")
        self.assertEqual(summary[1][9:14], ("tmap", "official_directions_api_recomputed", "unverified", "2026-11-17T21:05:05+09:00", "WGS84"))
        self.assertEqual(summary[1][21:24], ("session-1", "2026-11-17T12:05:59Z", "2026-11-17T12:06:06Z"))
        self.assertEqual(summary[2][0:4], (2, None, None, "old.png"))
        self.assertEqual(summary[2][5], "1km")
        endpoints = list(book["출발 목적지"].values)
        self.assertEqual([row[1] for row in endpoints[1:]], ["출발지", "경유지", "목적지"])
        self.assertEqual(endpoints[1][6], "경로 요청 입력값")
        destination = book["출발 목적지"]["C4"]
        self.assertEqual(destination.data_type, "s")
        self.assertTrue(destination.value.startswith("=HYPERLINK"))
        route = list(book["추천 경로 좌표"].values)
        self.assertEqual([(row[1], row[2], row[3]) for row in route[1:]], [(1, 37.50001, 127.10001), (2, 37.51, 127.11)])
        track = list(book["실제 이동 궤적"].values)
        self.assertEqual([(row[1], row[2], row[3], row[4]) for row in track[1:]],
                         [(1, "2026-11-17T12:06:00Z", 37.500001, 127.100001),
                          (2, "2026-11-17T12:06:05Z", 37.500005, 127.100008)])
        self.assertEqual(track[1][5:9], (5.5, 15.1, "gps", "stopped"))
        self.assertIsNone(track[2][6])

    def test_preview_matches_export_and_keeps_records_unchanged(self):
        from openpyxl import load_workbook

        other = {"screenshot": "older.png", "ocr": {"distance": "1km"}}
        records = [self.record, other]
        before = json.dumps(records, ensure_ascii=False, sort_keys=True)
        preview = preview_sheets(records)
        self.assertFalse(self.path.exists())
        self.assertEqual(json.dumps(records, ensure_ascii=False, sort_keys=True), before)
        export_xlsx(records, self.path)
        book = load_workbook(self.path, read_only=True, data_only=False)
        try:
            self.assertEqual(list(preview), book.sheetnames)
            for sheet in book:
                rows = list(sheet.values)
                normalize = lambda row: tuple('' if value is None else value for value in row)
                self.assertEqual(preview[sheet.title]['headers'], rows[0])
                self.assertEqual(preview[sheet.title]['total_rows'], len(rows) - 1)
                self.assertEqual([normalize(row) for row in preview[sheet.title]['rows']],
                                 [normalize(row) for row in rows[1:]])
            self.assertTrue(preview['출발 목적지']['rows'][-1][2].startswith('=HYPERLINK'))
        finally:
            book.close()

    def test_preview_limits_rows_without_hiding_total_and_validates_inputs(self):
        points = [{"lat": 37.5 + i * 0.000001, "lon": 127.1} for i in range(5)]
        record = {"route": {"points": points}}
        preview = preview_sheets([record], limit=2)
        self.assertEqual(preview['추천 경로 좌표']['total_rows'], 5)
        self.assertEqual(len(preview['추천 경로 좌표']['rows']), 2)
        self.assertEqual(preview_sheets([record], limit=0)['추천 경로 좌표']['rows'], [])
        with self.assertRaises(ExportError):
            preview_sheets([record], limit=-1)
        with self.assertRaises(ExportError):
            preview_sheets([{'route': {'points': [{'lat': float('nan'), 'lon': 127}]}}])

    def test_preview_searches_past_limit_and_preserves_original_indices(self):
        points = [{'lat': 37.5, 'lon': 127.1} for _ in range(1000)]
        points.append({'lat': 37.5, 'lon': 127.987654})
        records = [{'screenshot': 'RouteOne.png', 'route': {'points': points}},
                   {'screenshot': 'RouteTwo.png'}]
        filtered = preview_sheets(records, limit=1, query='127.987654')
        sheet = filtered['추천 경로 좌표']
        self.assertEqual(sheet['total_rows'], 1001)
        self.assertEqual(sheet['matched_rows'], 1)
        self.assertEqual(sheet['row_indices'], [1001])
        self.assertEqual(sheet['rows'][0][1], 1001)
        summary = preview_sheets(records, limit=1, query='rOuTeTwO')['수집 목록']
        self.assertEqual(summary['total_rows'], 2)
        self.assertEqual(summary['matched_rows'], 1)
        self.assertEqual(summary['row_indices'], [2])
        self.assertEqual(summary['rows'][0][3], 'RouteTwo.png')
        limited = preview_sheets(records, limit=2, query='127.1')['추천 경로 좌표']
        self.assertEqual(limited['matched_rows'], 1000)
        self.assertEqual(limited['row_indices'], [1, 2])
        self.assertEqual(preview_sheets(records, limit=0, query='RouteTwo')['수집 목록']['matched_rows'], 1)
        with self.assertRaisesRegex(ExportError, '검색어'):
            preview_sheets(records, query=None)

    def test_filtered_export_includes_all_matches_not_just_displayed_rows(self):
        from openpyxl import load_workbook

        points = [{'lat': 37.5 + i * .000001, 'lon': 127.1} for i in range(1001)]
        records = [{'route': {'points': points}}]
        shown = preview_sheets(records, limit=500, query='127.1')['추천 경로 좌표']
        self.assertEqual((len(shown['rows']), shown['matched_rows']), (500, 1001))
        export_filtered_xlsx(records, self.path, '추천 경로 좌표', '127.1', [1, 2])
        book = load_workbook(self.path, read_only=True, data_only=False)
        try:
            self.assertEqual(book.sheetnames, ['추천 경로 좌표'])
            rows = list(book.active.values)
            self.assertEqual(rows[0], ('순번', '위도'))
            self.assertEqual(len(rows), 1002)
            self.assertEqual((rows[1][0], rows[-1][0]), (1, 1001))
        finally:
            book.close()

    def test_filtered_export_literal_and_empty_results_do_not_overwrite(self):
        from openpyxl import load_workbook

        export_filtered_xlsx([self.record], self.path, '출발 목적지', 'EXAMPLE.INVALID', [2, 3])
        book = load_workbook(self.path, data_only=False)
        try:
            self.assertEqual(book.active.max_row, 2)
            self.assertEqual(book.active['A2'].data_type, 's')
            self.assertTrue(book.active['A2'].value.startswith('=HYPERLINK'))
        finally:
            book.close()
        previous = self.path.read_bytes()
        for query, columns in (('not present', [0]), ('', []), ('', [999]), ('', [0, 0]), ('', [True])):
            with self.subTest(query=query, columns=columns), self.assertRaises(ExportError):
                export_filtered_xlsx([self.record], self.path, '출발 목적지', query, columns)
            self.assertEqual(self.path.read_bytes(), previous)
        with self.assertRaisesRegex(ExportError, '시트'):
            export_filtered_xlsx([self.record], self.path, '없는 시트', '', [0])
        self.assertEqual(self.path.read_bytes(), previous)

    def test_empty_preview_has_all_sheet_headers_but_export_still_rejects(self):
        preview = preview_sheets([])
        self.assertEqual(list(preview), ['수집 목록', '출발 목적지', '추천 경로 좌표', '실제 이동 궤적'])
        for sheet in preview.values():
            self.assertTrue(sheet['headers'])
            self.assertEqual(sheet['rows'], [])
            self.assertEqual(sheet['total_rows'], 0)
        with self.assertRaisesRegex(ExportError, '없습니다'):
            export_xlsx([], self.path)
        self.assertFalse(self.path.exists())

    def test_build_workbook_preserves_literal_cell_type(self):
        book = build_workbook([self.record])
        try:
            self.assertEqual(book['출발 목적지']['C4'].data_type, 's')
        finally:
            book.close()

    def test_invalid_coordinates_and_unordered_time_never_replace_existing_file(self):
        self.path.write_bytes(b"old workbook")
        invalid = json.loads(json.dumps(self.record))
        invalid["route"]["points"][0]["lat"] = float("nan")
        with self.assertRaisesRegex(ExportError, "범위"):
            export_xlsx([invalid], self.path)
        self.assertEqual(self.path.read_bytes(), b"old workbook")
        invalid = json.loads(json.dumps(self.record))
        invalid["track"]["points"].reverse()
        with self.assertRaisesRegex(ExportError, "순서"):
            export_xlsx([invalid], self.path)
        self.assertEqual(self.path.read_bytes(), b"old workbook")

    def test_naive_timestamp_invalid_metadata_and_bad_extension_are_rejected(self):
        invalid = json.loads(json.dumps(self.record))
        invalid["track"]["points"][0]["timestamp"] = "2026-11-17T12:06:00"
        with self.assertRaisesRegex(ExportError, "시간대"):
            export_xlsx([invalid], self.path)
        with self.assertRaisesRegex(ExportError, "형식"):
            export_xlsx([{"route": []}], self.path)
        with self.assertRaisesRegex(ExportError, "xlsx"):
            export_xlsx([self.record], self.path.with_suffix(".png"))
        with self.assertRaisesRegex(ExportError, "없습니다"):
            export_xlsx([], self.path)

    def test_file_replace_failure_preserves_existing_file(self):
        self.path.write_bytes(b"previous")
        with patch("export_service.os.replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(ExportError, "저장"):
                export_xlsx([self.record], self.path)
        self.assertEqual(self.path.read_bytes(), b"previous")
        self.assertEqual(list(self.path.parent.glob(".export-*.xlsx")), [])

    def test_journey_exports_departure_and_arrival_independently(self):
        from openpyxl import load_workbook

        self.record['journey'] = {
            'status': 'completed', 'started_at': '2026-11-17T21:05:00+09:00', 'gps_status': 'stopped', 'app_match': 'mismatch',
            'start': {'screenshot': 'depart.png', 'captured_at': '2026-11-17T21:05:01+09:00', 'app': '티맵'},
            'finish': {
                'screenshot': '=arrival.png', 'captured_at': '2026-11-17T21:51:00+09:00', 'app': '티맵', 'app_detection': 'confirmed',
                'ocr': {'screen_time': '21:51', 'distance': '0.2km', 'duration': '1분'},
                'review': {'status': 'reviewed', 'screen_time': '21:52', 'distance': '0.1km', 'duration': '1분'},
            },
        }
        export_xlsx([self.record], self.path)
        book = load_workbook(self.path, data_only=False)
        try:
            sheet = book['수집 목록']
            header = [cell.value for cell in sheet[1]]
            values = {name: sheet.cell(2, index + 1).value for index, name in enumerate(header)}
            self.assertEqual(values['화면 시각'], '21:05')
            self.assertEqual(values['출발 이미지'], 'depart.png')
            self.assertEqual(values['출발 캡처 시각'], '2026-11-17T21:05:01+09:00')
            self.assertEqual(values['도착 이미지'], '=arrival.png')
            self.assertEqual(values['도착 캡처 시각'], '2026-11-17T21:51:00+09:00')
            self.assertEqual(values['도착 화면 시각'], '21:52')
            self.assertEqual(values['주행거리'], '0.1km')
            self.assertEqual(values['소요시간'], '1분')
            self.assertEqual(values['도착 검토 상태'], 'reviewed')
            self.assertEqual(values['GPS 종료 상태'], 'stopped')
            self.assertEqual(values['출발·도착 앱 일치'], 'mismatch')
            self.assertEqual(values['도착 앱 식별 상태'], 'confirmed')
            self.assertEqual(sheet.cell(2, header.index('도착 이미지') + 1).data_type, 's')
        finally:
            book.close()

    def test_journey_without_arrival_and_legacy_record_export_safely(self):
        from openpyxl import load_workbook

        self.record['journey'] = {'status': 'in_progress', 'start': {'screenshot': 'depart.png'}, 'finish': None}
        export_xlsx([self.record, {'screenshot': 'legacy.png'}], self.path)
        book = load_workbook(self.path, read_only=True)
        try:
            rows = list(book['수집 목록'].values)
            header = list(rows[0])
            self.assertEqual(rows[1][header.index('여정 상태')], 'in_progress')
            self.assertIsNone(rows[1][header.index('도착 이미지')])
            self.assertIsNone(rows[2][header.index('출발 이미지')])
            self.assertIsNone(rows[2][header.index('도착 검토 상태')])
        finally:
            book.close()

    def test_toll_and_recalculated_eta_in_preview_full_and_filtered_export(self):
        from openpyxl import load_workbook

        # The start review predates the toll field. The finish review cleared it.
        self.record['ocr'].update(toll='0원', estimated_arrival='09:00')
        self.record['review'].update(screen_time='22:12', duration='35분')
        self.record['journey'] = {'finish': {
            'ocr': {'screen_time': '14:00', 'duration': '3분', 'toll': '1,400원'},
            'review': {'status': 'reviewed', 'screen_time': '14:10', 'duration': '12분',
                       'toll': '', 'estimated_arrival': '14:03'},
        }}
        # A manually reviewed ETA takes precedence over this second record's calculation.
        manual = {'ocr': {'toll': '1,400원'}, 'review': {
            'status': 'reviewed', 'screen_time': '22:12', 'duration': '35분',
            'estimated_arrival': '22:55', 'estimated_arrival_manual': True,
        }, 'journey': {'finish': {'ocr': {'distance': '20km', 'duration': '45분', 'toll': '2,000원'}}}}
        records = [self.record, manual]
        preview = preview_sheets(records, query='22:47')['수집 목록']
        headers = ('예상 통행료', '도착 예정시간', '주행거리', '소요시간', '실 통행료')
        self.assertNotIn('도착 화면 기준 도착 예정시간', preview['headers'])
        columns = [preview['headers'].index(name) for name in headers]
        self.assertEqual(preview['row_indices'], [1])
        self.assertEqual(tuple(preview['rows'][0][i] for i in columns),
                         ('0원', '22:47', '', '12분', ''))

        export_xlsx(records, self.path)
        book = load_workbook(self.path, read_only=True)
        try:
            first, second = list(book['수집 목록'].values)[1:]
            self.assertEqual(tuple(first[i] for i in columns), ('0원', '22:47', None, '12분', None))
            self.assertEqual(tuple(second[i] for i in columns), ('1,400원', '22:55', '20km', '45분', '2,000원'))
        finally:
            book.close()

        export_filtered_xlsx(records, self.path, '수집 목록', '22:55', columns)
        book = load_workbook(self.path, read_only=True)
        try:
            self.assertEqual(list(book.active.values), [headers, ('1,400원', '22:55', '20km', '45분', '2,000원')])
        finally:
            book.close()

    def test_arrival_survives_gps_sync_failure_in_export(self):
        from openpyxl import load_workbook

        self.record['journey'] = {
            'status': 'needs_attention', 'gps_status': 'error',
            'start': {'screenshot': 'start.png'},
            'finish': {'screenshot': 'end.png', 'ocr': {'screen_time': '21:51'}},
        }
        export_xlsx([self.record], self.path)
        book = load_workbook(self.path, read_only=True)
        try:
            rows = list(book['수집 목록'].values)
            header = list(rows[0])
            self.assertEqual(rows[1][header.index('도착 이미지')], 'end.png')
            self.assertEqual(rows[1][header.index('도착 화면 시각')], '21:51')
            self.assertEqual(rows[1][header.index('여정 상태')], 'needs_attention')
            self.assertEqual(rows[1][header.index('GPS 종료 상태')], 'error')
        finally:
            book.close()

    def test_interrupted_arrival_capture_is_marked_without_ocr(self):
        from openpyxl import load_workbook

        record = {'journey': {'status': 'in_progress', 'start': {'screenshot': 'start.png'},
                              'finish': {'status': 'capturing', 'screenshot': 'pending.png'}}}
        export_xlsx([record], self.path)
        book = load_workbook(self.path, read_only=True)
        try:
            rows = list(book['수집 목록'].values)
            header = list(rows[0])
            self.assertEqual(rows[1][header.index('도착 캡처 상태')], 'capturing')
            self.assertIsNone(rows[1][header.index('도착 화면 시각')])
        finally:
            book.close()

    def test_invalid_journey_metadata_preserves_existing_file(self):
        self.path.write_bytes(b'previous')
        for journey in ({'finish': []}, {'start': 'invalid'}, {'finish': {'ocr': []}}):
            with self.subTest(journey=journey), self.assertRaisesRegex(ExportError, '형식'):
                export_xlsx([{'journey': journey}], self.path)
            self.assertEqual(self.path.read_bytes(), b'previous')

    def test_large_route_preserves_all_points_and_order(self):
        from openpyxl import load_workbook

        points = [{"lat": 37.5 + i * 0.000001, "lon": 127.1} for i in range(5000)]
        export_xlsx([{"route": {"points": points}}], self.path)
        book = load_workbook(self.path, read_only=True)
        try:
            rows = list(book["추천 경로 좌표"].values)
            self.assertEqual(len(rows), 5001)
            self.assertEqual(rows[1][1:4], (1, 37.5, 127.1))
            self.assertEqual(rows[-1][1], 5000)
            self.assertAlmostEqual(rows[-1][2], points[-1]["lat"])
        finally:
            book.close()


if __name__ == "__main__":
    unittest.main()
