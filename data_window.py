"""Coordinate collection and spreadsheet export for a captured screen."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

from route_service import fetch_route


class DataWindow(tk.Toplevel):
    def __init__(self, parent, write_metadata):
        super().__init__(parent)
        self.parent = parent
        self.write_metadata = write_metadata
        self.path = Path(parent.current_path).with_suffix('.json')
        self.title('좌표와 이동 경로')
        self.geometry('850x720')
        self.minsize(760, 630)
        self.transient(parent)
        self.grab_set()
        self.results = queue.Queue()
        self.worker = None
        self.status = tk.StringVar(value='캡처 기록에 연결된 좌표를 관리합니다.')
        self.provider = tk.StringVar(value=self._provider())
        self.key = tk.StringVar()
        self.secret = tk.StringVar()
        self.values = {name: tk.StringVar() for name in ('origin_name', 'origin_lat', 'origin_lon',
                                                          'destination_name', 'destination_lat', 'destination_lon')}
        self.search_candidates = {'origin': [], 'destination': []}
        self.selected_places = {'origin': None, 'destination': None}
        self.search_selection = {'origin': tk.StringVar(), 'destination': tk.StringVar()}
        self._build()
        self._load()
        self.after(100, self._poll)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def _provider(self):
        app = self.parent.current_metadata.get('app') if self.parent.current_metadata else None
        return {'티맵': 'tmap', '네이버 지도': 'naver', '카카오맵': 'kakao'}.get(app, 'tmap')

    def _build(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill='both', expand=True)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill='both', expand=True)
        route = ttk.Frame(notebook, padding=12)
        self.track_frame = ttk.Frame(notebook, padding=12)
        export = ttk.Frame(notebook, padding=12)
        notebook.add(route, text='출발·목적지와 추천 경로')
        notebook.add(self.track_frame, text='실제 이동 GPS')
        notebook.add(export, text='엑셀 내보내기')

        ttk.Label(route, text='지도사 API는 입력한 위치로 경로를 다시 계산합니다. 캡처 화면과 일치하는지는 확인되지 않습니다.',
                  wraplength=760).pack(anchor='w', pady=(0, 8))
        line = ttk.Frame(route)
        line.pack(fill='x', pady=3)
        ttk.Label(line, text='지도 서비스').pack(side='left')
        ttk.Combobox(line, textvariable=self.provider, values=('tmap', 'naver', 'kakao'),
                     state='readonly', width=12).pack(side='left', padx=8)
        ttk.Label(line, text='TMAP·카카오: API 키 / 네이버: Client ID').pack(side='left')
        ttk.Entry(line, textvariable=self.key, show='•', width=25).pack(side='left', padx=8, fill='x', expand=True)
        line = ttk.Frame(route)
        line.pack(fill='x', pady=3)
        ttk.Label(line, text='네이버 Client Secret').pack(side='left')
        ttk.Entry(line, textvariable=self.secret, show='•', width=25).pack(side='left', padx=8)
        ttk.Label(line, text='키는 파일에 저장하지 않습니다.').pack(side='left')

        for kind, label in (('origin', '출발지'), ('destination', '목적지')):
            box = ttk.LabelFrame(route, text=label, padding=8)
            box.pack(fill='x', pady=4)
            line = ttk.Frame(box)
            line.pack(fill='x')
            ttk.Label(line, text='장소명·주소').pack(side='left')
            ttk.Entry(line, textvariable=self.values[kind + '_name']).pack(side='left', fill='x', expand=True, padx=6)
            ttk.Button(line, text='장소 검색', command=lambda k=kind: self.search(k)).pack(side='left')
            choices = ttk.Combobox(box, textvariable=self.search_selection[kind], state='readonly')
            choices.pack(fill='x', pady=3)
            choices.bind('<<ComboboxSelected>>', lambda event, k=kind, widget=choices: self.select_place(k, widget.current()))
            setattr(self, kind + '_choices', choices)
            line = ttk.Frame(box)
            line.pack(fill='x')
            ttk.Label(line, text='위도').pack(side='left')
            ttk.Entry(line, textvariable=self.values[kind + '_lat'], width=20).pack(side='left', padx=5)
            ttk.Label(line, text='경도').pack(side='left')
            ttk.Entry(line, textvariable=self.values[kind + '_lon'], width=20).pack(side='left', padx=5)
        ttk.Label(route, text='위경도를 직접 입력해도 됩니다. 검색·재조회 시 입력한 위치가 선택한 지도사에 전송됩니다.',
                  wraplength=760).pack(anchor='w', pady=4)
        line = ttk.Frame(route)
        line.pack(fill='x', pady=4)
        ttk.Button(line, text='출발·목적지만 저장', command=self.save_endpoints).pack(side='left', padx=(0, 8))
        ttk.Button(line, text='자동차 경로 재조회 및 저장', command=self.fetch).pack(side='left')
        ttk.Button(line, text='이동 기록 처음·끝 사용', command=self.use_track_endpoints).pack(side='left', padx=8)
        self.route_info = tk.StringVar(value='저장된 경로가 없습니다.')
        ttk.Label(route, textvariable=self.route_info, wraplength=760).pack(anchor='w', pady=5)

        self._build_track(self.track_frame)
        ttk.Label(export, text='저장된 캡처와 좌표를 엑셀 파일로 내보냅니다.',
                  font=('TkDefaultFont', 13, 'bold')).pack(anchor='w')
        ttk.Label(export, text='캡처 정보, 출발·목적지, 추천 경로 좌표, 실제 이동 기록이 시트별로 저장됩니다.',
                  wraplength=700).pack(anchor='w', pady=10)
        ttk.Button(export, text='현재 캡처 엑셀로 저장', command=lambda: self.export(False)).pack(anchor='w', pady=5)
        ttk.Button(export, text='여러 캡처 선택하여 엑셀로 저장', command=lambda: self.export(True)).pack(anchor='w', pady=5)
        ttk.Label(outer, textvariable=self.status, wraplength=800).pack(anchor='w', pady=(6, 0))

    def _build_track(self, frame):
        ttk.Label(frame, text='실제 이동 GPS 기록', font=('TkDefaultFont', 13, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='휴대폰에 GPS 기록 보조 앱을 설치하고, 휴대폰에서 정확한 위치 권한을 허용해야 합니다. '
                  '기록 중에는 휴대폰 알림이 표시됩니다. 지도 앱으로 돌아가 이동한 뒤 기록을 동기화하세요.',
                  wraplength=700).pack(anchor='w', pady=(8, 4))
        self.track_info = tk.StringVar(value='기록된 이동 경로가 없습니다.')
        ttk.Label(frame, textvariable=self.track_info, wraplength=700).pack(anchor='w', pady=10)
        line = ttk.Frame(frame)
        line.pack(anchor='w', pady=5)
        ttk.Button(line, text='1. 휴대폰 보조 앱 설치', command=self.install_tracker).pack(side='left', padx=(0, 6))
        ttk.Button(line, text='2. GPS 기록 요청', command=self.start_tracker).pack(side='left', padx=6)
        line = ttk.Frame(frame)
        line.pack(anchor='w', pady=5)
        ttk.Button(line, text='기록 상태 확인·동기화', command=self.sync_tracker).pack(side='left', padx=(0, 6))
        ttk.Button(line, text='기록 중지 및 동기화', command=self.stop_tracker).pack(side='left', padx=6)
        ttk.Label(frame, text='한 캡처에는 하나의 이동 기록을 연결합니다. 새 이동 기록은 새 캡처에서 시작하세요.\n'
                  '휴대폰과 PC의 연결이 끊겨도 보조 앱이 기록을 유지하면 재연결 후 동기화할 수 있습니다.',
                  wraplength=700).pack(anchor='w', pady=10)

    def _credentials(self):
        if self.provider.get() == 'naver':
            return {'client_id': self.key.get().strip(), 'client_secret': self.secret.get().strip()}
        return {'api_key': self.key.get().strip()}

    def _points(self):
        points = []
        for kind, label in (('origin', '출발지'), ('destination', '목적지')):
            try:
                lat = float(self.values[kind + '_lat'].get().strip())
                lon = float(self.values[kind + '_lon'].get().strip())
                if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(f'{label}의 위도와 경도를 확인하세요.') from exc
            selected = self.selected_places[kind]
            same_place = selected and lat == selected.get('lat') and lon == selected.get('lon')
            point = {'lat': lat, 'lon': lon, 'name': self.values[kind + '_name'].get().strip(),
                     'source': selected.get('source', 'place_search') if same_place else 'user_input'}
            if same_place:
                point['address'] = selected.get('address', '')
            points.append(point)
        return points

    def _read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def _save_field(self, name, value):
        metadata = self._read()
        metadata[name] = value
        journey = metadata.get('journey') or {}
        if name == 'track' and journey.get('status') in ('in_progress', 'needs_attention'):
            state = value.get('status')
            if state in ('pending', 'awaiting_confirmation', 'awaiting_permission'):
                journey['gps_status'] = 'pending'
            elif state in ('recording', 'waiting_for_gps'):
                journey['gps_status'] = state
            elif state == 'stopped':
                journey['gps_status'] = 'stopped' if value.get('points') else ('no_fix' if value.get('started_at') else 'not_started')
            elif state in ('interrupted', 'error'):
                journey['gps_status'] = state
            metadata['journey'] = journey
        self.write_metadata(self.path, metadata)
        if self.parent.current_path and Path(self.parent.current_path).with_suffix('.json') == self.path:
            self.parent.current_metadata = metadata

    def _load(self):
        data = self._read()
        route = data.get('route') or {}
        if route.get('provider') in ('tmap', 'naver', 'kakao'):
            self.provider.set(route['provider'])
        for kind in ('origin', 'destination'):
            point = route.get(kind) or {}
            self.selected_places[kind] = point if point.get('source') not in (None, 'user_input') else None
            self.values[kind + '_name'].set(point.get('name', ''))
            for axis in ('lat', 'lon'):
                value = point.get(axis)
                self.values[kind + '_' + axis].set('' if value is None else str(value))
        if route:
            self.route_info.set(f"저장된 추천 경로: {len(route.get('points') or [])}개 좌표 · 출처: {route.get('source', '확인 필요')} · 화면 일치 미확인")
        self._show_track(data.get('track') or {})

    def _show_track(self, track):
        labels = {'pending': '휴대폰에서 기록 시작 대기', 'awaiting_confirmation': '휴대폰에서 기록 시작 대기',
                  'awaiting_permission': '휴대폰 위치 권한 승인 대기', 'recording': '기록 중',
                  'waiting_for_gps': 'GPS 수신 대기', 'stopped': '기록 종료', 'interrupted': '기록 중단',
                  'error': '기록 오류'}
        if not track:
            self.track_info.set('기록된 이동 경로가 없습니다.')
            return
        state = labels.get(track.get('status'), track.get('status') or '확인 필요')
        error = track.get('error')
        self.track_info.set(f"상태: {state} · 저장된 위치: {len(track.get('points') or [])}개"
                            + (f' · 오류: {error}' if error else ''))

    def _serial(self):
        serial = self.parent.selected_serial()
        if not serial:
            raise ValueError('먼저 메인 화면에서 연결된 휴대폰을 선택하세요.')
        return serial

    def install_tracker(self):
        from gps_tracker import GpsTracker
        try:
            serial = self._serial()
        except ValueError as exc:
            return messagebox.showwarning('기기 확인', str(exc), parent=self)
        tracker = GpsTracker(self.parent.adb)
        self._run('휴대폰에 GPS 보조 앱을 설치하고 있습니다…', lambda: tracker.install(serial),
                  lambda _: self.status.set('설치 완료. GPS 기록 요청을 누른 뒤 휴대폰에서 시작과 권한을 승인하세요.'))

    def start_tracker(self):
        from gps_tracker import GpsTracker
        try:
            serial = self._serial()
            data = self._read()
            journey = data.get('journey') or {}
            if journey.get('status') == 'completed' or journey.get('finish'):
                raise ValueError('도착 처리가 시작된 여정에는 새 GPS 기록을 시작할 수 없습니다. 새 출발 화면을 캡처하세요.')
            old = data.get('track') or {}
            if old.get('session_id'):
                if old.get('status') == 'stopped':
                    raise ValueError('이 캡처의 이동 기록은 이미 종료됐습니다. 새 기록은 새 캡처에서 시작하세요.')
                session = old['session_id']
            else:
                session = uuid4().hex
        except (ValueError, OSError) as exc:
            return messagebox.showwarning('기록 시작', str(exc), parent=self)
        tracker = GpsTracker(self.parent.adb)
        def identified(identity):
            data = self._read()
            self._check_journey_device(data.get('journey') or {}, serial, identity)
            prior = data.get('track') or {}
            self._check_device(prior, serial, identity)
            track = {**prior, 'session_id': session, 'status': prior.get('status', 'pending'),
                     'provider': 'gps', 'device_serial': serial, 'device_identity': identity,
                     'points': prior.get('points', [])}
            self._save_field('track', track)
            self._show_track(track)
            def work():
                current = tracker.read(serial, session)
                if current['status'] in ('recording', 'waiting_for_gps', 'stopped'):
                    return current
                tracker.start(serial, session)
                return None
            def done(current):
                if current is not None:
                    current['device_serial'], current['device_identity'] = serial, identity
                    self._save_field('track', current)
                    self._show_track(current)
                    self.status.set('기존 기록 상태를 확인했습니다.')
                else:
                    self.status.set('휴대폰 보조 앱에서 GPS 기록 시작을 누르고 정확한 위치를 허용하세요.')
            self._run('휴대폰에 GPS 기록을 요청하고 있습니다…', work, done)
        self._run('휴대폰을 확인하고 있습니다…', lambda: tracker.device_identity(serial), identified)

    @staticmethod
    def _check_device(track, serial, identity):
        previous = track.get('device_identity')
        if previous and previous != identity:
            raise ValueError('기록을 시작한 휴대폰과 다른 기기입니다. 원래 휴대폰을 선택하세요.')
        if track.get('session_id') and not previous and track.get('device_serial') not in (None, serial):
            raise ValueError('기록을 시작한 기기 연결을 선택하세요.')

    @staticmethod
    def _check_journey_device(journey, serial, identity):
        if journey.get('status') not in ('in_progress', 'needs_attention'):
            return
        expected = journey.get('device_identity')
        if expected and expected != identity:
            raise ValueError('출발할 때 연결한 휴대폰을 선택하세요.')
        if not expected and journey.get('device_serial') != serial:
            raise ValueError('출발할 때 사용한 기기 연결을 선택하세요.')

    def _track_operation(self, stop):
        from gps_tracker import GpsTracker
        try:
            serial = self._serial()
            data = self._read()
            old = data.get('track') or {}
            journey = data.get('journey') or {}
            session = old.get('session_id')
            if not session:
                raise ValueError('먼저 GPS 기록을 요청하세요.')
        except (ValueError, OSError) as exc:
            return messagebox.showwarning('기록 확인', str(exc), parent=self)
        tracker = GpsTracker(self.parent.adb)
        def work():
            identity = tracker.device_identity(serial)
            self._check_journey_device(journey, serial, identity)
            self._check_device(old, serial, identity)
            return (tracker.stop(serial, session) if stop else tracker.read(serial, session), identity)
        def done(value):
            result, identity = value
            data = self._read()
            prior = data.get('track') or {}
            self._check_journey_device(data.get('journey') or {}, serial, identity)
            if prior.get('session_id') != session:
                raise ValueError('현재 기록이 바뀌어 동기화하지 않았습니다.')
            self._check_device(prior, serial, identity)
            old_points, new_points = prior.get('points') or [], result.get('points') or []
            if len(new_points) < len(old_points) or new_points[:len(old_points)] != old_points:
                raise ValueError('휴대폰에서 가져온 기록이 기존 기록과 달라 저장하지 않았습니다.')
            result['device_serial'], result['device_identity'] = serial, identity
            self._save_field('track', result)
            self._show_track(result)
            self.status.set('GPS 기록 상태를 동기화했습니다.')
        self._run('휴대폰의 GPS 기록을 가져오고 있습니다…', work, done)

    def sync_tracker(self):
        self._track_operation(False)

    def stop_tracker(self):
        self._track_operation(True)

    def _run(self, message, work, done):
        if self.worker:
            return
        self.status.set(message)
        self._set_working(True)
        def run():
            try:
                self.results.put((done, work(), None))
            except Exception as exc:
                self.results.put((done, None, exc))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _set_working(self, working):
        def visit(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Button, ttk.Combobox)):
                    child.state(['disabled'] if working else ['!disabled'])
                visit(child)
        visit(self)

    def _poll(self):
        try:
            done, result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.worker.join()
            self.worker = None
            self._set_working(False)
            if error:
                self.status.set(str(error))
                messagebox.showerror('작업 실패', str(error), parent=self)
            else:
                try:
                    done(result)
                except (ValueError, OSError) as exc:
                    self.status.set(str(exc))
                    messagebox.showerror('저장 실패', str(exc), parent=self)
        self.after(100, self._poll)

    def search(self, kind):
        from route_service import search_places
        query = self.values[kind + '_name'].get().strip()
        if not query:
            return messagebox.showwarning('입력 확인', '검색할 장소명 또는 주소를 입력하세요.', parent=self)
        provider, credentials = self.provider.get(), self._credentials()
        self._run('지도사에 장소를 검색하고 있습니다…',
                  lambda: search_places(provider, query, credentials), lambda places: self._show_places(kind, places))

    def _show_places(self, kind, places):
        self.search_candidates[kind] = places
        widget = getattr(self, kind + '_choices')
        widget['values'] = [f"{p.get('name', '')} · {p.get('address', '')}" for p in places]
        self.search_selection[kind].set('')
        self.status.set('검색 결과를 선택하세요.' if places else '검색 결과가 없습니다.')

    def select_place(self, kind, index):
        if 0 <= index < len(self.search_candidates[kind]):
            point = self.search_candidates[kind][index]
            self.selected_places[kind] = point
            self.values[kind + '_name'].set(point.get('name', ''))
            self.values[kind + '_lat'].set(str(point['lat']))
            self.values[kind + '_lon'].set(str(point['lon']))

    def save_endpoints(self):
        try:
            origin, destination = self._points()
            current = self._read().get('route') or {}
            if current.get('points') and not messagebox.askyesno('좌표 변경', '기존 추천 경로 좌표를 지우고 출발·목적지만 저장할까요?', parent=self):
                return
            route = {'provider': self.provider.get(), 'source': 'user_input', 'matching': 'unverified',
                     'status': 'endpoints_only', 'requested_at': datetime.now().astimezone().isoformat(), 'coordinate_system': 'WGS84',
                     'origin': origin, 'destination': destination, 'points': []}
            self._save_field('route', route)
            self.route_info.set('출발·목적지 좌표를 저장했습니다. 추천 경로 좌표는 없습니다.')
            self.status.set('좌표를 저장했습니다.')
        except (ValueError, OSError) as exc:
            messagebox.showerror('저장 실패', str(exc), parent=self)

    def use_track_endpoints(self):
        try:
            points = (self._read().get('track') or {}).get('points') or []
            if len(points) < 2:
                raise ValueError('동기화된 이동 위치가 두 개 이상 필요합니다.')
            resolved = []
            for kind, point, source in (('origin', points[0], 'gps_track_first'),
                                        ('destination', points[-1], 'gps_track_last')):
                lat, lon = float(point['latitude']), float(point['longitude'])
                if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError('이동 기록의 좌표를 확인하세요.')
                resolved.append((kind, lat, lon, source))
            for kind, lat, lon, source in resolved:
                self.values[kind + '_name'].set('')
                self.search_selection[kind].set('')
                self.values[kind + '_lat'].set(str(lat))
                self.values[kind + '_lon'].set(str(lon))
                self.selected_places[kind] = {'lat': lat, 'lon': lon, 'source': source}
            self.status.set('이동 기록의 처음과 끝 위치를 입력했습니다. 지도 앱의 출발·목적지와 다를 수 있습니다.')
        except (KeyError, TypeError, ValueError, OSError) as exc:
            messagebox.showwarning('이동 기록 확인', str(exc), parent=self)

    def fetch(self):
        try:
            origin, destination = self._points()
        except ValueError as exc:
            return messagebox.showwarning('입력 확인', str(exc), parent=self)
        provider, credentials = self.provider.get(), self._credentials()
        def done(route):
            route['status'] = 'available'
            route['origin'].update({k: v for k, v in origin.items() if k not in ('lat', 'lon')})
            route['destination'].update({k: v for k, v in destination.items() if k not in ('lat', 'lon')})
            self._save_field('route', route)
            self.route_info.set(f"재계산 경로 {len(route['points'])}개 좌표 저장 · 화면 경로와 일치 미확인")
            self.status.set('경로 좌표를 저장했습니다.')
        self._run('지도사에서 자동차 경로를 재조회하고 있습니다…',
                  lambda: fetch_route(provider, origin, destination, credentials), done)

    def export(self, multiple):
        from export_service import export_xlsx
        paths = [str(self.path)]
        if multiple:
            paths = list(filedialog.askopenfilenames(parent=self, title='캡처 정보 파일 선택',
                         initialdir=str(self.path.parent), filetypes=[('캡처 정보', '*.json')]))
            if not paths:
                return
        output = filedialog.asksaveasfilename(parent=self, title='엑셀로 저장', defaultextension='.xlsx',
                    initialfile='지도_수집_결과.xlsx', filetypes=[('Excel 통합 문서', '*.xlsx')])
        if not output:
            return
        def work():
            records = [json.loads(Path(path).read_text(encoding='utf-8')) for path in paths]
            return export_xlsx(records, output)
        self._run('엑셀 파일을 만들고 있습니다…', work, lambda path: self.status.set(f'엑셀 저장 완료: {path}'))

    def close(self):
        if self.worker:
            messagebox.showinfo('작업 진행 중', '작업이 끝난 후 창을 닫아 주세요.', parent=self)
            return
        self.grab_release()
        self.destroy()
        if hasattr(self.parent, '_refresh_journey_view'):
            self.parent._refresh_journey_view()
