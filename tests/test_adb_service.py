import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adb_service import AdbError, AdbService, PNG_END, PNG_START


class AdbServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = AdbService("adb")

    @patch("adb_service.subprocess.run")
    def test_connect_validates_and_interprets_adb_response(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"connected to 192.168.0.10:5555\n", b"")
        self.assertIn("connected", self.service.connect("192.168.0.10:5555"))
        self.assertEqual(run.call_args.args[0], ["adb", "connect", "192.168.0.10:5555"])
        run.return_value.stdout = b"failed to connect to 192.168.0.10:5555"
        with self.assertRaises(AdbError):
            self.service.connect("192.168.0.10:5555")
        with self.assertRaises(AdbError):
            self.service.connect("192.168.0.10:99999")

    @patch("adb_service.subprocess.run")
    def test_pair_uses_separate_port_and_rejects_bad_code(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"Successfully paired to 192.168.0.10:37000 [guid=x]", b"")
        self.service.pair("192.168.0.10:37000", "123456")
        self.assertEqual(run.call_args.args[0], ["adb", "pair", "192.168.0.10:37000", "123456"])
        with self.assertRaises(AdbError):
            self.service.pair("192.168.0.10:37000", "123")

    @patch("adb_service.subprocess.run")
    def test_list_devices_and_failure(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"List of devices attached\n192.168.0.1:5555\tdevice\nfoo\tunauthorized\n\n", b"")
        self.assertEqual(self.service.list_devices(), [("192.168.0.1:5555", "device"), ("foo", "unauthorized")])
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"device offline")
        with self.assertRaisesRegex(AdbError, "device offline"):
            self.service.device_info("foo")

    @patch("adb_service.subprocess.run")
    def test_macos_finder_launch_can_use_homebrew_adb_outside_path(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"List of devices attached\n", b"")
        for installed in (Path("/opt/homebrew/bin/adb"), Path("/usr/local/bin/adb")):
            with self.subTest(installed=installed), patch("adb_service.sys.platform", "darwin"), \
                 patch.dict("adb_service.os.environ", {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}, clear=True), \
                 patch("adb_service.shutil.which", return_value=None), \
                 patch.object(Path, "home", return_value=Path("/Users/test")), \
                 patch.object(Path, "is_file", lambda path: path == installed):
                self.assertEqual(AdbService().list_devices(), [])
                self.assertEqual(run.call_args.args[0], [str(installed), "devices"])

    @patch("adb_service.subprocess.run")
    def test_discovery_lists_separate_tls_services_without_connecting(self, run):
        data = ("List of discovered mdns services\n"
                "adb-phone-one\t_adb-tls-connect._tcp\t192.168.43.1:37001\n"
                "adb-phone-one\t_adb-tls-pairing._tcp\t192.168.43.1:41002\n"
                "adb-phone-one\t_adb-tls-connect._tcp\t192.168.43.1:37001\n"
                "adb-phone-one\t_adb-tls-connect._tcp\t[fe80::1%en0]:37001\n"
                "bad\t_adb._tcp\t192.168.43.1:5555\n"
                "bad\t_adb-tls-connect._tcp\t0.0.0.0:5555\n"
                "bad\t_adb-tls-connect._tcp\t192.168.43.1:99999\n"
                "bad/name\t_adb-tls-connect._tcp\t192.168.43.1:1\n")
        run.return_value = subprocess.CompletedProcess([], 0, data.encode(), b"")
        items = self.service.discover_wireless()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["adb", "mdns", "services"])
        self.assertEqual([(x['kind'], x['endpoint']) for x in items], [
            ('connect', '192.168.43.1:37001'), ('pairing', '192.168.43.1:41002'),
            ('connect', '[fe80::1%en0]:37001')])

    @patch("adb_service.subprocess.run")
    def test_discovery_empty_and_adb_error_are_distinct(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"List of discovered mdns services\n", b"")
        self.assertEqual(self.service.discover_wireless(), [])
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"mDNS unavailable")
        with self.assertRaisesRegex(AdbError, "mDNS unavailable"):
            self.service.discover_wireless()

    @patch("adb_service.subprocess.run")
    def test_macos_spawn_option_preserves_other_platform_defaults(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"List of devices attached\n", b"")
        with patch("adb_service.sys.platform", "darwin"):
            self.service.list_devices()
            self.assertFalse(run.call_args.kwargs["close_fds"])
        with patch("adb_service.sys.platform", "linux"):
            self.service.list_devices()
            self.assertNotIn("close_fds", run.call_args.kwargs)

    def test_identifies_focused_apps_and_never_uses_background_map(self):
        for package, name in [("com.skt.tmap.ku", "티맵"), ("com.nhn.android.nmap", "네이버 지도"), ("net.daum.android.map", "카카오맵")]:
            with self.subTest(package=package), patch.object(self.service, "_run", return_value=f"  mCurrentFocus=Window{{abc u0 {package}/.MainActivity}}"):
                self.assertEqual(self.service.identify_app("device"), (name, package))
        output = "mCurrentFocus=Window{abc u0 com.android.settings/.Settings}\nmFocusedApp=ActivityRecord{abc com.skt.tmap.ku/.MainActivity}"
        with patch.object(self.service, "_run", return_value=output):
            self.assertEqual(self.service.identify_app("device"), (None, "com.android.settings"))

    def test_uses_resumed_activity_only_when_no_focus_data(self):
        with patch.object(self.service, "_run", side_effect=["mCurrentFocus=null", "topResumedActivity=ActivityRecord{abc u0 net.daum.android.map/.MainActivity}"]):
            self.assertEqual(self.service.identify_app("device"), ("카카오맵", "net.daum.android.map"))

    def test_captures_only_complete_png_without_damaging_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            path.write_bytes(b"original")
            with patch.object(self.service, "_run", return_value=b"error: device offline"):
                with self.assertRaises(AdbError):
                    self.service.capture_screenshot("device", path)
            self.assertEqual(path.read_bytes(), b"original")
            png = PNG_START + b"test" + PNG_END
            with patch.object(self.service, "_run", return_value=png):
                self.assertEqual(self.service.capture_screenshot("device", path), path)
            self.assertEqual(path.read_bytes(), png)
            self.assertEqual(list(path.parent.glob(".capture-*")), [])


if __name__ == "__main__":
    unittest.main()
