import json
from pathlib import Path
import tempfile
import unittest

from capture_repository import CaptureLoadError, load_capture_records


class CaptureRepositoryTests(unittest.TestCase):
    def test_loads_all_captures_and_open_record_outside_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'captures'
            folder.mkdir()
            for name in ('b', 'a'):
                (folder / f'{name}.json').write_text(json.dumps({'screenshot': f'{name}.png', 'app': name}))
            (folder / 'other.json').write_text('{}')
            outside = root / 'outside.json'
            outside.write_text(json.dumps({'screenshot': 'outside.png', 'app': 'outside'}))
            rows = load_capture_records(folder, outside)
            self.assertEqual([row['app'] for row in rows], ['a', 'b', 'outside'])

    def test_bad_capture_is_reported_and_unrelated_bad_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'other.json').write_text('{')
            self.assertEqual(load_capture_records(root), [])
            (root / 'broken.json').write_text('{')
            (root / 'broken.png').touch()
            with self.assertRaises(CaptureLoadError):
                load_capture_records(root)

    def test_invalid_screenshot_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'record.json'
            path.write_text(json.dumps({'screenshot': '../private.png'}))
            with self.assertRaises(CaptureLoadError):
                load_capture_records(temporary)

    def test_broken_journey_is_reported_even_if_only_arrival_image_remains(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'trip_start.json').write_text('{')
            (root / 'trip_finish.png').touch()
            with self.assertRaises(CaptureLoadError):
                load_capture_records(root)

    def test_valid_json_with_missing_capture_fields_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'capture.json').write_text('{}')
            (root / 'capture.png').touch()
            with self.assertRaises(CaptureLoadError):
                load_capture_records(root)

    def test_missing_folder_cannot_be_used_for_full_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CaptureLoadError):
                load_capture_records(Path(temporary) / 'missing', require_folder=True)


if __name__ == '__main__':
    unittest.main()
