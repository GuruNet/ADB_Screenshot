"""Capture departure and arrival as one recoverable journey record."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import uuid

from adb_service import AdbError, PNG_END, PNG_START
from gps_tracker import GpsTracker
from ocr_service import OCRError, recognize


class JourneyError(ValueError):
    """A journey cannot be safely created or resumed."""


def _now():
    return datetime.now().astimezone().isoformat()


def _identity(tracker, serial):
    try:
        return tracker.device_identity(serial)
    except AdbError:
        # An unavailable hardware identifier must not let a different transport take over.
        return {'id': serial, 'source': 'transport'}


def _verify_device(tracker, serial, journey):
    expected = journey.get('device_identity')
    if expected and _identity(tracker, serial) != expected:
        raise JourneyError('출발할 때 연결한 휴대폰을 선택하세요.')
    if not expected and journey.get('device_serial') != serial:
        raise JourneyError('출발할 때 사용한 기기 연결을 선택하세요.')


def _identify(adb, serial):
    try:
        return adb.identify_app(serial)
    except AdbError:
        return None, None


def _capture(adb, serial, path):
    before = _identify(adb, serial)
    captured_at = _now()
    adb.capture_screenshot(serial, path)
    after = _identify(adb, serial)
    same = bool(before[0] and before[1] and before == after)
    return {'screenshot': path.name, 'captured_at': captured_at,
            'app': before[0] if same else None, 'package': before[1] if same else None,
            'app_before': {'name': before[0], 'package': before[1]},
            'app_after': {'name': after[0], 'package': after[1]},
            'app_detection': 'confirmed' if same else 'uncertain'}


def _ocr(path, recognizer):
    try:
        return {'status': 'completed', **recognizer(path)}
    except OCRError as exc:
        return {'status': 'failed', 'error': str(exc), 'raw_text': '', 'screen_time': '',
                'distance': '', 'duration': '', 'toll': '', 'estimated_arrival': ''}


def _review():
    return {'status': 'needs_review', 'screen_time': '', 'distance': '', 'duration': '',
            'toll': '', 'estimated_arrival': ''}


def _read(path):
    try:
        metadata = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(metadata, dict) or not isinstance(metadata.get('journey'), dict):
            raise ValueError
        return metadata
    except (OSError, ValueError, TypeError) as exc:
        raise JourneyError('출발 기록을 읽을 수 없습니다. 저장된 JSON 파일을 확인하세요.') from exc


def _write(writer, path, metadata, image=None):
    try:
        writer(Path(path), metadata)
    except OSError as exc:
        saved = f' 스크린샷은 {image}에 보존되어 있습니다.' if image else ''
        raise JourneyError('기록 파일을 저장하지 못했습니다.' + saved) from exc


def start_journey(adb, serial, directory, writer, *, enable_gps=False, recognizer=recognize, tracker=None, on_saved=None):
    """Save a departure screenshot, then optionally request a user-approved GPS session."""
    if not serial:
        raise JourneyError('연결된 휴대폰을 선택하세요.')
    if not str(directory).strip():
        raise JourneyError('스크린샷 저장 폴더를 선택하세요.')
    folder = Path(directory).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    tracker = tracker or GpsTracker(adb)
    identity = _identity(tracker, serial)
    stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
    image = folder / f'{stamp}_{uuid.uuid4().hex[:8]}_start.png'
    snapshot = _capture(adb, serial, image)
    metadata_path = image.with_suffix('.json')
    journey = {'status': 'in_progress', 'started_at': snapshot['captured_at'],
               'device_serial': serial, 'device_identity': identity,
               'start': {key: snapshot[key] for key in ('screenshot', 'captured_at', 'app', 'package')},
               'finish': None, 'gps_status': 'pending' if enable_gps else 'disabled'}
    metadata = {**snapshot, 'device_serial': serial, 'ocr': {'status': 'pending'},
                'review': _review(), 'journey': journey}
    if enable_gps:
        metadata['track'] = {'session_id': uuid.uuid4().hex, 'status': 'pending', 'provider': 'gps',
                             'device_serial': serial, 'device_identity': identity, 'points': []}
    # Save the image and the session ID before the phone is asked to start recording.
    _write(writer, metadata_path, metadata, image)
    if on_saved is not None:
        try:
            on_saved(metadata_path)
        except Exception as exc:
            raise JourneyError(f'복구 정보를 저장하지 못해 GPS를 시작하지 않았습니다. 출발 기록은 {metadata_path}에 있습니다.') from exc
    if enable_gps:
        try:
            tracker.start(serial, metadata['track']['session_id'])
        except AdbError as exc:
            journey['gps_status'] = 'error'
            metadata['track']['error'] = str(exc)
    metadata['ocr'] = _ocr(image, recognizer)
    _write(writer, metadata_path, metadata, image)
    return image, metadata


def _finish_journey(adb, serial, metadata_path, writer, *, recognizer, tracker):
    metadata_path = Path(metadata_path)
    metadata = _read(metadata_path)
    journey = metadata['journey']
    if journey.get('status') not in ('in_progress', 'needs_attention', 'completed'):
        raise JourneyError('도착 처리를 할 수 없는 기록입니다.')
    tracker = tracker or GpsTracker(adb)
    _verify_device(tracker, serial, journey)
    finish = journey.get('finish')
    if finish is None:
        name = metadata.get('screenshot')
        if not isinstance(name, str) or not name or Path(name).name != name or not name.endswith('_start.png'):
            raise JourneyError('출발 스크린샷 파일명을 확인할 수 없습니다.')
        image = metadata_path.parent / (name[:-len('_start.png')] + '_finish.png')
        if image.exists():
            raise JourneyError(f'도착 이미지가 이미 존재합니다. 덮어쓰지 않았습니다: {image}')
        finish = {'screenshot': image.name, 'status': 'capturing', 'ocr': {'status': 'pending'}, 'review': _review()}
        journey['finish'] = finish
        journey['status'] = 'needs_attention'
        _write(writer, metadata_path, metadata)
    else:
        name = finish.get('screenshot')
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise JourneyError('저장된 도착 스크린샷 정보가 올바르지 않습니다.')
        image = metadata_path.parent / name

    if finish.get('status') == 'capturing':
        if image.exists():
            try:
                raw = image.read_bytes()
                if not raw.startswith(PNG_START) or not raw.endswith(PNG_END):
                    raise ValueError
            except (OSError, ValueError) as exc:
                raise JourneyError('도착 이미지가 불완전해 덮어쓰지 않았습니다. 파일을 확인하세요.') from exc
            captured_at = datetime.fromtimestamp(image.stat().st_mtime).astimezone().isoformat()
            snapshot = {'screenshot': image.name, 'captured_at': captured_at, 'app': None,
                        'package': None, 'app_detection': 'uncertain', 'recovered': True}
        else:
            snapshot = _capture(adb, serial, image)
        finish.update(snapshot)
        finish['status'] = 'captured'
        journey['finished_at'] = finish['captured_at']
        start_package = (journey.get('start') or {}).get('package')
        finish_package = finish.get('package')
        journey['app_match'] = ('unknown' if not start_package or not finish_package else
                                'confirmed' if start_package == finish_package else 'mismatch')
        _write(writer, metadata_path, metadata, image)
    elif not image.is_file():
        raise JourneyError('도착 스크린샷 파일이 없습니다. 기존 기록은 변경하지 않았습니다.')

    track = metadata.get('track')
    if journey.get('gps_status') == 'disabled' and not track:
        journey['status'] = 'completed'
    elif not isinstance(track, dict) or not track.get('session_id'):
        journey['gps_status'] = 'error'
        journey['gps_error'] = 'GPS 세션 정보가 없어 동기화할 수 없습니다.'
        journey['status'] = 'needs_attention'
    elif track.get('device_identity') and journey.get('device_identity') and track['device_identity'] != journey['device_identity']:
        journey['gps_status'] = 'error'
        journey['gps_error'] = 'GPS 기록과 출발 기록의 기기가 일치하지 않습니다.'
        journey['status'] = 'needs_attention'
    elif journey.get('gps_status') == 'stopped' and track.get('status') == 'stopped':
        journey['status'] = 'completed'
    else:
        try:
            track_identity = track.get('device_identity')
            if track_identity and track_identity != _identity(tracker, serial):
                raise JourneyError('GPS 기록을 시작한 휴대폰을 선택하세요.')
            if not track_identity and track.get('device_serial') not in (None, serial):
                raise JourneyError('GPS 기록을 시작한 기기 연결을 선택하세요.')
            result = tracker.read(serial, track['session_id'])
            if result.get('status') not in ('stopped', 'interrupted', 'error'):
                result = tracker.stop(serial, track['session_id'])
            old_points, new_points = track.get('points') or [], result.get('points') or []
            if len(new_points) < len(old_points) or new_points[:len(old_points)] != old_points:
                raise JourneyError('휴대폰에서 가져온 GPS 기록이 기존 기록과 달라 저장하지 않았습니다.')
            if result.get('status') not in ('stopped', 'interrupted', 'error'):
                raise JourneyError('GPS 종료가 아직 확인되지 않았습니다. 도착 버튼을 다시 눌러 동기화하세요.')
            result['device_serial'] = serial
            result['device_identity'] = journey.get('device_identity')
            metadata['track'] = result
            if result['status'] == 'stopped' and not result.get('started_at'):
                journey['gps_status'] = 'not_started'
            elif result['status'] == 'stopped' and not new_points:
                journey['gps_status'] = 'no_fix'
            elif result['status'] == 'interrupted':
                journey['gps_status'] = 'interrupted'
            else:
                journey['gps_status'] = 'stopped' if result['status'] == 'stopped' else 'error'
            # A terminal session cannot be repaired by retrying arrival; preserve the warning.
            journey['status'] = 'completed'
            if journey['gps_status'] == 'stopped':
                journey.pop('gps_error', None)
            elif journey['gps_status'] in ('not_started', 'no_fix'):
                journey.pop('gps_error', None)
            else:
                journey['gps_error'] = result.get('error') or result['status']
        except (AdbError, JourneyError, KeyError) as exc:
            journey['gps_status'] = 'error'
            journey['gps_error'] = str(exc)
            journey['status'] = 'needs_attention'
    _write(writer, metadata_path, metadata, image)
    if (finish.get('ocr') or {}).get('status') == 'pending':
        finish['ocr'] = _ocr(image, recognizer)
        # The duration on an arrival capture is elapsed driving time.
        finish['ocr']['estimated_arrival'] = ''
        _write(writer, metadata_path, metadata, image)
    return image, metadata


def _stop_after_capture_failure(tracker, serial, journey, track):
    """Stop only the known session; the phone keeps its journal for later recovery."""
    if not isinstance(track, dict) or not track.get('session_id'):
        return 'GPS 세션을 확인할 수 없습니다. 휴대폰 알림에서 기록을 직접 중지하세요.'
    identity = _identity(tracker, serial)
    expected = track.get('device_identity')
    if expected and expected != identity:
        return 'GPS 기록 기기가 일치하지 않습니다. 원래 휴대폰 알림에서 기록을 중지하세요.'
    if not expected and track.get('device_serial') not in (None, serial):
        return 'GPS 기록 기기가 일치하지 않습니다. 원래 휴대폰 알림에서 기록을 중지하세요.'
    try:
        try:
            result = tracker.read(serial, track['session_id'])
        except AdbError:
            result = {}
        if result.get('status') not in ('stopped', 'interrupted', 'error'):
            result = tracker.stop(serial, track['session_id'])
        if result.get('status') in ('stopped', 'interrupted'):
            return 'GPS 종료 상태를 확인했습니다. 휴대폰 기록은 보존되며 저장 문제를 해결한 뒤 도착을 다시 시도해 동기화하세요.'
    except (AdbError, KeyError, ValueError):
        pass
    return 'GPS 종료를 확인하지 못했습니다. 휴대폰 알림에서 직접 중지한 뒤 도착을 다시 시도해 동기화하세요.'


def finish_journey(adb, serial, metadata_path, writer, *, recognizer=recognize, tracker=None):
    """Save arrival once, then stop/sync GPS; a retry never overwrites the image."""
    try:
        metadata = _read(metadata_path)
    except JourneyError as exc:
        raise JourneyError(f'{exc} GPS가 기록 중이면 휴대폰 알림에서 직접 중지하세요.') from exc
    journey = metadata['journey']
    tracker = tracker or GpsTracker(adb)
    # Never touch a GPS session if the selected device is not the departure device.
    try:
        _verify_device(tracker, serial, journey)
    except JourneyError as exc:
        raise JourneyError(f'{exc} GPS는 종료되지 않았습니다.') from exc
    try:
        return _finish_journey(adb, serial, metadata_path, writer, recognizer=recognizer, tracker=tracker)
    except (AdbError, JourneyError, OSError) as exc:
        track = metadata.get('track')
        if journey.get('gps_status') == 'disabled' and not track:
            raise
        guidance = _stop_after_capture_failure(tracker, serial, journey, track)
        raise JourneyError(f'{exc} {guidance}') from exc
