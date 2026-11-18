import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocr_service import OCRError, extract_fields, recognize


def line(text, x, y, width=0.12, height=0.03):
    return {"text": text, "x": x, "y": y, "width": width, "height": height}


class OCRServiceTests(unittest.TestCase):
    def test_extracts_status_time_and_selected_route(self):
        lines = [line("오전 9:41", .02, .95), line("10:51 도착", .55, .25),
                 line("1시간", .05, .15), line("20분", .19, .15), line("65.4km", .05, .10)]
        result = extract_fields(lines)
        self.assertEqual(result["screen_time"], "오전 9:41")
        self.assertEqual(result["duration"], "1시간 20분")
        self.assertEqual(result["distance"], "65.4km")
        self.assertIn("10:51 도착", result["raw_text"])

    def test_alternative_routes_are_left_for_review(self):
        lines = [line("14:32", .02, .95), line("42분 32km", .05, .15),
                 line("50분 35km", .05, .10)]
        result = extract_fields(lines)
        self.assertEqual(result["screen_time"], "14:32")
        self.assertEqual(result["duration"], "")
        self.assertEqual(result["distance"], "")

    def test_tmap_metrics_follow_highlight_and_keep_full_transcript_and_clock(self):
        lines = [line("22:12", .04, .96), line("안내시작", .5, .06),
                 line("35분", .05, .22), line("19km", .05, .16),
                 line("29분", .47, .22), line("오후 10:05 도착", .47, .19),
                 line("17km", .47, .16), line("30분", .84, .22), line("21km", .84, .16)]
        with patch("ocr_service.find_selected_route_card", return_value=(.42, .70, .80, .86)):
            result = extract_fields(lines, screenshot=Path("route.png"))
            self.assertEqual((result["screen_time"], result["distance"], result["duration"]),
                             ("22:12", "17km", "29분"))
            self.assertIn("35분", result["raw_text"])
            self.assertIn("21km", result["raw_text"])
            # Neither a missing value nor a line spanning two cards belongs to this selection.
            lines.remove(next(item for item in lines if item["text"] == "17km"))
            lines.append(line("19km 17km", .05, .16, width=.65))
            result = extract_fields(lines, screenshot=Path("route.png"))
            self.assertEqual((result["distance"], result["duration"]), ("", "29분"))

        with patch("ocr_service.find_selected_route_card", return_value=None):
            self.assertEqual(extract_fields(lines, screenshot=Path("route.png"))["duration"], "")

    def test_arrival_clock_time_is_not_screen_time_or_duration(self):
        result = extract_fields([line("오후 3시 20분 도착", .05, .4), line("15:20", .4, .3)])
        self.assertEqual(result["screen_time"], "")
        self.assertEqual(result["duration"], "")

    def test_tolls_and_eta_follow_the_selected_card(self):
        lines = [line("22:12", .02, .96), line("안내시작", .50, .06),
                 line("35분", .05, .23), line("오후 10:11 도착", .05, .19),
                 line("19km · 통행료 없음", .05, .15, .26),
                 line("29분", .47, .23), line("오후 10:05 도착", .47, .19),
                 line("19km", .47, .15), line(". 1,400 원", .61, .15, .16)]
        for bounds, toll, eta in (((.02, .70, .41, .87), "0원", "22:47"),
                                  ((.42, .70, .81, .87), "1,400원", "22:41"),
                                  (None, "", "")):
            with self.subTest(toll=toll), patch("ocr_service.find_selected_route_card", return_value=bounds):
                result = extract_fields(lines, screenshot=Path("tmap.png"))
                self.assertEqual((result["toll"], result["estimated_arrival"]), (toll, eta))

    def test_toll_requires_a_clear_label_outside_a_selected_card(self):
        for value, expected in (("통 행 료 : 1, 400 원", "1,400원"), ("통행 요금 무료", "0원"),
                                ("통행료 0 원", "0원"), ("톨비 2400원", "2,400원"),
                                ("통행료 금액: 2,100원", "2,100원"),
                                ("통행료 미정", ""), ("주유비 1,400원", ""), ("택시비 1,400원", ""),
                                ("주차 무료", ""), ("1,400원", ""), ("통행료 1,400원 할인쿠폰", ""),
                                ("통행료 1,40원", "")):
            with self.subTest(value=value):
                self.assertEqual(extract_fields([line(value, .1, .2)])["toll"], expected)
        self.assertEqual(extract_fields([line("통행료 없음", .1, .16),
                                         line("통행료 1,400원", .1, .13)])["toll"], "")

    def test_selected_card_does_not_treat_fuel_or_unknown_as_toll(self):
        for price in ("19km", "19km · 주유비 1,400원", "19km · 무료주차"):
            lines = [line("안내시작", .5, .06), line("도착", .1, .2),
                     line(price, .05, .16), line("19km · 1,400원", .5, .16)]
            with self.subTest(price=price), patch("ocr_service.find_selected_route_card",
                                                   return_value=(.02, .70, .41, .87)):
                self.assertEqual(extract_fields(lines, screenshot=Path("route.png"))["toll"], "")

    def test_conflicting_status_times_do_not_get_guessed(self):
        result = extract_fields([line("9:10", .02, .96), line("9:11", .3, .96)])
        self.assertEqual(result["screen_time"], "")

    def test_status_crop_recovers_clock_without_altering_raw_screen_text(self):
        full = line("20843", .02, .97)
        retried = {**line("20:43", .02, .97), "source": "status_crop"}
        route = line("46분 7.5km", .05, .15)
        result = extract_fields([full, retried, route])
        self.assertEqual(result["screen_time"], "20:43")
        self.assertIn("20843", result["raw_text"])
        self.assertNotIn("20:43", result["raw_text"])
        self.assertEqual(result["duration"], "46분")
        self.assertEqual(result["distance"], "7.5km")

    def test_conflicting_status_crop_candidates_are_not_guessed(self):
        lines = [{**line("9:10", .02, .97), "source": "status_crop"},
                 {**line("9:11", .3, .97), "source": "status_crop"}]
        self.assertEqual(extract_fields(lines)["screen_time"], "")

    def test_identical_clock_from_multiple_crops_is_accepted(self):
        lines = [{**line("21:05", .02, .97), "source": "status_crop"},
                 {**line("21:05", .02, .97), "source": "status_crop"}]
        self.assertEqual(extract_fields(lines)["screen_time"], "21:05")

    def test_invalid_status_crop_falls_back_to_full_image(self):
        lines = [line("9:10", .02, .97), {**line("garbled", .02, .97), "source": "status_crop"}]
        self.assertEqual(extract_fields(lines)["screen_time"], "9:10")

    def test_status_time_keeps_ampm_after_the_clock(self):
        for value in ("9:41 PM", "9:41 pm", "9:41 오후", "9:41AM"):
            with self.subTest(value=value):
                result = extract_fields([line(value, .02, .96)])
                self.assertEqual(result["screen_time"], value)

    def test_scale_distance_without_nearby_route_information_is_not_guessed(self):
        lines = [line("14:32", .02, .95), line("100m", .05, .55)]
        self.assertEqual(extract_fields(lines)["distance"], "")
        lines.append(line("1시간 20분", .05, .12))
        self.assertEqual(extract_fields(lines)["distance"], "")

    def test_explicit_route_distance_label_does_not_require_duration(self):
        result = extract_fields([line("총 거리 300m", .05, .15)])
        self.assertEqual(result["distance"], "300m")

    def test_missing_image_and_missing_engine_are_explained(self):
        with self.assertRaisesRegex(OCRError, "파일"):
            recognize("/no/such/screenshot.png")
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "screen.png"
            image.touch()
            with patch("ocr_service.sys.platform", "linux"), patch("ocr_service.shutil.which", return_value=None):
                with self.assertRaisesRegex(OCRError, "Tesseract"):
                    recognize(image)

    def test_vision_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "screen.png"
            image.touch()
            with patch("ocr_service.sys.platform", "darwin"), patch("ocr_service.shutil.which", return_value="swift"), \
                 patch("ocr_service._run", return_value="not json"):
                with self.assertRaises(OCRError):
                    recognize(image)

    def test_bundled_vision_engine_is_used_without_requiring_swift(self):
        with tempfile.TemporaryDirectory() as folder:
            module = Path(folder) / "ocr_service.py"
            engine = module.with_name("vision_ocr")
            image = module.with_name("screen.png")
            engine.touch()
            image.touch()
            output = json.dumps([line("14:32", .02, .95)])
            for swift in (None, "/usr/bin/swift"):
                with self.subTest(swift=swift), patch("ocr_service.sys.platform", "darwin"), \
                     patch("ocr_service.__file__", str(module)), patch("ocr_service.shutil.which", return_value=swift), \
                     patch("ocr_service._run", return_value=output) as run:
                    self.assertEqual(recognize(image)["screen_time"], "14:32")
                    run.assert_called_once_with([str(engine), str(image)])


if __name__ == "__main__":
    unittest.main()
