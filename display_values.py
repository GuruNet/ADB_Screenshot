"""Readable table labels without changing stored or exported values."""
from datetime import datetime


def preview_column_order(headers):
    """Group related metrics in the UI while keeping full workbook column positions."""
    order = list(range(len(headers)))
    for duration, *related in (
        ('예상 소요시간', '예상 통행료', '도착 예정시간'),
        ('소요시간', '실 통행료'),
    ):
        if duration not in headers:
            continue
        additions = [headers.index(label) for label in related if label in headers]
        for column in additions:
            order.remove(column)
        insert_at = order.index(headers.index(duration)) + 1
        order[insert_at:insert_at] = additions
    return order


def table_value(header, value):
    if not isinstance(value, str):
        return value
    if header == '검토 상태':
        return {'reviewed': '검토 완료', 'needs_review': '검토 필요'}.get(value, value)
    if header == '캡처 시각':
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).strftime('%m.%d %H:%M:%S')
        except ValueError:
            pass
    return value
