import unittest

from route_metrics import effective_metrics, estimated_arrival


class RouteMetricsTests(unittest.TestCase):
    def test_time_and_duration_formats_and_rollover(self):
        for clock, duration, expected in (
            ("22:12", "35분", "22:47"), ("오전 12:00", "1시간", "01:00"),
            ("12:30 PM", "1시간 20분", "13:50"), ("9:10 오후", "0분", "21:10"),
            ("오후 11:55", "10분", "다음 날 00:05"), ("23:55", "5분", "다음 날 00:00"),
            ("9:41am", "80분", "11:01"), ("23:30", "25시간", "2일 후 00:30"),
        ):
            with self.subTest(clock=clock, duration=duration):
                self.assertEqual(estimated_arrival(clock, duration), expected)

    def test_invalid_or_partial_input_has_no_eta(self):
        for clock, duration in ((None, "35분"), ("22:12", None), ("", "35분"),
                                ("22:12", ""), ("24:01", "1분"), ("오후 13:10", "5분"),
                                ("오전 9:41 PM", "5분"), ("10:60", "12분"),
                                ("22:12", "약"), ("22:12", "-5분"), ("22:12", "1시간 90분")):
            with self.subTest(clock=clock, duration=duration):
                self.assertEqual(estimated_arrival(clock, duration), "")

    def test_legacy_review_can_gain_a_toll_without_overwriting_reviewed_values(self):
        ocr = {"screen_time": "22:12", "duration": "35분", "toll": "1,400원"}
        review = {"status": "reviewed", "screen_time": "22:10", "duration": "20분",
                  "estimated_arrival": "01:00"}
        shown = effective_metrics(review, ocr)
        self.assertEqual((shown["estimated_arrival"], shown["toll"]), ("22:30", "1,400원"))
        self.assertEqual(effective_metrics({**review, "toll": ""}, ocr)["toll"], "")
        self.assertEqual(effective_metrics(None, ocr)["estimated_arrival"], "22:47")

    def test_only_explicit_reviewed_eta_overrides_the_clock(self):
        ocr = {"screen_time": "22:12", "duration": "35분", "estimated_arrival": "22:47"}
        reviewed = {**ocr, "status": "reviewed", "estimated_arrival_manual": True,
                    "estimated_arrival": " 23:00 "}
        self.assertEqual(effective_metrics(reviewed, ocr)["estimated_arrival"], "23:00")
        for change in ({"estimated_arrival_manual": False}, {"estimated_arrival": " "},
                       {"status": "needs_review"}):
            with self.subTest(change=change):
                self.assertEqual(effective_metrics({**reviewed, **change}, ocr)["estimated_arrival"], "22:47")
