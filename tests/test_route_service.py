import io
import json
import unittest
import urllib.error
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from route_service import RouteError, _NoRedirect, fetch_route, search_places


START = {"lat": 37.5, "lon": 127.0}
END = {"lat": 37.7, "lon": 127.2}
VIA = [{"lat": 37.6, "lon": 127.1}]


class RouteServiceTests(unittest.TestCase):
    def run_route(self, provider, credentials, payload, option=None):
        with patch("route_service._request_json", return_value=payload) as mocked:
            result = fetch_route(provider, START, END, credentials, VIA, option)
            return result, mocked.call_args.args[0]

    def test_tmap_request_and_route_geometry(self):
        payload = {"features": [
            {"geometry": {"type": "Point", "coordinates": [127, 37.5]}},
            {"geometry": {"type": "LineString", "coordinates": [[127, 37.5], [127.1, 37.6]]}},
            {"geometry": {"type": "LineString", "coordinates": [[127.1, 37.6], [127.2, 37.7]]}},
        ]}
        result, req = self.run_route("티맵", {"api_key": "TOP_SECRET"}, payload)
        body = json.loads(req.data)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.host, "apis.openapi.sk.com")
        self.assertEqual(req.get_header("Appkey"), "TOP_SECRET")
        self.assertEqual(body["passList"], "127.1,37.6")
        self.assertEqual(body["resCoordType"], "WGS84GEO")
        self.assertEqual(result["points"], [START, VIA[0], END])
        self.assertEqual(result["matching"], "unverified")
        self.assertNotIn("TOP_SECRET", json.dumps(result))

    def test_naver_request_and_geometry(self):
        payload = {"code": 0, "route": {"trafast": [{"path": [[127, 37.5], [127.2, 37.7]]}]}}
        result, req = self.run_route("naver", {"client_id": "id", "client_secret": "secret"}, payload)
        query = parse_qs(urlparse(req.full_url).query)
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.host, "maps.apigw.ntruss.com")
        self.assertEqual(req.get_header("X-ncp-apigw-api-key-id"), "id")
        self.assertEqual(req.get_header("X-ncp-apigw-api-key"), "secret")
        self.assertEqual(query["start"], ["127.0,37.5"])
        self.assertEqual(query["waypoints"], ["127.1,37.6"])
        self.assertEqual(result["points"], [START, END])
        self.assertNotIn("secret", req.full_url)
        self.assertNotIn("secret", json.dumps(result))

    def test_kakao_request_and_multiple_segments(self):
        payload = {"routes": [{"result_code": 0, "sections": [{"roads": [
            {"vertexes": [127, 37.5, 127.1, 37.6]}, {"vertexes": [127.1, 37.6, 127.2, 37.7]}
        ]}]}]}
        result, req = self.run_route("카카오맵", {"api_key": "secret"}, payload)
        query = parse_qs(urlparse(req.full_url).query)
        self.assertEqual(req.host, "apis-navi.kakaomobility.com")
        self.assertEqual(req.get_header("Authorization"), "KakaoAK secret")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(query["summary"], ["false"])
        self.assertEqual(query["waypoints"], ["127.1,37.6"])
        self.assertEqual(result["points"], [START, VIA[0], END])
        self.assertNotIn("secret", req.full_url)

    def test_tmap_multiple_waypoints_are_separated_by_underscore(self):
        payload = {"features": [{"geometry": {"type": "LineString", "coordinates": [[127, 37.5], [127.2, 37.7]]}}]}
        points = [{"lat": 37.55, "lon": 127.05}, {"lat": 37.65, "lon": 127.15}]
        with patch("route_service._request_json", return_value=payload) as mocked:
            fetch_route("tmap", START, END, {"api_key": "secret"}, points)
        self.assertEqual(json.loads(mocked.call_args.args[0].data)["passList"], "127.05,37.55_127.15,37.65")

    def test_invalid_input_does_not_send_request(self):
        with patch("route_service._request_json") as mock:
            with self.assertRaises(RouteError):
                fetch_route("naver", {"lat": float("nan"), "lon": 127}, END, {}, VIA)
            with self.assertRaises(RouteError):
                fetch_route("naver", START, END, {}, VIA)
            with self.assertRaises(RouteError):
                fetch_route("kakao", START, END, {"api_key": "x"}, VIA * 6)
            with self.assertRaises(RouteError):
                fetch_route("kakao", START, END, {"api_key": "x\nvalue"}, VIA)
            mock.assert_not_called()

    def test_invalid_or_incomplete_response_is_not_saved_as_route(self):
        for payload in ({"routes": [{"result_code": 1}]},
                        {"routes": [{"result_code": 0, "sections": [{"roads": [{"vertexes": [127, 37, 1]}]}]}]},
                        {"routes": []}):
            with self.subTest(payload=payload), patch("route_service._request_json", return_value=payload):
                with self.assertRaises(RouteError):
                    fetch_route("kakao", START, END, {"api_key": "test"})

    def test_tmap_place_search_returns_candidates_without_auto_selection(self):
        response = {"searchPoiInfo": {"pois": {"poi": [
            {"name": "장소 A", "frontLat": "37.5", "frontLon": "127.0", "upperAddrName": "서울"},
            {"name": "장소 B", "noorLat": "37.6", "noorLon": "127.1", "upperAddrName": "경기"},
            {"name": "좌표 없음"},
        ]}}}
        with patch("route_service._request_json", return_value=response) as mocked:
            result = search_places("tmap", "테스트 장소", {"api_key": "secret"})
        self.assertEqual(len(result), 2)
        self.assertEqual([p["name"] for p in result], ["장소 A", "장소 B"])
        self.assertEqual(result[0]["source"], "tmap_poi_search")
        self.assertEqual(result[1]["lat"], 37.6)
        req = mocked.call_args.args[0]
        self.assertEqual(parse_qs(urlparse(req.full_url).query)["searchKeyword"], ["테스트 장소"])
        self.assertNotIn("secret", req.full_url)

    def test_naver_geocoding_returns_address_candidates(self):
        response = {"status": "OK", "addresses": [{"roadAddress": "주소 A", "x": "127.0", "y": "37.5"}]}
        with patch("route_service._request_json", return_value=response):
            result = search_places("네이버 지도", "주소 A", {"client_id": "id", "client_secret": "secret"})
        self.assertEqual(result[0]["address"], "주소 A")
        self.assertEqual(result[0]["source"], "naver_geocoding")
        self.assertEqual((result[0]["lat"], result[0]["lon"]), (37.5, 127.0))

    def test_kakao_uses_address_search_when_keyword_not_found(self):
        responses = [{"documents": []}, {"documents": [{"address_name": "주소 B", "x": "127.2", "y": "37.7"}]}]
        with patch("route_service._request_json", side_effect=responses) as mocked:
            result = search_places("kakao", "주소 B", {"api_key": "secret"})
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result[0]["source"], "kakao_address_search")
        self.assertEqual(result[0]["name"], "주소 B")

    def test_place_search_requires_query_and_credentials(self):
        with patch("route_service._request_json") as mocked:
            with self.assertRaises(RouteError):
                search_places("tmap", "", {"api_key": "x"})
            with self.assertRaises(RouteError):
                search_places("naver", "주소", {"client_id": "x"})
            mocked.assert_not_called()

    def test_http_errors_do_not_leak_keys_and_redirects_are_not_followed(self):
        opener = Mock()
        body = io.BytesIO(b"response may contain secrets")
        opener.open.side_effect = urllib.error.HTTPError("https://example.invalid/?key=TOP_SECRET", 401, "TOP_SECRET", {}, body)
        with patch("route_service.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(RouteError) as error:
                fetch_route("tmap", START, END, {"api_key": "TOP_SECRET"})
        self.assertIn("HTTP 401", str(error.exception))
        self.assertNotIn("TOP_SECRET", str(error.exception))
        self.assertTrue(body.closed)
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))


if __name__ == "__main__":
    unittest.main()
