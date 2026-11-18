import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from adb_service import AdbError, AdbService
from gps_tracker import GpsTracker, parse_journal


ID = "1" * 32


def journal(*events):
    return "".join(json.dumps(event) + "\n" for event in events)


class FakeAdb:
    def __init__(self):
        self.calls = []
        self.journal = ""
        self.services = ""
        self.error = None
        self.identity = ""

    def _run(self, *args, **kwargs):
        self.calls.append(args)
        if "install" in args:
            return "Success"
        if "am" in args:
            return "Status: ok"
        if "getprop" in args:
            return self.identity
        if "run-as" in args:
            if "shell" not in args or "-T" not in args or "exec-out" in args:
                raise AssertionError("GPS journal must use shell -T to preserve remote exit status")
            if self.error:
                raise AdbError(self.error)
            return self.journal.encode() if kwargs.get("binary") else self.journal.strip()
        if "dumpsys" in args:
            return self.services
        raise AssertionError(args)


class GpsTrackerTests(unittest.TestCase):
    def test_parse_real_fixes_and_status(self):
        text = journal(
            {"event": "started", "timestamp": "2026-01-01T00:00:00Z"},
            {"event": "point", "timestamp": "2026-01-01T00:00:03Z", "latitude": 37.1,
             "longitude": 127.2, "accuracy_m": 4.5, "altitude_m": None, "provider": "gps"},
            {"event": "stopped", "timestamp": "2026-01-01T00:00:05Z"},
        )
        result = parse_journal(text)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["points"][0]["accuracy_m"], 4.5)
        self.assertEqual(result["coordinate_system"], "WGS84")
        self.assertEqual(result["stopped_at"], "2026-01-01T00:00:05Z")

    def test_partial_record_is_not_a_point(self):
        result = parse_journal(journal({"event": "started"}) + '{"event":"point"')
        self.assertEqual(result["status"], "recording")
        self.assertEqual(result["points"], [])

    def test_invalid_coordinates_are_rejected(self):
        text = journal({"event": "point", "timestamp": "2026-01-01T00:00:03Z", "latitude": 900, "longitude": 1})
        with self.assertRaises(AdbError):
            parse_journal(text)

    def test_start_is_pending_and_requires_explicit_session(self):
        fake = FakeAdb()
        tracker = GpsTracker(fake)
        session = tracker.start("serial")
        self.assertEqual(len(session), 32)
        self.assertEqual(tracker.read("serial", session)["status"], "pending")
        self.assertIn("start", fake.calls[0])
        self.assertEqual(tracker.start("serial", ID), ID)
        with self.assertRaises(AdbError):
            tracker.read("serial", "../../other")

    def test_missing_data_is_pending_but_authorization_errors_are_not_hidden(self):
        fake = FakeAdb()
        tracker = GpsTracker(fake)
        fake.error = "cat: No such file or directory"
        self.assertEqual(tracker.read("serial", ID)["status"], "pending")
        fake.error = "run-as: package not debuggable"
        with self.assertRaises(AdbError):
            tracker.read("serial", ID)

    def test_interrupted_service_is_not_reported_as_recording(self):
        fake = FakeAdb()
        fake.journal = journal({"event": "started"})
        tracker = GpsTracker(fake)
        self.assertEqual(tracker.read("serial", ID)["status"], "interrupted")
        fake.services = "  * ServiceRecord{123 u0 io.github.adbscreenshot.gpstracker/.TrackerService}"
        self.assertEqual(tracker.read("serial", ID)["status"], "recording")

    def test_read_preserves_last_newline_and_last_point(self):
        fake = FakeAdb()
        fake.journal = journal({"event": "started"}, {"event": "point", "timestamp": "2026-01-01T00:00:03Z", "latitude": 37.1, "longitude": 127.2})
        fake.services = "ServiceRecord{123 u0 io.github.adbscreenshot.gpstracker/.TrackerService}"
        self.assertEqual(len(GpsTracker(fake).read("serial", ID)["points"]), 1)
        fake.journal += journal({"event": "stopped"})
        self.assertEqual(GpsTracker(fake).read("serial", ID)["status"], "stopped")

    def test_actual_adb_wrapper_propagates_remote_file_errors(self):
        tracker = GpsTracker(AdbService(adb_path="adb"))
        missing = CompletedProcess([], 1, b"", b"cat: No such file or directory\n")
        with patch("adb_service.subprocess.run", return_value=missing) as run:
            self.assertEqual(tracker.read("serial", ID)["status"], "pending")
        command = run.call_args.args[0]
        self.assertEqual(command[3:6], ["shell", "-T", "run-as"])
        denied = CompletedProcess([], 1, b"", b"run-as: package not debuggable\n")
        with patch("adb_service.subprocess.run", return_value=denied):
            with self.assertRaisesRegex(AdbError, "debuggable"):
                tracker.read("serial", ID)

    def test_permissions_and_provider_errors_are_explicit(self):
        for event in ("permission_denied", "location_disabled", "location_error", "storage_error"):
            with self.subTest(event=event):
                result = parse_journal(journal({"event": event}))
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error"], event)

    def test_successful_retry_clears_previous_error_without_losing_points(self):
        point = {"event": "point", "timestamp": "2026-01-01T00:00:03Z", "latitude": 37.1, "longitude": 127.2}
        result = parse_journal(journal({"event": "permission_denied"}, {"event": "awaiting_confirmation"},
                                       {"event": "started", "timestamp": "2026-01-01T00:00:00Z"},
                                       point, {"event": "stopped", "timestamp": "2026-01-01T00:00:05Z"}))
        self.assertEqual(result["status"], "stopped")
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["points"]), 1)

    def test_stop_after_unresolved_error_does_not_hide_failure(self):
        result = parse_journal(journal({"event": "storage_error"}, {"event": "stopped"}))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "storage_error")

    def test_install_uses_selected_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / "helper.apk"
            apk.touch()
            fake = FakeAdb()
            GpsTracker(fake, apk).install("serial")
            self.assertEqual(fake.calls[0][:4], ("-s", "serial", "install", "-r"))

    def test_device_identity_prefers_stable_serial_and_falls_back_to_transport(self):
        fake = FakeAdb()
        tracker = GpsTracker(fake)
        fake.identity = "device-serial"
        self.assertEqual(tracker.device_identity("usb"), tracker.device_identity("ip:port"))
        fake.identity = "unknown"
        self.assertNotEqual(tracker.device_identity("usb"), tracker.device_identity("ip:port"))
        self.assertEqual(tracker.device_identity("usb"), {"id": "usb", "source": "transport"})

    def test_device_identity_adb_failure_is_limited_to_current_transport(self):
        fake = FakeAdb()
        tracker = GpsTracker(fake)
        with patch.object(fake, '_run', side_effect=AdbError('getprop unavailable')):
            self.assertEqual(tracker.device_identity('usb'), {'id': 'usb', 'source': 'transport'})
            self.assertNotEqual(tracker.device_identity('usb'), tracker.device_identity('wifi'))


if __name__ == "__main__":
    unittest.main()
