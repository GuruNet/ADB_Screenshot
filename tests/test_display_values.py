"""Readable table values remain searchable while exports preserve source values."""
import copy
import tempfile
from pathlib import Path
import unittest

from display_values import preview_column_order, table_value
from export_service import export_filtered_xlsx, preview_sheets


class DisplayValueTests(unittest.TestCase):
    def test_completed_trip_metrics_are_grouped_in_preview(self):
        headers = preview_sheets([])['수집 목록']['headers']
        displayed = [headers[i] for i in preview_column_order(headers)]
        start = displayed.index('예상 소요시간')
        finish = displayed.index('주행거리')
        self.assertEqual(displayed[start:start + 3], ['예상 소요시간', '예상 통행료', '도착 예정시간'])
        self.assertEqual(displayed[finish:finish + 3], ['주행거리', '소요시간', '실 통행료'])
        self.assertCountEqual(displayed, headers)

    def test_formatter_changes_only_supported_columns_and_keeps_unknown_values(self):
        self.assertEqual(table_value('캡처 시각', '2026-11-17T23:43:07.989403+09:00'), '11.17 23:43:07')
        self.assertEqual(table_value('캡처 시각', '2026-11-17T14:43:07Z'), '11.17 14:43:07')
        self.assertEqual(table_value('검토 상태', 'reviewed'), '검토 완료')
        self.assertEqual(table_value('검토 상태', 'needs_review'), '검토 필요')
        for header, value in (('검토 상태', 'unknown'), ('캡처 시각', 'bad timestamp'),
                              ('스크린샷 파일', 'reviewed'), ('경로 거리', '7.5km'),
                              ('검토 상태', None), ('위도', 37.123456789)):
            self.assertEqual(table_value(header, value), value)

    def test_search_accepts_display_and_original_values_without_mutation(self):
        records = [
            {'captured_at': '2026-11-17T23:43:07.989403+09:00', 'screenshot': 'first.png',
             'review': {'status': 'reviewed'}},
            {'captured_at': '2026-11-18T00:11:00+09:00', 'screenshot': 'second.png',
             'review': {'status': 'needs_review'}},
        ]
        before = copy.deepcopy(records)
        for query, indices in (('검토 완료', [1]), ('reviewed', [1]), ('검토 필요', [2]),
                               ('needs_review', [2]), ('11.17 23:43:07', [1]),
                               ('2026-11-17T23:43:07', [1])):
            with self.subTest(query=query):
                result = preview_sheets(records, query=query)['수집 목록']
                self.assertEqual(result['row_indices'], indices)
                self.assertEqual(result['rows'][0][1], records[indices[0] - 1]['captured_at'])
                self.assertEqual(result['rows'][0][7], records[indices[0] - 1]['review']['status'])
        self.assertEqual(records, before)

    def test_display_search_filtered_export_preserves_raw_status_and_timestamp(self):
        from openpyxl import load_workbook

        records = [
            {'captured_at': '2026-11-17T23:43:07.989403+09:00', 'screenshot': 'first.png',
             'review': {'status': 'reviewed'}},
            {'screenshot': 'second.png', 'review': {'status': 'needs_review'}},
        ]
        before = copy.deepcopy(records)
        with tempfile.TemporaryDirectory() as temporary:
            for query in ('검토 완료', '11.17 23:43:07'):
                path = Path(temporary) / 'filtered.xlsx'
                export_filtered_xlsx(records, path, '수집 목록', query, [1, 7])
                book = load_workbook(path, read_only=True, data_only=False)
                try:
                    self.assertEqual(list(book.active.values), [
                        ('캡처 시각', '검토 상태'),
                        ('2026-11-17T23:43:07.989403+09:00', 'reviewed'),
                    ])
                finally:
                    book.close()
        self.assertEqual(records, before)

    def test_aliases_do_not_apply_to_arbitrary_names(self):
        records = [{'screenshot': 'reviewed', 'review': {'status': 'needs_review'}}]
        self.assertEqual(preview_sheets(records, query='검토 완료')['수집 목록']['matched_rows'], 0)
        self.assertEqual(preview_sheets(records, query='reviewed')['수집 목록']['matched_rows'], 1)


if __name__ == '__main__':
    unittest.main()
