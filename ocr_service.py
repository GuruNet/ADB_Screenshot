"""Read and conservatively interpret text from captured Android screenshots."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from route_card import find_selected_route_card
from route_metrics import estimated_arrival


class OCRError(RuntimeError):
    """An OCR engine or image could not be used."""


TIME = re.compile(r"(?<![\d:])(?:(?:오전|오후|AM|PM)\s*)?(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)(?:\s*(?:오전|오후|AM|PM)(?![A-Za-z]))?", re.I)
DISTANCE = re.compile(r"(?<![\d.])\d+(?:[.,]\d+)?\s*(?:km|㎞|m)(?![A-Za-z])", re.I)
DURATION = re.compile(r"(?<!\d)(?:\d{1,3}\s*시간(?:\s*\d{1,2}\s*분)?|\d{1,4}\s*분)")
WON_AMOUNT = r"(?:[0-9]{1,3}(?:,[0-9]{3}){1,2}|[0-9]{1,9})원"
LABELLED_TOLL = re.compile(rf"(?:통행료|통행요금|톨비)(?:금액)?[:：]?({WON_AMOUNT}|없음|무료)(?![가-힣])")
CARD_DISTANCE_AND_FARE = re.compile(rf"{DISTANCE.pattern}[·•.ㆍ|:/-]?({WON_AMOUNT})", re.I)


def _tolls_on_row(row: str, selected_card: bool) -> list[str]:
    # Vision sometimes splits the Korean label, thousands separator, or currency unit.
    compact = "".join(row.split())
    if any(word in compact for word in ("쿠폰", "적립", "캐시백", "할인", "포인트", "환급")):
        return []
    amounts = LABELLED_TOLL.findall(compact)
    if selected_card and (match := CARD_DISTANCE_AND_FARE.fullmatch(compact)):
        # TMAP's paid option omits the label, e.g. "19km · 1,400원".
        amounts.append(match.group(1))
    return ["0원" if amount in ("없음", "무료") else f"{int(amount[:-1].replace(',', '')):,}원"
            for amount in amounts]


def _unique(values: list[str]) -> str:
    # A list of distinct alternatives needs human review, not a guessed choice.
    unique = list(dict.fromkeys(re.sub(r"\s+", " ", value.strip()) for value in values))
    return unique[0] if len(unique) == 1 else ""


def _group_rows(lines: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for line in sorted(lines, key=lambda item: -float(item["y"]) - float(item["height"]) / 2):
        center = float(line["y"]) + float(line["height"]) / 2
        for row in rows:
            other = row[0]
            other_center = float(other["y"]) + float(other["height"]) / 2
            if abs(center - other_center) <= max(float(line["height"]), float(other["height"])) * 0.6:
                row.append(line)
                break
        else:
            rows.append([line])
    return rows


def _row_text(row: list[dict]) -> str:
    return " ".join(item["text"] for item in sorted(row, key=lambda item: float(item["x"])))


def _inside_card(line: dict, bounds: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = bounds  # Image coordinates start at the top-left.
    x, y = float(line["x"]), float(line["y"])  # OCR coordinates start at the bottom-left.
    return (left <= x and x + float(line["width"]) <= right
            and 1 - bottom <= y and y + float(line["height"]) <= 1 - top)


def extract_fields(lines: list[dict], *, screenshot: Path | None = None) -> dict[str, str]:
    """Return raw OCR text and values, leaving ambiguous candidates blank."""
    screen_lines = [line for line in lines if line.get("source") != "status_crop"]
    status_lines = [line for line in lines if line.get("source") == "status_crop"]
    grouped = _group_rows(screen_lines)
    raw_text = "\n".join(_row_text(row) for row in grouped)
    # shortcut: Android status time is expected in the top 15%; adapt only after testing device screenshots.
    top = [line for line in screen_lines if float(line["y"]) + float(line["height"]) / 2 >= 0.85]
    status_time = [match.group() for row in _group_rows(status_lines) for match in TIME.finditer(_row_text(row))]
    time_matches = status_time or [match.group() for row in _group_rows(top) for match in TIME.finditer(_row_text(row))]
    route_groups = grouped
    compact_text = "".join(raw_text.split())
    tmap_options = "도착" in compact_text and ("안내시작" in compact_text or "티맵추천" in compact_text)
    bounds = None
    if screenshot is not None and tmap_options:
        bounds = find_selected_route_card(screenshot)
        if bounds is not None:
            # Keep the full transcript and status clock, but never mix adjacent route cards.
            route_groups = _group_rows([line for line in screen_lines if _inside_card(line, bounds)])
    distance_candidates = []
    duration_rows = []
    durations = []
    tolls = []
    for group in route_groups:
        row = _row_text(group)
        tolls.extend(_tolls_on_row(row, selected_card=bounds is not None))
        center = sum(float(item["y"]) + float(item["height"]) / 2 for item in group) / len(group)
        for match in DURATION.finditer(row):
            # "오후 3시 20분 도착" is an arrival clock time, not a 20-minute journey.
            if re.search(r"\d{1,2}\s*시\s*$", row[:match.start()]):
                continue
            durations.append(match.group())
            duration_rows.append(center)
        for match in DISTANCE.finditer(row):
            distance_candidates.append((match.group(), center, bool(re.search(r"(?:총|경로)\s*거리", row))))
    # shortcut: proximity to duration or a route-distance label excludes many map scale bars; verify real layouts.
    distances = [value for value, y, labelled in distance_candidates
                 if labelled or any(abs(y - duration_y) <= .15 for duration_y in duration_rows)]
    screen_time = _unique(time_matches)
    duration = _unique(durations)
    distance = _unique(distances)
    # A lone readable price can belong to an unselected alternative.
    multiple_routes = (bool(durations) and not duration) or (bool(distances) and not distance)
    toll = _unique(tolls) if bounds is not None or not (tmap_options or multiple_routes) else ""
    return {
        "raw_text": raw_text,
        "screen_time": screen_time,
        "distance": distance,
        "duration": duration,
        "toll": toll,
        "estimated_arrival": estimated_arrival(screen_time, duration),
    }


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    except FileNotFoundError as exc:
        raise OCRError("OCR 실행 도구를 찾을 수 없습니다. 설치 안내를 확인해 주세요.") from exc
    except subprocess.TimeoutExpired as exc:
        raise OCRError("문자 인식 시간이 초과되었습니다.") from exc
    if result.returncode:
        raise OCRError("문자 인식에 실패했습니다: " + (result.stderr.strip() or "알 수 없는 오류"))
    return result.stdout


def _tesseract_lines(image: Path, executable: str) -> list[dict]:
    languages = _run([executable, "--list-langs"])
    if "kor" not in languages.splitlines():
        raise OCRError("Tesseract 한국어 언어 데이터(kor)가 필요합니다.")
    tsv = _run([executable, str(image), "stdout", "-l", "kor+eng", "tsv"])
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
    pages = [row for row in rows if row.get("level") == "1"]
    if not pages or not int(pages[0]["height"]) or not int(pages[0]["width"]):
        raise OCRError("Tesseract 이미지 크기를 읽지 못했습니다.")
    width, height = int(pages[0]["width"]), int(pages[0]["height"])
    lines = []
    for row in rows:
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        left, top = int(row["left"]), int(row["top"])
        w, h = int(row["width"]), int(row["height"])
        lines.append({"text": row["text"], "x": left / width, "y": 1 - (top + h) / height,
                      "width": w / width, "height": h / height})
    return lines


def recognize(path: str | Path) -> dict[str, str]:
    image = Path(path)
    if not image.is_file():
        raise OCRError("인식할 스크린샷 파일을 찾을 수 없습니다.")
    native_engine = Path(__file__).with_name("vision_ocr")
    swift = shutil.which("swift") if sys.platform == "darwin" else None
    if sys.platform == "darwin" and (native_engine.is_file() or swift):
        # The app bundle includes a compiled engine, so Xcode is not needed to run it.
        command = ([str(native_engine), str(image)] if native_engine.is_file() else
                   [swift, str(Path(__file__).with_name("vision_ocr.swift")), str(image)])
        try:
            lines = json.loads(_run(command))
        except (json.JSONDecodeError, TypeError) as exc:
            raise OCRError("OCR 엔진의 결과를 읽지 못했습니다.") from exc
    elif executable := shutil.which("tesseract"):
        try:
            lines = _tesseract_lines(image, executable)
        except (ValueError, KeyError, csv.Error) as exc:
            raise OCRError("Tesseract 인식 결과를 읽지 못했습니다.") from exc
    else:
        raise OCRError("OCR 도구가 없습니다. macOS에서는 Swift를, 다른 운영체제에서는 Tesseract와 한국어 언어 데이터를 설치하세요.")
    try:
        return extract_fields(lines, screenshot=image)
    except (TypeError, KeyError, ValueError) as exc:
        raise OCRError("OCR 엔진의 결과 형식이 올바르지 않습니다.") from exc
