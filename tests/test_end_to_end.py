"""Offline integration test with a fake adb process and the actual macOS OCR engine.

The PNG fixture is a generated map-like image, not a screenshot from a user.
No network or Android hardware is needed; the subprocess does not invoke real adb.
"""

from __future__ import annotations

import json
import gc
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import app as ui
from adb_service import AdbService


FIXTURE = Path(__file__).parent / "fixtures" / "route_sample.png"


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "requires macOS Vision and Swift")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        swift = subprocess.run(["swift", "--version"], capture_output=True, timeout=15)
        if swift.returncode:
            self.skipTest("Swift developer tools are unavailable")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        fake_adb = root / "adb"
        fake_adb.write_text(f'''#!{sys.executable}
import sys
from pathlib import Path
args = sys.argv[1:]
if args == ["devices"]:
    print("List of devices attached\\n192.0.2.10:5555\\tdevice")
elif args == ["connect", "192.0.2.10:5555"]:
    print("connected to 192.0.2.10:5555")
elif args == ["-s", "192.0.2.10:5555", "shell", "getprop", "ro.product.model"]:
    print("Test Phone")
elif args == ["-s", "192.0.2.10:5555", "shell", "getprop", "ro.build.version.release"]:
    print("14")
elif args == ["-s", "192.0.2.10:5555", "shell", "dumpsys", "window"]:
    print("mCurrentFocus=Window{{fake u0 com.nhn.android.nmap/.MapActivity}}")
elif args == ["-s", "192.0.2.10:5555", "exec-out", "screencap", "-p"]:
    sys.stdout.buffer.write(Path({str(FIXTURE.resolve())!r}).read_bytes())
else:
    print("unexpected fake adb command", args, file=sys.stderr)
    sys.exit(1)
''', encoding="utf-8")
        fake_adb.chmod(0o700)
        try:
            self.window = ui.ScreenshotApp(state_file=root / 'active.json')
        except tk.TclError as exc:
            self.skipTest(f"GUI display unavailable: {exc}")
        self.addCleanup(self.cleanup_window)
        self.window.withdraw()
        self.window.adb = AdbService(str(fake_adb))
        self.window.output_dir.set(str(root))

    def cleanup_window(self):
        window, self.window = self.window, None
        if window.worker is not None:
            window.worker.join(timeout=30)
        if window.sheet_worker is not None:
            window.sheet_worker.join(timeout=30)
        window.destroy()
        del window
        gc.collect()

    def wait_for(self, condition, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.window.update()
            if condition():
                return
            time.sleep(0.01)
        self.fail(f"작업 시간 초과: {self.window.status.get()}")

    def test_connect_capture_ocr_review_and_reopen(self):
        self.window.host.set("192.0.2.10")
        self.window.port.set("5555")
        self.window.connect_device()
        self.wait_for(lambda: not self.window.busy and self.window.selected_serial() == "192.0.2.10:5555")
        self.assertIn("네이버 지도", self.window.app_detail.get())

        self.window.capture()
        self.wait_for(lambda: self.window.current_path is not None and not self.window.busy)
        path = self.window.current_path
        self.assertEqual(path.read_bytes(), FIXTURE.read_bytes())
        self.assertEqual(self.window.screen_time.get(), "14:32")
        self.assertEqual(self.window.distance.get().replace(" ", ""), "65.4km")
        self.assertEqual(self.window.duration.get().replace(" ", ""), "1시간20분")

        self.window.distance.set("65.5km")
        self.assertTrue(self.window.save_review())
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["app"], "네이버 지도")
        self.assertEqual(metadata["ocr"]["distance"].replace(" ", ""), "65.4km")
        self.assertEqual(metadata["review"]["distance"], "65.5km")
        self.assertEqual(metadata["review"]["status"], "reviewed")

        with patch.object(ui.filedialog, "askopenfilename", return_value=str(path)):
            self.window.open_capture()
        self.assertEqual(self.window.distance.get(), "65.5km")


if __name__ == "__main__":
    unittest.main()
