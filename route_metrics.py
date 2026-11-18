"""Route metrics shared by OCR, the desktop review form, and Excel export."""
import re
import unicodedata


_CLOCK = re.compile(
    r"(?:(오전|오후|AM|PM)\s*)?([0-9]{1,2})\s*:\s*([0-9]{2})(?:\s*(오전|오후|AM|PM))?",
    re.IGNORECASE,
)
_DURATION = re.compile(r"(?:([0-9]{1,3})\s*시간\s*)?(?:([0-9]{1,4})\s*분)?")


def estimated_arrival(screen_time: str, duration: str) -> str:
    """Add the route duration to the displayed clock. Incomplete input has no ETA."""
    if not isinstance(screen_time, str) or not isinstance(duration, str):
        return ""
    clock = _CLOCK.fullmatch(unicodedata.normalize("NFKC", screen_time).strip())
    travel = _DURATION.fullmatch(unicodedata.normalize("NFKC", duration).strip())
    if not clock or not travel or not any(travel.groups()):
        return ""
    prefix, hour_text, minute_text, suffix = clock.groups()
    hour, minute = int(hour_text), int(minute_text)
    if hour > 23 or minute > 59 or (prefix and suffix):
        return ""
    period = prefix or suffix
    if period:
        if not 1 <= hour <= 12:
            return ""
        hour = hour % 12 + (12 if period.upper() in ("오후", "PM") else 0)

    travel_hours, travel_minutes = travel.groups()
    minutes = int(travel_minutes or 0)
    if travel_hours is not None and minutes >= 60:
        return ""
    total = (hour + int(travel_hours or 0)) * 60 + minute + minutes
    days, minute_of_day = divmod(total, 24 * 60)
    arrival_hour, arrival_minute = divmod(minute_of_day, 60)
    day_label = "" if days == 0 else "다음 날 " if days == 1 else f"{days}일 후 "
    return f"{day_label}{arrival_hour:02d}:{arrival_minute:02d}"


def effective_metrics(review: dict | None, ocr: dict | None) -> dict[str, str]:
    """Resolve a capture's saved review and OCR consistently for display and export."""
    review, ocr = review or {}, ocr or {}
    reviewed = review.get("status") == "reviewed"
    shown = review if reviewed else ocr
    values = {key: shown.get(key) for key in ("screen_time", "distance", "duration", "toll")}
    # Historical reviews predate tolls; a newer review can intentionally clear the fee.
    if "toll" not in shown:
        values["toll"] = ocr.get("toll")
    values = {key: "" if value is None else str(value) for key, value in values.items()}
    # Older reviews also stored an ETA, but it was only a snapshot of the calculation.
    manual_eta = review.get("estimated_arrival") if reviewed and review.get("estimated_arrival_manual") is True else None
    if isinstance(manual_eta, str) and manual_eta.strip():
        values["estimated_arrival"] = manual_eta.strip()
    else:
        values["estimated_arrival"] = estimated_arrival(values["screen_time"], values["duration"])
    return values
