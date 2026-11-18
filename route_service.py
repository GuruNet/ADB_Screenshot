"""Fetch independently computed driving routes from official map-service APIs.

The returned coordinates are not obtained from the app screenshot and must not be
represented as the route shown there. API keys remain in request headers only.
"""

from __future__ import annotations

from datetime import datetime
import json
import math
import urllib.error
import urllib.parse
import urllib.request


class RouteError(ValueError):
    """Invalid input or an unavailable/invalid routing response."""


_PROVIDERS = {
    "tmap": "tmap", "티맵": "tmap",
    "naver": "naver", "네이버 지도": "naver", "네이버": "naver",
    "kakao": "kakao", "카카오맵": "kakao", "카카오": "kakao",
}
_OPTIONS = {
    "tmap": {"0", "1", "2", "3", "4", "10", "12", "19"},
    "naver": {"trafast", "tracomfort", "traoptimal", "traavoidtoll", "traavoidcaronly"},
    "kakao": {"RECOMMEND", "TIME", "DISTANCE"},
}
_DEFAULTS = {"tmap": "0", "naver": "trafast", "kakao": "RECOMMEND"}


def _point(value, label):
    try:
        if not isinstance(value, dict) or isinstance(value.get("lat"), bool) or isinstance(value.get("lon"), bool):
            raise ValueError
        lat, lon = float(value["lat"]), float(value["lon"])
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise RouteError(f"{label}의 위도(-90~90)와 경도(-180~180)를 확인하세요.") from exc
    return {"lat": lat, "lon": lon}


def _credential(credentials, key, label):
    value = credentials.get(key) if isinstance(credentials, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise RouteError(f"{label}을 입력하세요.")
    value = value.strip()
    if any(character in value for character in "\r\n"):
        raise RouteError(f"{label}을 확인하세요.")
    return value


def _lonlat(point):
    return f"{point['lon']},{point['lat']}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Provider credentials must never be forwarded to a redirected host.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_json(request):
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=20) as response:
            body = response.read(10_000_001)
        if len(body) > 10_000_000:
            raise RouteError("경로 응답이 너무 큽니다.")
        return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise RouteError(f"경로 API 요청이 실패했습니다 (HTTP {code}). 인증 정보와 이용 권한을 확인하세요.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RouteError("경로 API에 연결할 수 없습니다. 네트워크와 서비스 상태를 확인하세요.") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RouteError("경로 API 응답을 읽을 수 없습니다.") from None


def _parse_points(provider, payload, option):
    try:
        if provider == "tmap":
            coordinates = []
            for feature in payload["features"]:
                geometry = feature.get("geometry") or {}
                if geometry.get("type") == "LineString":
                    coordinates.extend(geometry["coordinates"])
                elif geometry.get("type") == "MultiLineString":
                    for segment in geometry["coordinates"]:
                        coordinates.extend(segment)
        elif provider == "naver":
            if payload.get("code") != 0:
                raise RouteError("네이버 경로 API가 경로를 찾지 못했습니다. 입력과 API 이용 권한을 확인하세요.")
            coordinates = payload["route"][option][0]["path"]
        else:
            route = payload["routes"][0]
            if route.get("result_code") != 0:
                raise RouteError("카카오 경로 API가 경로를 찾지 못했습니다. 입력과 API 이용 권한을 확인하세요.")
            coordinates = []
            for section in route["sections"]:
                for road in section["roads"]:
                    vertices = road["vertexes"]
                    if len(vertices) % 2:
                        raise ValueError("odd number of coordinates")
                    coordinates.extend(zip(vertices[::2], vertices[1::2]))
        points = []
        for pair in coordinates:
            if len(pair) < 2:
                raise ValueError("missing coordinate")
            point = _point({"lon": pair[0], "lat": pair[1]}, "경로 응답 좌표")
            if not points or point != points[-1]:
                points.append(point)
        if len(points) < 2:
            raise ValueError("missing route geometry")
        return points
    except RouteError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise RouteError("경로 API에 유효한 전체 경로 좌표가 없습니다.") from None


def fetch_route(provider, origin, destination, credentials, waypoints=(), option=None):
    """Return WGS84 driving route; origin/destination/waypoints are {lat, lon}.

    Credentials: tmap/kakao ``{'api_key': ...}``; naver ``{'client_id': ...,
    'client_secret': ...}``. They are never included in the returned result.
    """
    provider = _PROVIDERS.get(str(provider).strip().lower())
    if not provider:
        raise RouteError("지원하지 않는 지도 서비스입니다.")
    start = _point(origin, "출발지")
    end = _point(destination, "목적지")
    try:
        via = [_point(point, "경유지") for point in waypoints]
    except TypeError as exc:
        raise RouteError("경유지 좌표를 확인하세요.") from exc
    if len(via) > 5:
        raise RouteError("경유지는 최대 5개까지 지원합니다.")
    option = str(option) if option is not None else _DEFAULTS[provider]
    if option not in _OPTIONS[provider]:
        raise RouteError("선택한 지도 서비스의 경로 탐색 옵션이 올바르지 않습니다.")

    if provider == "tmap":
        key = _credential(credentials, "api_key", "티맵 appKey")
        url = "https://apis.openapi.sk.com/tmap/routes?version=1"
        body = {"startX": start["lon"], "startY": start["lat"], "endX": end["lon"], "endY": end["lat"],
                "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO", "searchOption": int(option),
                "totalValue": 1, "sort": "index"}
        if via:
            body["passList"] = "_".join(map(_lonlat, via))
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"appKey": key, "Content-Type": "application/json", "Accept": "application/json"})
    elif provider == "naver":
        client_id = _credential(credentials, "client_id", "네이버 Client ID")
        client_secret = _credential(credentials, "client_secret", "네이버 Client Secret")
        query = {"start": _lonlat(start), "goal": _lonlat(end), "option": option}
        if via:
            query["waypoints"] = "|".join(map(_lonlat, via))
        url = "https://maps.apigw.ntruss.com/map-direction/v1/driving?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"x-ncp-apigw-api-key-id": client_id,
                                                       "x-ncp-apigw-api-key": client_secret, "Accept": "application/json"})
    else:
        key = _credential(credentials, "api_key", "카카오 REST API 키")
        query = {"origin": _lonlat(start), "destination": _lonlat(end), "priority": option,
                 "summary": "false", "alternatives": "false"}
        if via:
            query["waypoints"] = "|".join(map(_lonlat, via))
        url = "https://apis-navi.kakaomobility.com/v1/directions?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}", "Content-Type": "application/json", "Accept": "application/json"})

    requested_at = datetime.now().astimezone().isoformat()
    points = _parse_points(provider, _request_json(request), option)
    return {"provider": provider, "source": "official_directions_api_recomputed", "matching": "unverified",
            "requested_at": requested_at, "origin": start, "destination": end, "waypoints": via,
            "option": option, "travel_mode": "driving", "coordinate_system": "WGS84",
            "points": points}


