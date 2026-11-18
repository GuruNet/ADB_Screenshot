"""Record user-approved device GPS fixes with the Android companion app."""

from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import re
import time
from uuid import uuid4

from adb_service import AdbError, AdbService


PACKAGE = "io.github.adbscreenshot.gpstracker"
ACTIVITY = PACKAGE + "/.TrackerActivity"
APK = Path(__file__).parent / "gps_helper" / "gps-helper.apk"
_SESSION = re.compile(r"[0-9a-f]{32}\Z")


def _session_id(value: str) -> str:
    if not isinstance(value, str) or not _SESSION.fullmatch(value):
        raise AdbError("올바르지 않은 GPS 기록 번호입니다.")
    return value


def _point(event: dict) -> dict:
    try:
        latitude, longitude = event["latitude"], event["longitude"]
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            raise ValueError
        latitude, longitude = float(latitude), float(longitude)
        if not math.isfinite(latitude) or not math.isfinite(longitude) or abs(latitude) > 90 or abs(longitude) > 180:
            raise ValueError
        timestamp = event["timestamp"]
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
        point = {"timestamp": timestamp, "latitude": latitude, "longitude": longitude}
        for key in ("accuracy_m", "altitude_m"):
            value = event.get(key)
            if value is None:
                point[key] = None
            else:
                value = float(value)
                if not math.isfinite(value) or (key == "accuracy_m" and value < 0):
                    raise ValueError
                point[key] = value
        point["provider"] = event.get("provider", "gps")
        return point
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise AdbError("GPS 기록에 잘못된 위치 데이터가 있습니다.") from exc


def parse_journal(text: str) -> dict:
    """Interpret a helper journal; an unfinished final line is not a complete fix."""
    status = "pending"
    points = []
    started_at = None
    stopped_at = None
    error = None
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines.pop()
    for line in lines:
        try:
            event = json.loads(line)
            kind = event["event"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AdbError("GPS 기록 파일을 읽을 수 없습니다.") from exc
        if kind == "awaiting_confirmation":
            status = "awaiting_confirmation"
        elif kind == "permission_required":
            status = "awaiting_permission"
        elif kind == "started":
            status, started_at = "recording", event.get("timestamp")
            stopped_at, error = None, None
        elif kind == "point":
            points.append(_point(event))
        elif kind == "provider_disabled":
            status = "waiting_for_gps"
        elif kind == "provider_enabled":
            status = "recording"
        elif kind == "stopped":
            status, stopped_at = ("error" if error else "stopped"), event.get("timestamp")
        elif kind == "interrupted":
            status = "error" if error else "interrupted"
        elif kind in {"permission_denied", "location_disabled", "location_error", "storage_error", "already_recording", "session_mismatch"}:
            status, error = "error", kind
    return {"status": status, "points": points, "started_at": started_at, "stopped_at": stopped_at,
            "provider": "gps", "coordinate_system": "WGS84", "error": error}


class GpsTracker:
    def __init__(self, adb: AdbService | None = None, apk: str | Path = APK):
        self.adb = adb or AdbService()
        self.apk = Path(apk)

    def install(self, serial: str) -> str:
        if not self.apk.is_file():
            raise AdbError("GPS 보조 앱이 없습니다. gps_helper 빌드 안내를 확인하세요.")
        output = self.adb._run("-s", serial, "install", "-r", str(self.apk), timeout=90)
        if "success" not in output.lower():
            raise AdbError("GPS 보조 앱 설치에 실패했습니다: " + output)
        return "GPS 보조 앱 설치 완료"

    def device_identity(self, serial: str) -> dict[str, str]:
        """Prefer the device serial property; otherwise restrict reuse to this transport."""
        try:
            value = self.adb._run("-s", serial, "shell", "getprop", "ro.serialno").strip()
        except AdbError:
            return {"id": serial, "source": "transport"}
        if value and value.lower() not in {"unknown", "null", "n/a", "none", "0"}:
            return {"id": value, "source": "ro.serialno"}
        return {"id": serial, "source": "transport"}

    def _launch(self, serial: str, action: str, session: str) -> None:
        session = _session_id(session)
        output = self.adb._run("-s", serial, "shell", "am", "start", "-W", "-n", ACTIVITY,
                               "--es", "action", action, "--es", "session", session, timeout=30)
        if re.search(r"(?:^|\n)(?:Error|Exception|SecurityException):", output, re.I):
            raise AdbError("GPS 보조 앱을 열지 못했습니다: " + output)

    def start(self, serial: str, session_id: str | None = None) -> str:
        session = _session_id(session_id) if session_id is not None else uuid4().hex
        self._launch(serial, "start", session)
        return session

    def _service_running(self, serial: str) -> bool:
        output = self.adb._run("-s", serial, "shell", "dumpsys", "activity", "services", PACKAGE)
        return bool(re.search(r"ServiceRecord\{[^\n]*" + re.escape(PACKAGE) + r"/(?:\.TrackerService|" + re.escape(PACKAGE) + r"\.TrackerService)", output))

    def read(self, serial: str, session: str) -> dict:
        session = _session_id(session)
        try:
            # shell -T preserves bytes and propagates the remote exit status on Android 12.
            raw = self.adb._run("-s", serial, "shell", "-T", "run-as", PACKAGE, "cat", f"files/tracks/{session}.jsonl", binary=True)
        except AdbError as exc:
            if "no such file or directory" in str(exc).lower():
                result = parse_journal("")
                result["session_id"] = session
                return result
            raise
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AdbError("GPS 기록 파일을 읽을 수 없습니다.") from exc
        result = parse_journal(text)
        result["session_id"] = session
        if result["status"] in ("recording", "waiting_for_gps") and not self._service_running(serial):
            result["status"] = "interrupted"
        return result

    def stop(self, serial: str, session: str) -> dict:
        self._launch(serial, "stop", session)
        deadline = time.monotonic() + 5
        while True:
            result = self.read(serial, session)
            if result["status"] in ("stopped", "interrupted", "error") or time.monotonic() >= deadline:
                return result
            time.sleep(0.2)
