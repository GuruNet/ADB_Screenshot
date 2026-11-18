"""Load capture records from the selected folder without modifying them."""
from __future__ import annotations

import json
from pathlib import Path


class CaptureLoadError(ValueError):
    """A capture record could not be loaded completely."""


def load_capture_records(folder, current_path=None, *, require_folder=False):
    """Return all capture records in a folder plus an open record outside it.

    Unrelated JSON is ignored. A malformed JSON with an adjacent PNG is treated
    as an incomplete capture, so a full export cannot silently omit it.
    """
    if not str(folder).strip():
        raise CaptureLoadError('저장 폴더를 선택하세요.')
    folder = Path(folder).expanduser()
    if require_folder and not folder.is_dir():
        raise CaptureLoadError('저장 폴더를 찾을 수 없습니다. 폴더를 다시 선택하세요.')
    if folder.exists() and not folder.is_dir():
        raise CaptureLoadError('저장 경로가 폴더가 아닙니다.')
    paths = sorted(folder.glob('*.json')) if folder.is_dir() else []
    if current_path:
        current = Path(current_path).expanduser().with_suffix('.json')
        if current.is_file() and all(p.resolve() != current.resolve() for p in paths):
            paths.append(current)
    records = []
    for path in paths:
        finish_image = path.with_name(path.name[:-len('_start.json')] + '_finish.png') if path.name.endswith('_start.json') else None
        candidate = bool(path.with_suffix('.png').exists() or (finish_image and finish_image.exists()) or
                         (current_path and path.resolve() == Path(current_path).with_suffix('.json').resolve()))
        try:
            with path.open(encoding='utf-8') as stream:
                record = json.load(stream)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            if candidate:
                raise CaptureLoadError(f'캡처 정보를 읽지 못했습니다: {path.name}') from exc
            continue
        if not isinstance(record, dict) or 'screenshot' not in record:
            if candidate:
                raise CaptureLoadError(f'캡처 정보 형식이 올바르지 않습니다: {path.name}')
            continue
        name = record.get('screenshot')
        if not isinstance(name, str) or not name or Path(name).name != name or Path(name).suffix.lower() != '.png' or Path(name).with_suffix('.json').name != path.name:
            raise CaptureLoadError(f'캡처 파일 정보가 올바르지 않습니다: {path.name}')
        records.append(record)
    return records