def search_places(provider, query, credentials):
    """Return place/address candidates; the caller must let the user choose.

    NAVER Geocoding expects an address. TMAP supports place names and addresses;
    Kakao tries keyword search and then address search when there are no matches.
    Coordinates and labels come from the search service, not the screenshot.
    """
    provider = _PROVIDERS.get(str(provider).strip().lower())
    if not provider:
        raise RouteError("지원하지 않는 지도 서비스입니다.")
    if not isinstance(query, str) or not query.strip() or len(query.strip()) > 200:
        raise RouteError("검색할 장소명 또는 주소를 입력하세요.")
    query = query.strip()
    if provider == "tmap":
        key = _credential(credentials, "api_key", "티맵 appKey")
        params = {"version": "1", "searchKeyword": query, "searchType": "all", "count": "20",
                  "resCoordType": "WGS84GEO"}
        request = urllib.request.Request("https://apis.openapi.sk.com/tmap/pois?" + urllib.parse.urlencode(params),
                                         headers={"appKey": key, "Accept": "application/json"})
    elif provider == "naver":
        client_id = _credential(credentials, "client_id", "네이버 Client ID")
        secret = _credential(credentials, "client_secret", "네이버 Client Secret")
        params = {"query": query, "count": "20"}
        request = urllib.request.Request("https://maps.apigw.ntruss.com/map-geocode/v2/geocode?" + urllib.parse.urlencode(params),
                                         headers={"x-ncp-apigw-api-key-id": client_id,
                                                  "x-ncp-apigw-api-key": secret, "Accept": "application/json"})
    else:
        key = _credential(credentials, "api_key", "카카오 REST API 키")
        params = {"query": query, "size": "15"}
        request = urllib.request.Request("https://dapi.kakao.com/v2/local/search/keyword.json?" + urllib.parse.urlencode(params),
                                         headers={"Authorization": f"KakaoAK {key}", "Accept": "application/json"})
    payload = _request_json(request)
    try:
        if provider == "tmap":
            items = payload["searchPoiInfo"].get("pois", {}).get("poi", [])
            if isinstance(items, dict):
                items = [items]
            source = "tmap_poi_search"
        elif provider == "naver":
            if payload.get("status") != "OK":
                raise RouteError("네이버 주소 검색에 실패했습니다. Geocoding 이용 권한을 확인하세요.")
            items = payload["addresses"]
            source = "naver_geocoding"
        else:
            items = payload["documents"]
            source = "kakao_keyword_search"
            if not items:
                request = urllib.request.Request("https://dapi.kakao.com/v2/local/search/address.json?" + urllib.parse.urlencode(params),
                                                 headers={"Authorization": f"KakaoAK {key}", "Accept": "application/json"})
                items = _request_json(request)["documents"]
                source = "kakao_address_search"
        if not isinstance(items, list):
            raise TypeError
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if provider == "tmap":
                lat, lon = item.get("frontLat") or item.get("noorLat"), item.get("frontLon") or item.get("noorLon")
                name = item.get("name") or ""
                address = item.get("roadName") or " ".join(filter(None, [item.get("upperAddrName"), item.get("middleAddrName"), item.get("lowerAddrName"), item.get("firstNo")]))
            elif provider == "naver":
                lat, lon = item.get("y"), item.get("x")
                address = item.get("roadAddress") or item.get("jibunAddress") or ""
                name = address
            else:
                lat, lon = item.get("y"), item.get("x")
                address = item.get("road_address_name") or item.get("address_name") or ""
                name = item.get("place_name") or address
            try:
                point = _point({"lat": lat, "lon": lon}, "검색 결과")
            except RouteError:
                continue
            if point["lat"] == point["lon"] == 0:
                continue
            result.append({"name": str(name), "address": str(address), **point, "source": source})
        return result
    except RouteError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise RouteError("장소 검색 API의 응답을 읽을 수 없습니다.") from None
