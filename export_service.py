"""Export captured routes and recorded GPS locations to an Excel workbook."""

from __future__ import annotations

from datetime import datetime
import math
import os
from pathlib import Path
import tempfile

from display_values import table_value
from route_metrics import effective_metrics


class ExportError(ValueError):
    """Data cannot be safely exported."""


def _search_query(query):
    if not isinstance(query, str):
        raise ExportError("검색어는 문자열이어야 합니다.")
    return query.strip().casefold()


def _matches(row, needle, headers):
    return not needle or any(
        needle in str(value).casefold() or needle in str(table_value(header, value)).casefold()
        for header, value in zip(headers, row) if value is not None
    )


def _coordinate(point, field, minimum, maximum):
    value = point.get(field)
    if isinstance(value, bool) or value is None:
        raise ExportError(f"좌표 {field} 값이 없습니다.")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExportError(f"좌표 {field} 값이 숫자가 아닙니다.") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ExportError(f"좌표 {field} 값의 범위가 올바르지 않습니다.")
    return value


def _point(point, latitude, longitude):
    if not isinstance(point, dict):
        raise ExportError("좌표 정보의 형식이 올바르지 않습니다.")
    return _coordinate(point, latitude, -90, 90), _coordinate(point, longitude, -180, 180)


def _timestamp(value):
    if not isinstance(value, str) or not value:
        raise ExportError("이동 좌표에 기록 시각이 없습니다.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportError("이동 좌표의 시각 형식이 올바르지 않습니다.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExportError("이동 좌표의 시각에 시간대가 필요합니다.")
    return parsed


def _optional_number(value, field, nonnegative=False):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ExportError(f"{field} 값이 숫자가 아닙니다.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExportError(f"{field} 값이 숫자가 아닙니다.") from exc
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise ExportError(f"{field} 값의 범위가 올바르지 않습니다.")
    return number


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if len(value) > 32767:
        raise ExportError("엑셀 셀에 저장할 수 있는 글자 수를 초과했습니다.")
    return value


def _append(sheet, values):
    from openpyxl.cell import Cell

    cells = []
    for value in values:
        if isinstance(value, str):
            cell = Cell(sheet, value=_text(value))
            # Force literal text; screenshot names and route labels may start with '='.
            cell.data_type = "s"
            cells.append(cell)
        else:
            cells.append(value)
    sheet.append(cells)


def build_workbook(records, *, allow_empty=False):
    """Build the same validated workbook used for export and in-app preview.

    Route points use lat/lon, and recorded GPS points use latitude/longitude.
    Points stay in their stored order. A per-export record number joins sheets.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise ExportError("엑셀 저장을 위해 openpyxl 설치가 필요합니다. 사용 안내의 설치 방법을 확인하세요.") from exc
    records = list(records)
    if not records and not allow_empty:
        raise ExportError("내보낼 수집 기록이 없습니다.")
    workbook = Workbook()
    summary = workbook.active
    summary.title = "수집 목록"
    endpoints = workbook.create_sheet("출발 목적지")
    routes = workbook.create_sheet("추천 경로 좌표")
    tracks = workbook.create_sheet("실제 이동 궤적")
    headers = {
        summary: ("기록 번호", "캡처 시각", "지도 앱", "스크린샷 파일", "화면 시각", "경로 거리", "예상 소요시간", "검토 상태", "경로 상태", "경로 제공자", "경로 출처", "화면 경로와 일치", "경로 조회 시각", "경로 좌표계", "경로 옵션", "이동 수단", "경로 오류", "이동 기록 상태", "이동 기록 출처", "이동 기록 오류", "이동 위치 수", "이동 기록 세션", "이동 기록 시작 시각", "이동 기록 종료 시각", "여정 상태", "여정 시작 시각", "출발 캡처 시각", "출발 이미지", "도착 캡처 시각", "도착 이미지", "도착 지도 앱", "도착 화면 시각", "주행거리", "소요시간", "도착 검토 상태", "GPS 종료 상태", "도착 캡처 상태", "출발·도착 앱 일치", "출발 앱 식별 상태", "도착 앱 식별 상태",
                  "예상 통행료", "도착 예정시간", "실 통행료"),
        endpoints: ("기록 번호", "구분", "장소명", "위도", "경도", "좌표계", "좌표 출처"),
        routes: ("기록 번호", "순번", "위도", "경도", "좌표계", "경로 제공자", "경로 출처", "화면 경로와 일치"),
        tracks: ("기록 번호", "순번", "측정 시각", "위도", "경도", "정확도 (m)", "고도 (m)", "위치 제공자", "기록 상태", "좌표계"),
    }
    for sheet, names in headers.items():
        sheet.append(names)

    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ExportError(f"{index}번 수집 기록의 형식이 올바르지 않습니다.")
        route = record.get("route") if record.get("route") is not None else {}
        track = record.get("track") if record.get("track") is not None else {}
        review = record.get("review") if record.get("review") is not None else {}
        ocr = record.get("ocr") if record.get("ocr") is not None else {}
        journey = record.get("journey") if record.get("journey") is not None else {}
        if not all(isinstance(value, dict) for value in (route, track, review, ocr, journey)):
            raise ExportError(f"{index}번 수집 기록의 경로 또는 검토 데이터 형식이 올바르지 않습니다.")
        start = journey.get("start") if journey.get("start") is not None else {}
        finish = journey.get("finish") if journey.get("finish") is not None else {}
        if not all(isinstance(value, dict) for value in (start, finish)):
            raise ExportError(f"{index}번 수집 기록의 출발 또는 도착 데이터 형식이 올바르지 않습니다.")
        finish_ocr = finish.get("ocr") if finish.get("ocr") is not None else {}
        finish_review = finish.get("review") if finish.get("review") is not None else {}
        if not all(isinstance(value, dict) for value in (finish_ocr, finish_review)):
            raise ExportError(f"{index}번 수집 기록의 도착 인식 데이터 형식이 올바르지 않습니다.")
        finish_shown = effective_metrics(finish_review, finish_ocr)
        route_points = route.get("points") if route.get("points") is not None else []
        track_points = track.get("points") if track.get("points") is not None else []
        waypoints = route.get("waypoints") if route.get("waypoints") is not None else []
        if not all(isinstance(points, list) for points in (route_points, track_points, waypoints)):
            raise ExportError(f"{index}번 수집 기록의 좌표 목록 형식이 올바르지 않습니다.")
        shown = effective_metrics(review, ocr)
        provider = _text(route.get("provider"))
        source = _text(route.get("source"))
        matching = _text(route.get("matching"))
        coordinate_system = _text(route.get("coordinate_system") or ("WGS84" if source == "official_directions_api_recomputed" else ""))
        track_provider = _text(track.get("provider"))
        track_status = _text(track.get("status"))
        track_coordinate_system = _text(track.get("coordinate_system") or ("WGS84" if track_provider == "gps" else ""))
        _append(summary, (index, _text(record.get("captured_at")), _text(record.get("app")), _text(record.get("screenshot")),
                          _text(shown.get("screen_time")), _text(shown.get("distance")), _text(shown.get("duration")),
                          _text(review.get("status") or "needs_review"), _text(route.get("status")), provider, source, matching,
                          _text(route.get("requested_at")), coordinate_system if route else "", _text(route.get("option")),
                          _text(route.get("travel_mode")), _text(route.get("error")), track_status, track_provider,
                          _text(track.get("error")), len(track_points), _text(track.get("session_id")),
                          _text(track.get("started_at")), _text(track.get("stopped_at")),
                          _text(journey.get("status")), _text(journey.get("started_at")),
                          _text(start.get("captured_at")), _text(start.get("screenshot")),
                          _text(finish.get("captured_at")), _text(finish.get("screenshot")), _text(finish.get("app")),
                          _text(finish_shown.get("screen_time")), _text(finish_shown.get("distance")),
                          _text(finish_shown.get("duration")),
                          _text(finish_review.get("status") or ("needs_review" if finish else "")),
                          _text(journey.get("gps_status")), _text(finish.get("status")),
                          _text(journey.get("app_match")), _text(record.get("app_detection")),
                          _text(finish.get("app_detection")),
                          shown["toll"], shown["estimated_arrival"],
                          finish_shown["toll"]))

        locations = [("출발지", route.get("origin")), *(('경유지', point) for point in waypoints), ("목적지", route.get("destination"))]
        for label, point in locations:
            if point is None:
                continue
            lat, lon = _point(point, "lat", "lon")
            point_source = point.get("source") or ("경로 요청 입력값" if source == "official_directions_api_recomputed" else source)
            _append(endpoints, (index, label, _text(point.get("name")), lat, lon, coordinate_system, _text(point_source)))

        for sequence, point in enumerate(route_points, start=1):
            lat, lon = _point(point, "lat", "lon")
            _append(routes, (index, sequence, lat, lon, coordinate_system, provider, source, matching))

        previous_time = None
        for sequence, point in enumerate(track_points, start=1):
            lat, lon = _point(point, "latitude", "longitude")
            time = _timestamp(point.get("timestamp"))
            if previous_time is not None and time < previous_time:
                raise ExportError(f"{index}번 이동 기록의 위치 시각이 순서대로 정렬되어 있지 않습니다.")
            previous_time = time
            accuracy = _optional_number(point.get("accuracy_m"), "정확도", nonnegative=True)
            altitude = _optional_number(point.get("altitude_m"), "고도")
            _append(tracks, (index, sequence, _text(point.get("timestamp")), lat, lon, accuracy, altitude,
                             _text(point.get("provider") or track_provider), track_status, track_coordinate_system))

    for sheet, names in headers.items():
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="24527A")
            cell.alignment = Alignment(vertical="center")
        sheet.row_dimensions[1].height = 22
        for column, label in enumerate(names, start=1):
            width = 28 if "시각" in label or "이미지" in label or label in ("스크린샷 파일", "경로 출처", "좌표 출처", "화면 경로와 일치", "경로 오류", "이동 기록 오류") else 17
            if "도착 예정시간" in label:
                width = max(width, 2 * len(label) + 2)
            sheet.column_dimensions[get_column_letter(column)].width = width
            if label in ("위도", "경도"):
                for cells in sheet.iter_cols(min_col=column, max_col=column, min_row=2):
                    for cell in cells:
                        cell.number_format = "0.0000000000"

    return workbook


def preview_sheets(records, limit=500, query=""):
    """Search all rows and return a bounded preview with original 1-based data-row indices.

    ``total_rows`` counts the full sheet and ``matched_rows`` the filtered rows;
    ``row_indices`` maps each returned row to its position in the original sheet.
    Search examines all columns and does not mutate the records or write a file.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ExportError("미리보기 행 수는 0 이상의 정수여야 합니다.")
    needle = _search_query(query)
    workbook = build_workbook(records, allow_empty=True)
    try:
        result = {}
        for sheet in workbook.worksheets:
            total = max(0, sheet.max_row - 1)
            headers = tuple(cell.value for cell in sheet[1])
            rows, row_indices, matched = [], [], 0
            if total:
                for index, row in enumerate(sheet.iter_rows(min_row=2, max_row=total + 1, values_only=True), start=1):
                    if not _matches(row, needle, headers):
                        continue
                    matched += 1
                    if len(rows) < limit:
                        rows.append(tuple(row))
                        row_indices.append(index)
            result[sheet.title] = {"headers": headers, "rows": rows, "total_rows": total,
                                   "matched_rows": matched, "row_indices": row_indices}
        return result
    finally:
        workbook.close()


def _save_workbook(workbook, destination):
    destination = Path(destination)
    if destination.suffix.lower() != ".xlsx":
        raise ExportError("엑셀 파일 이름은 .xlsx로 끝나야 합니다.")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".export-", suffix=".xlsx", delete=False) as temp:
            temp_path = Path(temp.name)
        workbook.save(temp_path)
        os.replace(temp_path, destination)
    except (OSError, ValueError) as exc:
        raise ExportError(f"엑셀 파일을 저장하지 못했습니다: {exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        workbook.close()
    return destination


def export_xlsx(records, destination):
    """Export one or more capture metadata dictionaries without mutating them."""
    destination = Path(destination)
    if destination.suffix.lower() != ".xlsx":
        raise ExportError("엑셀 파일 이름은 .xlsx로 끝나야 합니다.")
    return _save_workbook(build_workbook(records), destination)


def _selected_workbook(source, sheet_name, row_indices, column_indices):
    from copy import copy
    from itertools import chain
    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise ExportError("엑셀 저장을 위해 openpyxl 설치가 필요합니다. 사용 안내의 설치 방법을 확인하세요.") from exc
    result = None
    try:
        if not isinstance(sheet_name, str) or sheet_name not in source.sheetnames:
            raise ExportError("내보낼 시트를 확인하세요.")
        sheet = source[sheet_name]
        try:
            rows, columns = list(row_indices), list(column_indices)
        except TypeError as exc:
            raise ExportError("내보낼 행과 열을 선택하세요.") from exc
        if not rows:
            raise ExportError("내보낼 행을 하나 이상 선택하세요.")
        if not columns:
            raise ExportError("내보낼 열을 하나 이상 선택하세요.")
        for values, maximum, label, minimum in ((rows, sheet.max_row - 1, "행", 1),
                                                 (columns, sheet.max_column - 1, "열", 0)):
            if (any(isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum
                    for value in values) or len(set(values)) != len(values)):
                raise ExportError(f"선택한 {label}의 범위 또는 중복을 확인하세요.")
        result = Workbook()
        target = result.active
        target.title = sheet.title
        for new_row, source_row in enumerate(chain((1,), (index + 1 for index in rows)), start=1):
            _append(target, [sheet.cell(source_row, index + 1).value for index in columns])
            for target_col, source_col in enumerate(columns, start=1):
                src, dst = sheet.cell(source_row, source_col + 1), target.cell(new_row, target_col)
                if src.has_style:
                    dst.font = copy(src.font)
                    dst.fill = copy(src.fill)
                    dst.border = copy(src.border)
                    dst.alignment = copy(src.alignment)
                    dst.protection = copy(src.protection)
                dst.number_format = src.number_format
        for target_col, source_col in enumerate(columns, start=1):
            width = sheet.column_dimensions[get_column_letter(source_col + 1)].width
            if width:
                target.column_dimensions[get_column_letter(target_col)].width = width
        target.freeze_panes = "B2"
        target.auto_filter.ref = target.dimensions
    except Exception:
        if result is not None:
            result.close()
        raise
    return result


def export_filtered_xlsx(records, destination, sheet_name, query, column_indices):
    """Export every matching row of one sheet with explicitly chosen columns.

    Filtering uses the same all-column, case-insensitive matching as the preview;
    the output includes matching rows beyond the preview's display limit.
    """
    destination = Path(destination)
    if destination.suffix.lower() != ".xlsx":
        raise ExportError("엑셀 파일 이름은 .xlsx로 끝나야 합니다.")
    needle = _search_query(query)
    source = build_workbook(records)
    try:
        if not isinstance(sheet_name, str) or sheet_name not in source.sheetnames:
            raise ExportError("내보낼 시트를 확인하세요.")
        sheet = source[sheet_name]
        headers = tuple(cell.value for cell in sheet[1])
        rows = [index for index, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=1)
                if _matches(row, needle, headers)] if sheet.max_row > 1 else []
        if not rows:
            raise ExportError("필터 조건에 맞는 행이 없습니다.")
        result = _selected_workbook(source, sheet_name, rows, column_indices)
    finally:
        source.close()
    return _save_workbook(result, destination)
