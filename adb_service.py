"""Small, bounded wrapper around the Android SDK's adb executable."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class AdbError(RuntimeError):
    """An actionable ADB or device error."""


MAP_PACKAGES = {
    "com.skt.tmap.ku": "티맵",
    "com.nhn.android.nmap": "네이버 지도",
    "net.daum.android.map": "카카오맵",
}
PNG_START = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_COMPONENT = re.compile(r"(?<![\w.])([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+")


def _endpoint(value: str) -> str:
    value = value.strip()
    try:
        if value.startswith("["):
            host, port = value[1:].split("]:", 1)
            address = ipaddress.IPv6Address(host)
            host = f"[{address}]"
        else:
            host, port = value.rsplit(":", 1)
            host = str(ipaddress.IPv4Address(host))
        if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError
    except ValueError as exc:
        raise AdbError("IP 주소와 포트를 확인해 주세요. 예: 192.168.0.10:5555") from exc
    return f"{host}:{int(port)}"


def _adb_path() -> str:
    found = shutil.which("adb")
    if found:
        return found
    candidates = []
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env):
            candidates.append(Path(os.environ[env]) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb"))
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    candidates.extend([
        Path.home() / "Library/Android/sdk/platform-tools/adb",
        Path.home() / "Android/Sdk/platform-tools/adb",
    ])
    if sys.platform == "darwin":
        # Finder launches apps without the shell's Homebrew PATH.
        candidates.extend([Path("/opt/homebrew/bin/adb"), Path("/usr/local/bin/adb")])
    for path in candidates:
        if path.is_file():
            return str(path)
    raise AdbError("ADB를 찾을 수 없습니다. Android SDK Platform-Tools를 설치하고 adb를 PATH에 추가해 주세요.")


class AdbService:
    def __init__(self, adb_path: str | None = None):
        self.adb_path = adb_path

    def _run(self, *args: str, timeout: int = 15, binary: bool = False) -> str | bytes:
        try:
            # macOS uses posix_spawn here; explicitly inheritable descriptors can still reach adb.
            spawn_options = {"close_fds": False} if sys.platform == "darwin" else {}
            result = subprocess.run(
                [self.adb_path or _adb_path(), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                **spawn_options,
            )
        except FileNotFoundError as exc:
            raise AdbError("ADB 실행 파일을 찾을 수 없습니다. Platform-Tools 설치를 확인해 주세요.") from exc
        except PermissionError as exc:
            raise AdbError("ADB 실행 권한이 없습니다. Platform-Tools 설치를 확인해 주세요.") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError("ADB 응답 시간이 초과되었습니다. 기기와 네트워크 상태를 확인해 주세요.") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
            raise AdbError(f"ADB 명령에 실패했습니다: {detail}" if detail else "ADB 명령에 실패했습니다.")
        return result.stdout if binary else result.stdout.decode("utf-8", "replace").strip()

    def connect(self, endpoint: str) -> str:
        endpoint = _endpoint(endpoint)
        result = self._run("connect", endpoint, timeout=20)
        if not re.search(r"(?:already connected to|connected to)\s", result, re.I):
            raise AdbError(f"ADB 연결에 실패했습니다: {result or '응답 없음'}")
        return result

    def pair(self, endpoint: str, code: str) -> str:
        endpoint = _endpoint(endpoint)
        code = code.strip()
        if not re.fullmatch(r"[0-9]{6}", code):
            raise AdbError("기기에 표시된 6자리 페어링 코드를 입력해 주세요.")
        result = self._run("pair", endpoint, code, timeout=30)
        if "successfully paired" not in result.lower():
            raise AdbError(f"페어링에 실패했습니다: {result or '응답 없음'}")
        return result

    def disconnect(self, endpoint: str) -> str:
        return self._run("disconnect", _endpoint(endpoint))

    def list_devices(self) -> list[tuple[str, str]]:
        result = self._run("devices")
        devices = []
        for line in result.splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 2:
                devices.append((fields[0], fields[1]))
        return devices

    def discover_wireless(self) -> list[dict[str, str | int]]:
        """List advertised TLS ADB services without pairing or connecting to them.

        Discovery does not prove a service is trusted or paired. A pairing service
        and a connect service can have different ports (and multiple addresses).
        """
        output = self._run("mdns", "services")
        kinds = {"_adb-tls-connect._tcp": "connect", "_adb-tls-pairing._tcp": "pairing"}
        found = []
        seen = set()
        for line in output.splitlines():
            fields = line.split()
            if len(fields) != 3:
                continue
            instance, service, raw_endpoint = fields
            kind = kinds.get(service.rstrip("."))
            if not kind or len(instance) > 255 or not re.fullmatch(r"[A-Za-z0-9_.-]+", instance):
                continue
            try:
                endpoint = _endpoint(raw_endpoint)
                if endpoint.startswith("["):
                    host, port = endpoint[1:].split("]:", 1)
                else:
                    host, port = endpoint.rsplit(":", 1)
                address = ipaddress.ip_address(host)
                if address.is_unspecified or address.is_multicast:
                    continue
            except (AdbError, ValueError):
                continue
            key = (instance, kind, endpoint)
            if key in seen:
                continue
            seen.add(key)
            found.append({"instance": instance, "kind": kind, "endpoint": endpoint,
                          "host": str(address), "port": int(port)})
        return found

    def device_info(self, serial: str) -> dict[str, str]:
        model = self._run("-s", serial, "shell", "getprop", "ro.product.model")
        version = self._run("-s", serial, "shell", "getprop", "ro.build.version.release")
        return {"model": model or "알 수 없음", "android_version": version or "알 수 없음"}

    def identify_app(self, serial: str) -> tuple[str | None, str | None]:
        # Focus takes precedence over resumed activities: a dialog can hide the map.
        window = self._run("-s", serial, "shell", "dumpsys", "window")
        for line in window.splitlines():
            if "mCurrentFocus=" in line and "null" not in line:
                match = _COMPONENT.search(line)
                if match:
                    package = match.group(1)
                    return MAP_PACKAGES.get(package), package
                return None, None
        for line in window.splitlines():
            if "mFocusedApp=" in line:
                match = _COMPONENT.search(line)
                if match:
                    package = match.group(1)
                    return MAP_PACKAGES.get(package), package
        activities = self._run("-s", serial, "shell", "dumpsys", "activity", "activities")
        for marker in ("topResumedActivity=", "mResumedActivity:"):
            for line in activities.splitlines():
                if marker in line:
                    match = _COMPONENT.search(line)
                    if match:
                        package = match.group(1)
                        return MAP_PACKAGES.get(package), package
        return None, None

    def capture_screenshot(self, serial: str, path: str | Path) -> Path:
        image = self._run("-s", serial, "exec-out", "screencap", "-p", timeout=30, binary=True)
        if not image.startswith(PNG_START) or not image.endswith(PNG_END):
            raise AdbError("유효한 스크린샷을 받지 못했습니다. 기기 화면과 연결 상태를 확인해 주세요.")
        destination = Path(path)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".capture-", suffix=".tmp", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(image)
            os.replace(tmp_path, destination)
        except OSError as exc:
            raise AdbError(f"스크린샷을 저장할 수 없습니다: {exc}") from exc
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        return destination
