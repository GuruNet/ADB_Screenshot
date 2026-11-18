"""ADB Screenshot captures the screen and app details of an Android device."""
from __future__ import annotations

import json
import copy
import ipaddress
import os
import queue
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from adb_service import AdbError, AdbService
from display_values import preview_column_order, table_value
from image_fit import load_preview, preview_size, render_preview
from ocr_service import OCRError, recognize
from route_metrics import effective_metrics, estimated_arrival


APP_NAME = "ADB Screenshot"


def parse_endpoint(host, port, port_label):
    """Accept Android's IP:port display as well as separate fields."""
    host, port = host.strip(), port.strip()
    if not host:
        raise ValueError("기기 IP 주소를 입력하세요. 페어링과 연결에 같은 IP 주소를 사용합니다.")
    embedded_port = None
    if host.startswith("["):
        closing = host.find("]")
        if closing == -1:
            raise ValueError("기기 IP 주소를 확인하세요.")
        suffix = host[closing + 1:]
        host = host[1:closing]
        if suffix:
            if not suffix.startswith(":"):
                raise ValueError("기기 IP 주소를 확인하세요.")
            embedded_port = suffix[1:]
    elif host.count(":") == 1:
        host, embedded_port = host.rsplit(":", 1)
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("기기 IP 주소를 확인하세요. 예: 192.168.0.10") from exc
    if embedded_port is not None:
        port = embedded_port
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError(f"{port_label} 포트를 1~65535 사이 숫자로 입력하세요. 페어링 포트와 연결 포트는 다릅니다.")
    return str(address), str(int(port))


def write_metadata(path, metadata):
    """Keep the prior JSON intact if the replacement cannot be completed."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".metadata-", suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


class ScreenshotApp(tk.Tk):
    def __init__(self, state_file=None):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1400x920")
        self.minsize(800, 580)
        self.adb = AdbService()
        self.results = queue.Queue()
        self.sheet_results = queue.Queue()
        self.sheet_worker = None
        self.sheet_pending = None
        self.sheet_revision = 0
        self.sheet_job = None
        self.sheet_data = {}
        self.sheet_records = []
        self.sheet_includes_open_outside = False
        self.sheet_columns = {}
        self.discovered_services = []
        self.discovery_target = None
        self.busy = False
        self.worker = None
        self.close_when_done = False
        self.photo = None
        self.large_photo = None
        self.preview_source = None
        self.preview_resize_job = None
        self.preview_render_cache = {}
        self.current_path = None
        self.current_metadata = None
        self.preview_path = None
        self.selected_phase = "start"
        self.start_thumb_photo = None
        self.finish_thumb_photo = None
        self.start_thumb_source = None
        self.finish_thumb_source = None
        self.state_file = Path(state_file) if state_file is not None else Path.home() / '.adb_screenshot' / 'active_journey.json'
        self.devices = []
        self.status = tk.StringVar(value="기기를 연결하거나 목록을 새로고침하세요.")
        self.host = tk.StringVar()
        self.port = tk.StringVar(value="5555")
        self.pair_port = tk.StringVar()
        self.pair_code = tk.StringVar()
        self.device = tk.StringVar()
        self.device_detail = tk.StringVar(value="선택된 기기가 없습니다.")
        self.app_detail = tk.StringVar(value="지도 앱을 확인해 주세요.")
        self.output_dir = tk.StringVar(value=str(Path.home() / "Pictures" / "ADB_Screenshots"))
        self.last_saved = tk.StringVar(value="저장된 스크린샷이 없습니다.")
        self.screen_time = tk.StringVar()
        self.distance = tk.StringVar()
        self.duration = tk.StringVar()
        self.toll = tk.StringVar()
        self.estimated_arrival = tk.StringVar()
        self._eta_manual = False
        self._setting_eta = False
        self.review_variables = {
            "screen_time": self.screen_time, "distance": self.distance,
            "duration": self.duration, "toll": self.toll,
        }
        self.baseline = self._values()
        self.ocr_status = tk.StringVar(value="캡처 후 자동 인식합니다. 결과를 반드시 확인해 주세요.")
        self._build()
        self.after(100, self._poll)
        self.after(100, self._poll_sheet_preview)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(200, self.refresh_devices)
        self.after(400, self._restore_active_journey)

    def _active_journey_path(self):
        try:
            state = json.loads(self.state_file.read_text(encoding='utf-8'))
            path = Path(state['metadata_path'])
            return path if path.is_absolute() and path.suffix.lower() == '.json' else None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _remember_active_journey(self, metadata_path):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        write_metadata(self.state_file, {'metadata_path': str(Path(metadata_path).resolve())})

    def _forget_active_journey(self, metadata_path):
        active = self._active_journey_path()
        if active is not None and active == Path(metadata_path).resolve():
            self.state_file.unlink(missing_ok=True)

    def _restore_active_journey(self):
        if self.current_path is not None:
            return
        if self.busy:
            self.after(300, self._restore_active_journey)
            return
        path = self._active_journey_path()
        if path is None:
            return
        try:
            metadata = json.loads(path.read_text(encoding='utf-8'))
            if (metadata.get('journey') or {}).get('status') not in ('in_progress', 'needs_attention'):
                return
        except (OSError, ValueError, TypeError, AttributeError):
            self.status.set('이전 여정 파일을 찾지 못했습니다. 기존 캡처 열기에서 파일을 확인하세요.')
            return
        self.open_capture_file(path)
        if self.current_path is not None:
            self.status.set('진행 중인 여정을 복원했습니다. 도착 화면을 준비한 뒤 종료하세요.')

    def _build(self):
        from ui_layout import build_main
        build_main(self)
        self.sheet_search.trace_add('write', lambda *_: self._schedule_sheet_preview())
        self.output_dir.trace_add('write', lambda *_: self._schedule_sheet_preview())
        self.preview_container.bind('<Configure>', self._schedule_preview_resize, add='+')
        for widget in (self.preview, self.large_preview, self.start_thumbnail, self.finish_thumbnail):
            widget.bind('<Configure>', self._schedule_preview_resize, add='+')
        for value in self.review_variables.values():
            value.trace_add('write', lambda *_: self._update_review_styles())
        for value in (self.screen_time, self.duration):
            value.trace_add('write', self._sync_eta)
        self.estimated_arrival.trace_add('write', self._on_eta_edit)
        self.eta_entry.bind('<FocusOut>', self._sync_eta, add='+')
        self.eta_entry.bind('<FocusIn>', lambda _: self.status.set(
            '도착 예정시간을 직접 수정할 수 있습니다. 값을 비우면 다시 자동 계산합니다.'), add='+')
        self._refresh_journey_view()

    def _set_eta(self, value, manual=False):
        self._eta_manual = manual
        self._setting_eta = True
        try:
            self.estimated_arrival.set(value)
        finally:
            self._setting_eta = False

    def _sync_eta(self, *_):
        if not self._eta_manual:
            value = estimated_arrival(self.screen_time.get(), self.duration.get()) if self.selected_phase == 'start' else ''
            self._set_eta(value)

    def _on_eta_edit(self, *_):
        if not self._setting_eta:
            # Leave an empty entry alone until focus-out, so replacing a selection works.
            self._eta_manual = bool(self.estimated_arrival.get().strip())
        self._update_review_styles()

    def _update_review_styles(self):
        variables = (*self.review_variables.values(), self.estimated_arrival)
        for entry, variable in zip(self.review_entries, variables):
            value = variable.get()
            entry.configure(style='Missing.TEntry' if self.current_path and not value.strip() else 'TEntry')
        self.refresh_review_hints()

    def _update_connection_badge(self, state=None):
        if state is None:
            serial = self.selected_serial()
            state = next((status for item, status in self.devices if item == serial), None)
        labels = {'device': '● 연결됨', 'offline': '오프라인', 'unauthorized': '승인 대기'}
        self.connection_state.set(labels.get(state, '연결 안 됨'))
        self.connection_badge.configure(style='Connected.Badge.TLabel' if state == 'device' else 'Neutral.Badge.TLabel')

    def toggle_connection(self):
        if self.connection_popup.state() != "withdrawn":
            self.connection_popup.withdraw()
            self.connection_toggle.configure(text="연결 설정 ▾")
        else:
            self.connection_popup.deiconify()
            self.connection_popup.lift()
            self.connection_toggle.configure(text="연결 설정 ▴")

    def _refresh_journey_view(self):
        journey = (self.current_metadata or {}).get("journey") or {}
        if not self.current_path:
            self.review_title.set('화면 인식 결과')
            self.preview_context.set('선택된 화면 없음')
        else:
            phase = ('도착 화면' if self.selected_phase == 'finish' else '출발 화면') if journey else '단일 캡처'
            self.review_title.set(f'{phase} · 인식 결과')
            self.preview_context.set(f'{phase} 선택됨')
        self.selected_filename.set(self.preview_path.name if self.preview_path else '선택된 캡처가 없습니다.')
        self.refresh_layout_state(self.selected_phase, bool(journey), bool(self.current_path))
        self._update_review_styles()
        finish = journey.get("finish") or {}
        state = journey.get("status")
        active = state in ("in_progress", "needs_attention")
        labels = {"in_progress": "출발 저장됨 · 도착 화면 대기",
                  "completed": "✓ 출발 · 도착 저장 완료",
                  "needs_attention": "확인 필요 · GPS 종료 또는 동기화를 다시 시도하세요"}
        text = labels.get(state, "대기 · 출발 화면을 캡처하세요")
        if state == "needs_attention" and finish.get("status") == "capturing":
            text = "도착 화면 확인 필요 · 도착 버튼을 다시 누르세요"
        if finish and finish.get("status") == "captured" and journey.get("app_match") == "mismatch":
            text += " · 출발·도착 앱 다름"
        elif finish and finish.get("status") == "captured" and journey.get("app_match") == "unknown":
            text += " · 출발·도착 앱 확인 필요"
        gps = journey.get("gps_status")
        track_status = ((self.current_metadata or {}).get("track") or {}).get("status")
        if gps in ("pending", "error") and track_status in ("recording", "waiting_for_gps", "awaiting_permission", "awaiting_confirmation"):
            gps = track_status
        gps_labels = {"disabled": "GPS 사용 안 함", "pending": "GPS 휴대폰 승인 대기",
                      "awaiting_confirmation": "GPS 휴대폰 승인 대기", "awaiting_permission": "GPS 권한 승인 대기",
                      "recording": "GPS 기록 중", "waiting_for_gps": "GPS 수신 대기", "stopped": "GPS 종료됨",
                      "not_started": "GPS가 시작되지 않았습니다", "no_fix": "GPS 위치를 수신하지 못했습니다",
                      "interrupted": "GPS 기록이 중단됐습니다",
                      "error": "GPS 확인 필요"}
        self.journey_status.set(text)
        attention = state == 'needs_attention' or (finish.get('status') == 'captured' and journey.get('app_match') in ('mismatch', 'unknown'))
        self.journey_badge.configure(style='Warning.Badge.TLabel' if attention else
                                    'Success.Badge.TLabel' if state == 'completed' else 'Badge.TLabel')
        if hasattr(self, "journey_gps_status"):
            gps_text = gps_labels.get(gps, "GPS 상태 미확인" if journey else "GPS 대기")
            reason = journey.get("gps_error") or ((self.current_metadata or {}).get("track") or {}).get("error")
            reasons = {"permission_denied": "위치 권한이 거부됐습니다", "location_disabled": "휴대폰 GPS가 꺼져 있습니다",
                       "location_error": "위치 서비스를 확인하세요", "storage_error": "휴대폰 저장 공간을 확인하세요",
                       "already_recording": "다른 GPS 기록이 진행 중입니다", "session_mismatch": "기록 세션이 일치하지 않습니다"}
            if gps == "error" and reason in reasons:
                gps_text += " · " + reasons[reason]
            self.journey_gps_status.set(gps_text)

        start = journey.get("start") or {}
        start_file = self.current_path if journey and self.current_path is not None else None
        finish_name = finish.get("screenshot")
        end_file = (self.current_path.parent / finish_name) if self.current_path and isinstance(finish_name, str) and Path(finish_name).name == finish_name else None
        for image_path, widget, attribute, status_var, info, placeholder in (
            (start_file, self.start_thumbnail, "start_thumb_photo", self.start_card_status, start, "출발 화면이 없습니다"),
            (end_file, self.finish_thumbnail, "finish_thumb_photo", self.finish_card_status, finish, "도착 화면이 없습니다"),
        ):
            try:
                if image_path is None or not image_path.is_file():
                    raise OSError
                source = load_preview(image_path, self)
                setattr(self, attribute.replace('_photo', '_source'), source)
                when = info.get("captured_at") or (self.current_metadata or {}).get("captured_at", "")
                try:
                    when = datetime.fromisoformat(when).astimezone().strftime("%m.%d %H:%M")
                except (ValueError, TypeError):
                    when = "시간 미확인"
                selected = (attribute == "start_thumb_photo" and self.selected_phase == "start") or (attribute == "finish_thumb_photo" and self.selected_phase == "finish")
                status_var.set(f"{when} · {'선택됨' if selected else '저장됨'}")
            except (OSError, tk.TclError):
                setattr(self, attribute, None)
                setattr(self, attribute.replace('_photo', '_source'), None)
                self.preview_render_cache.pop(attribute, None)
                widget.configure(image="", text=placeholder)
                status_var.set("아직 캡처하지 않았습니다")
        self._resize_preview()
        if not self.busy:
            available = bool(self.preview_path and self.preview_path.is_file())
            for button, enabled in ((self.departure_button, not active), (self.arrival_button, active),
                                    (self.start_select_button, bool(start_file and start_file.is_file())),
                                    (self.finish_select_button, bool(end_file and end_file.is_file())),
                                    (self.capture_button, not active), (self.open_button, not active),
                                    (self.gps_checkbox, not active), (self.original_button, available),
                                    (self.review_button, available), (self.recognize_button, available),
                                    (self.data_button, bool(self.current_path))):
                button.state(["!disabled" if enabled else "disabled"])
            for button, enabled in ((self.review_start_button, bool(start_file and start_file.is_file())),
                                    (self.review_finish_button, bool(end_file and end_file.is_file())),
                                    (self.selected_original_button, available)):
                button.state(["!disabled" if enabled else "disabled"])
            for entry in self.review_entries:
                entry.state(["!disabled" if available else "disabled"])
            self.refresh_review_hints()
            if hasattr(self, "gps_status_button"):
                self.gps_status_button.state(["!disabled" if active and ((self.current_metadata or {}).get("track") or {}).get("session_id") else "disabled"])
            self.arrival_button.configure(text="종료 재시도" if state == "needs_attention" else "도착 캡처 및 종료")
        self._schedule_sheet_preview()

    def _schedule_sheet_preview(self):
        if not hasattr(self, 'sheet_table'):
            return
        self.sheet_revision += 1
        self.sheet_pending = (self.sheet_revision, self.output_dir.get().strip(),
                              str(self.current_path) if self.current_path else None,
                              self.sheet_search.get().strip())
        self.sheet_data = {}
        self.sheet_records = []
        self._render_sheet_preview()
        self.sheet_note.set('저장 폴더의 수집 기록을 불러오는 중…')
        if self.sheet_job is not None:
            self.after_cancel(self.sheet_job)
        self.sheet_job = self.after(120, self._start_sheet_preview)

    def _start_sheet_preview(self):
        self.sheet_job = None
        if self.sheet_worker is not None or self.sheet_pending is None:
            return
        revision, folder, current_path, query = self.sheet_pending
        self.sheet_pending = None
        output = self.sheet_results
        def work():
            try:
                from export_service import preview_sheets
                from capture_repository import load_capture_records
                records = load_capture_records(folder, current_path)
                outside = bool(current_path and Path(current_path).parent.resolve() != Path(folder).expanduser().resolve())
                output.put((revision, preview_sheets(records, limit=500, query=query), records, outside, None))
            except Exception as exc:
                output.put((revision, None, [], False, str(exc)))
        self.sheet_worker = threading.Thread(target=work, daemon=True)
        self.sheet_worker.start()

    def _poll_sheet_preview(self):
        try:
            revision, data, records, outside, error = self.sheet_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.sheet_worker.join()
            self.sheet_worker = None
            if revision == self.sheet_revision:
                self.sheet_data = data or {}
                self.sheet_records = records
                self.sheet_includes_open_outside = outside
                self._render_sheet_preview()
                if error:
                    self.sheet_note.set('미리보기를 불러오지 못했습니다: ' + error)
            if self.sheet_pending is not None and self.sheet_job is None:
                self._start_sheet_preview()
        self.after(100, self._poll_sheet_preview)

    def _render_sheet_preview(self):
        if not hasattr(self, 'sheet_table'):
            return
        table = self.sheet_table
        self.record_count.set(f'{len(self.sheet_records)}건')
        table.delete(*table.get_children())
        sheet = self.sheet_data.get(self.sheet_choice.get())
        table.configure(displaycolumns='#all')
        if not sheet:
            table.configure(columns=())
            self.sheet_note.set('저장된 수집 기록이 없습니다.')
            if not self.busy:
                for button in (self.sheet_columns_button, self.export_all_button, self.export_filtered_button):
                    button.state(['disabled'])
            return
        headers = sheet['headers']
        columns = [f'c{index}' for index in range(len(headers))]
        table.configure(columns=columns)
        chosen = self.sheet_columns.get(self.sheet_choice.get(), preview_column_order(headers))
        chosen = [index for index in chosen if 0 <= index < len(headers)]
        table.configure(displaycolumns=[columns[index] for index in chosen])
        for column, header in zip(columns, headers):
            table.heading(column, text=header)
            label = str(header)
            width = 145 if '시각' in label else 265 if ('파일' in label or '이미지' in label) else 100
            if '도착 예정시간' in label:
                width = max(width, 10 * len(label) + 30)
            table.column(column, width=width, minwidth=70, stretch=False)
        for index, row in enumerate(sheet['rows']):
            table.insert('', 'end', values=tuple('' if value is None else table_value(header, value)
                                                for header, value in zip(headers, row)),
                         tags=('alternate',) if index % 2 else ())
        table.xview_moveto(0)
        table.yview_moveto(0)
        shown, total = len(sheet['rows']), sheet['total_rows']
        matched = sheet.get('matched_rows', total)
        if self.sheet_records:
            scope = '저장 폴더 + 다른 폴더의 열린 기록' if self.sheet_includes_open_outside else '저장 폴더'
            self.sheet_note.set(f'{scope} {len(self.sheet_records)}개 · 검색 {matched}/{total}행 · {shown}행 표시 · 선택 저장: 현재 시트의 검색 행과 표시 열 · 수정값 저장 후 반영')
        else:
            self.sheet_note.set('저장된 수집 기록이 없습니다.')
        if not self.busy:
            self.sheet_columns_button.state(['!disabled' if headers else 'disabled'])
            self.export_all_button.state(['!disabled' if self.sheet_records else 'disabled'])
            self.export_filtered_button.state(['!disabled' if self.sheet_records and matched and chosen else 'disabled'])

    def on_sheet_changed(self, _event=None):
        self._render_sheet_preview()

    def choose_sheet_columns(self):
        sheet_name = self.sheet_choice.get()
        sheet = self.sheet_data.get(sheet_name)
        if not sheet:
            return self._input_error(ValueError('먼저 미리볼 시트를 불러오세요.'))
        headers = sheet['headers']
        dialog = tk.Toplevel(self)
        dialog.title('표시할 항목 선택')
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text='표시할 열을 선택하세요. 선택 내보내기에도 적용됩니다.', padding=10).pack(anchor='w')
        frame = ttk.Frame(dialog, padding=(10, 0, 10, 0))
        frame.pack(fill='both', expand=True)
        items = tk.Listbox(frame, selectmode='multiple', exportselection=False, height=16, width=38)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=items.yview)
        items.configure(yscrollcommand=scroll.set)
        items.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        order = preview_column_order(headers)
        chosen = set(self.sheet_columns.get(sheet_name, order))
        for position, column in enumerate(order):
            items.insert('end', headers[column])
            if column in chosen:
                items.selection_set(position)
        actions = ttk.Frame(dialog, padding=10)
        actions.pack(fill='x')
        ttk.Button(actions, text='모두 선택', command=lambda: items.selection_set(0, 'end')).pack(side='left')
        def apply():
            selected = [order[position] for position in items.curselection()]
            if not selected:
                messagebox.showwarning('항목 선택', '표시할 항목을 하나 이상 선택하세요.', parent=dialog)
                return
            self.sheet_columns[sheet_name] = selected
            dialog.grab_release()
            dialog.destroy()
            self._render_sheet_preview()
        ttk.Button(actions, text='적용', command=apply).pack(side='right')
        ttk.Button(actions, text='취소', command=dialog.destroy).pack(side='right', padx=5)

    def _save_review_before_export(self):
        if self.current_path and self._values() != self.baseline:
            if not messagebox.askyesno('수정 사항 저장', '수정한 인식 결과를 저장한 뒤 내보낼까요?', parent=self):
                return False
            return self.save_review()
        return True

    def export_all_data(self):
        if not self._save_review_before_export():
            return
        folder = self.output_dir.get().strip()
        if not folder or not Path(folder).expanduser().is_dir():
            return self._input_error(ValueError('저장 폴더를 찾을 수 없습니다. 폴더를 다시 선택하세요.'))
        output = filedialog.asksaveasfilename(parent=self, title='전체 데이터 내보내기',
                    defaultextension='.xlsx', initialfile='전체_수집_데이터.xlsx',
                    filetypes=[('Excel 통합 문서', '*.xlsx')])
        if not output:
            return
        current = str(self.current_path) if self.current_path else None
        def work():
            from capture_repository import load_capture_records
            from export_service import export_xlsx
            records = load_capture_records(folder, current, require_folder=True)
            return export_xlsx(records, output), len(records)
        self._run('전체 수집 데이터를 엑셀로 저장하고 있습니다…', work,
                  lambda result: self.status.set(f'전체 {result[1]}개 기록 저장 완료: {result[0]}'))

    def export_filtered_data(self):
        sheet_name = self.sheet_choice.get()
        sheet = self.sheet_data.get(sheet_name)
        if not sheet:
            return self._input_error(ValueError('먼저 미리볼 시트를 불러오세요.'))
        columns = list(self.sheet_columns.get(sheet_name, preview_column_order(sheet['headers'])))
        if self.current_path and self._values() != self.baseline:
            if messagebox.askyesno('수정 사항 저장', '수정한 인식 결과를 저장하고 미리보기를 갱신할까요?', parent=self):
                if self.save_review():
                    self.status.set('미리보기를 갱신합니다. 확인 후 선택사항 내보내기를 다시 누르세요.')
            return
        folder = self.output_dir.get().strip()
        if not folder or not Path(folder).expanduser().is_dir():
            return self._input_error(ValueError('저장 폴더를 찾을 수 없습니다. 폴더를 다시 선택하세요.'))
        records = list(self.sheet_records)
        query = self.sheet_search.get().strip()
        output = filedialog.asksaveasfilename(parent=self, title='선택사항 내보내기',
                    defaultextension='.xlsx', initialfile='선택_수집_데이터.xlsx',
                    filetypes=[('Excel 통합 문서', '*.xlsx')])
        if not output:
            return
        def work():
            from export_service import export_filtered_xlsx
            return export_filtered_xlsx(records, output, sheet_name, query, columns)
        self._run('필터된 모든 행과 표시 항목을 엑셀로 저장하고 있습니다…', work,
                  lambda path: self.status.set(f'선택사항 저장 완료: {path}'))

    def _run(self, message, work, done):
        if self.busy:
            return
        self.busy = True
        self.status.set(message)
        for button in self.buttons:
            button.state(["disabled"])
        for entry in self.review_entries:
            entry.state(["disabled"])
        self.sheet_search_entry.state(["disabled"])
        self.output_entry.state(["disabled"])
        self.sheet_selector.configure(state="disabled")
        self.discovered_selector.configure(state="disabled")
        self.device_box.configure(state="disabled")

        def worker():
            try:
                self.results.put((done, work(), None))
            except Exception as exc:
                self.results.put((done, None, exc))
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            done, value, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            # The result is queued as the worker's final action; join before releasing Tk references.
            self.worker.join()
            self.worker = None
            self.busy = False
            for button in self.buttons:
                button.state(["!disabled"])
            for entry in self.review_entries:
                entry.state(["!disabled"])
            self.sheet_search_entry.state(["!disabled"])
            self.output_entry.state(["!disabled"])
            self.sheet_selector.configure(state="readonly")
            self.discovered_selector.configure(state="readonly")
            self.device_box.configure(state="readonly")
            if error:
                self.status.set("작업 실패: " + str(error))
                messagebox.showerror("작업 실패", str(error), parent=self)
            else:
                try:
                    done(value)
                except (OSError, tk.TclError, ValueError) as exc:
                    self.status.set("결과 표시 실패: " + str(exc))
                    messagebox.showerror("결과 표시 실패", str(exc), parent=self)
            if not self.busy:
                self._refresh_journey_view()
        if self.close_when_done and not self.busy:
            self.close_when_done = False
            if self._close():
                return
        self.after(100, self._poll)

    def _endpoint(self, port_variable, port_label):
        host, port = parse_endpoint(self.host.get(), port_variable.get(), port_label)
        self.host.set(host)
        port_variable.set(port)
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

    def _input_error(self, exc):
        self.status.set(str(exc))
        messagebox.showwarning("입력 확인", str(exc), parent=self)

    def connect_device(self):
        try:
            endpoint = self._endpoint(self.port, "연결")
        except ValueError as exc:
            return self._input_error(exc)
        self._run("ADB 연결 중…", lambda: self.adb.connect(endpoint), self._connected)

    def _connected(self, result):
        self.status.set(str(result))
        self.refresh_devices()

    def pair_device(self):
        try:
            endpoint = self._endpoint(self.pair_port, "페어링")
            code = self.pair_code.get().strip()
            if not code:
                raise ValueError("기기에 표시된 페어링 코드를 입력하세요.")
        except ValueError as exc:
            return self._input_error(exc)
        self._run("기기 페어링 중…", lambda: self.adb.pair(endpoint, code), self._paired)

    def _paired(self, result):
        self.pair_code.set("")
        self.status.set(str(result) + " 연결 포트를 확인하고 연결을 눌러 주세요.")

    def disconnect_device(self):
        try:
            endpoint = self._endpoint(self.port, "연결")
        except ValueError as exc:
            return self._input_error(exc)
        self._run("ADB 연결 해제 중…", lambda: self.adb.disconnect(endpoint), self._connected)

    def discover_wireless(self):
        self.discovery_status.set('무선 디버깅 기기를 찾고 있습니다…')
        def work():
            try:
                return self.adb.discover_wireless(), None
            except AdbError as exc:
                return [], str(exc)
        def done(result):
            services, error = result
            if error:
                self.discovery_status.set('자동 검색에 실패했습니다. IP와 포트를 직접 입력할 수 있습니다.')
                self.status.set('무선 자동 검색 실패: ' + error)
            else:
                self._show_discovery(services)
        self._run('무선 디버깅 기기를 찾고 있습니다…', work, done)

    def _show_discovery(self, services):
        self.discovered_services = services
        self.discovered_selector['values'] = [
            f"{'연결' if item['kind'] == 'connect' else '페어링'} · {item['endpoint']} · {item['instance']}"
            for item in services
        ]
        self.discovered_choice.set('')
        if not services:
            self.discovery_status.set('기기를 찾지 못했습니다. 무선 디버깅을 켜거나 IP와 포트를 직접 입력하세요.')
            self.status.set('자동 발견된 무선 기기가 없습니다.')
            return
        serial = self.selected_serial() or ''
        matches = [index for index, item in enumerate(services) if serial and
                   (item['instance'].startswith(f'adb-{serial}-') or item['endpoint'] == serial or serial.startswith(item['instance']))]
        connect_matches = [index for index in matches if services[index]['kind'] == 'connect']
        if len(connect_matches) == 1:
            index = connect_matches[0]
        elif len(matches) == 1:
            index = matches[0]
        else:
            self.discovery_status.set(f'{len(services)}개 후보를 찾았습니다. 현재 기기와 주소를 확인한 뒤 목록에서 선택하세요.')
            self.status.set('무선 디버깅 후보를 찾았습니다.')
            return
        self.discovered_selector.current(index)
        self.on_discovery_selected()
        if len(connect_matches) == 1 and index == connect_matches[0] and ':' not in serial and services[index]['instance'].startswith(f'adb-{serial}-'):
            self._connect_known_discovery(services[index], serial)

    def _connect_known_discovery(self, item, usb_serial):
        from gps_tracker import GpsTracker
        endpoint = str(item['endpoint'])
        def work():
            tracker = GpsTracker(self.adb)
            expected = tracker.device_identity(usb_serial)
            if expected.get('source') != 'ro.serialno':
                return False, '기기 식별을 확인할 수 없어 후보만 표시합니다. 직접 연결하세요.'
            existing = {serial for serial, _ in self.adb.list_devices()}
            try:
                self.adb.connect(endpoint)
                states = dict(self.adb.list_devices())
                if states.get(endpoint) != 'device':
                    if endpoint not in existing and endpoint in states:
                        self.adb.disconnect(endpoint)
                    return False, '무선 기기가 아직 연결되지 않았습니다. 페어링과 연결 상태를 확인하세요.'
                actual = tracker.device_identity(endpoint)
                if actual != expected:
                    if endpoint not in existing:
                        self.adb.disconnect(endpoint)
                    return False, '자동 연결한 기기가 일치하지 않아 선택하지 않았습니다.'
                return True, ''
            except AdbError:
                return False, '자동 연결되지 않았습니다. 휴대폰에서 페어링한 뒤 다시 찾거나 직접 연결하세요.'
        def done(result):
            connected, reason = result
            if not connected:
                self.discovery_status.set(reason)
                self.status.set(reason)
                return
            self.discovery_target = endpoint
            self.discovery_status.set('페어링된 휴대폰에 자동 연결했습니다.')
            self.refresh_devices()
        self._run('발견한 휴대폰에 연결하고 있습니다…', work, done)

    def on_discovery_selected(self, _event=None):
        index = self.discovered_selector.current()
        if not 0 <= index < len(self.discovered_services):
            return
        item = self.discovered_services[index]
        serial = self.selected_serial() or ''
        if serial and ':' not in serial and not (item['instance'].startswith(f'adb-{serial}-') or serial.startswith(item['instance'])):
            if not messagebox.askyesno('다른 기기 후보', '선택된 USB 기기와 이름이 다른 후보입니다. 이 주소를 사용하시겠습니까?', parent=self.connection_popup):
                self.discovered_choice.set('')
                return
        self.host.set(str(item['host']))
        (self.port if item['kind'] == 'connect' else self.pair_port).set(str(item['port']))
        for other in self.discovered_services:
            if other['instance'] == item['instance'] and other['host'] == item['host'] and other['kind'] != item['kind']:
                (self.port if other['kind'] == 'connect' else self.pair_port).set(str(other['port']))
        if item['kind'] == 'connect':
            self.discovery_status.set('연결 주소를 입력했습니다. 페어링된 기기면 연결을 누르세요.')
        else:
            self.discovery_status.set('페어링 주소를 입력했습니다. 휴대폰의 코드를 직접 입력하세요.')
        self.status.set('선택한 무선 주소를 입력했습니다.')

    def refresh_devices(self):
        self._run("기기 목록 확인 중…", self.adb.list_devices, self._show_devices)

    def _show_devices(self, devices):
        previous = self.discovery_target or self.selected_serial()
        self.discovery_target = None
        self.devices = devices
        labels = [f"{serial}  [{state}]" for serial, state in devices]
        self.device_box["values"] = labels
        index = next((i for i, (serial, state) in enumerate(devices) if serial == previous), None)
        if index is None:
            index = next((i for i, (_, state) in enumerate(devices) if state == "device"), None)
        if index is None:
            self.device.set("")
            self._update_connection_badge(devices[0][1] if devices else None)
            if devices:
                self.device_detail.set(self._device_state_message(devices[0][1]))
            else:
                self.device_detail.set("연결된 기기가 없습니다. USB 케이블 또는 무선 연결을 확인하세요.")
            self.app_detail.set("지도 앱을 확인할 수 없습니다.")
            self.status.set("기기 목록을 확인했습니다.")
        else:
            self.device_box.current(index)
            self.inspect_device()

    def selected_serial(self):
        i = self.device_box.current()
        return self.devices[i][0] if 0 <= i < len(self.devices) else None

    @staticmethod
    def _device_state_message(state):
        if state == "unauthorized":
            return "기기가 승인 대기 중입니다. 휴대폰 잠금을 풀고 USB 디버깅을 허용한 후 새로고침하세요."
        if state == "offline":
            return "기기가 오프라인입니다. USB 케이블 또는 무선 연결을 확인한 후 새로고침하세요."
        return f"기기를 사용할 수 없습니다. 현재 상태: {state}"

    def inspect_device(self):
        serial = self.selected_serial()
        if not serial:
            return self._input_error(ValueError("먼저 연결된 기기를 선택하세요."))
        state = next((state for device, state in self.devices if device == serial), None)
        self._update_connection_badge(state)
        if state != "device":
            self.device_detail.set(self._device_state_message(state or "알 수 없음"))
            self.app_detail.set("지도 앱을 확인할 수 없습니다.")
            self.status.set("기기 연결 상태를 확인하세요.")
            return
        def work():
            return self.adb.device_info(serial), self.adb.identify_app(serial)
        self._run("기기와 지도 앱 확인 중…", work, self._show_device_info)

    def _show_device_info(self, result):
        info, (name, package) = result
        self._update_connection_badge()
        self.device_detail.set(f"기기: {info.get('model') or '알 수 없음'}  ·  Android {info.get('android_version') or '알 수 없음'}")
        self.app_detail.set(f"현재 화면 앱: {name or '알 수 없음'}" + (f" ({package})" if package else ""))
        self.status.set("기기와 현재 화면 앱을 확인했습니다.")

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.output_dir.get() if Path(self.output_dir.get()).is_dir() else str(Path.home()))
        if folder:
            self.output_dir.set(folder)

    def start_journey(self):
        from journey_service import start_journey
        serial = self.selected_serial()
        if not serial:
            return self._input_error(ValueError("먼저 연결된 휴대폰을 선택하세요."))
        if (self.current_metadata or {}).get("journey", {}).get("status") in ("in_progress", "needs_attention"):
            return self._input_error(ValueError("진행 중인 여정의 도착 처리를 먼저 완료하세요."))
        active_path = self._active_journey_path()
        if active_path:
            try:
                active = json.loads(active_path.read_text(encoding='utf-8'))
                if (active.get('journey') or {}).get('status') in ('in_progress', 'needs_attention'):
                    return self._input_error(ValueError("저장된 진행 중 여정이 있습니다. 기존 캡처 열기에서 해당 기록을 먼저 종료하세요."))
            except (OSError, ValueError, TypeError, AttributeError):
                if not messagebox.askyesno("이전 여정 확인", "이전 여정 파일을 찾거나 읽을 수 없습니다. 휴대폰에서 GPS 기록이 계속 중일 수 있습니다.\n"
                                            "복구 경로만 해제하고 새 여정을 시작할까요? 기존 파일은 삭제하지 않습니다.", parent=self):
                    return
                try:
                    self.state_file.unlink(missing_ok=True)
                except OSError as exc:
                    return self._input_error(ValueError(f"이전 여정 경로를 해제하지 못했습니다: {exc}"))
        if not self._allow_switch():
            return
        folder, enable_gps = self.output_dir.get().strip(), self.journey_gps.get()
        def done(result):
            path, metadata = result
            self.current_path, self.current_metadata = Path(path), metadata
            self.selected_phase = "start"
            self._load_phase("start")
            gps = metadata.get("journey", {}).get("gps_status")
            if gps == "pending":
                self.status.set("출발 화면 저장 완료 · 휴대폰 보조 앱에서 GPS 기록 시작을 승인하세요.")
            elif gps == "error":
                self.status.set("출발 화면은 저장됐습니다. GPS·엑셀에서 보조 앱 설치와 기록 상태를 확인하세요.")
            else:
                self.status.set("출발 화면을 저장했습니다. 도착하면 종료 버튼을 누르세요.")
        self._run("출발 화면을 캡처하고 있습니다…",
                  lambda: start_journey(self.adb, serial, folder, write_metadata, enable_gps=enable_gps,
                                        recognizer=recognize, on_saved=self._remember_active_journey), done)

    def finish_journey(self):
        from journey_service import finish_journey
        serial = self.selected_serial()
        if not serial:
            return self._input_error(ValueError("먼저 출발에 사용한 휴대폰을 선택하세요."))
        if not self.current_path or not self.current_metadata or not self.current_metadata.get("journey"):
            return self._input_error(ValueError("먼저 출발 화면을 캡처하거나 진행 중인 기록을 열어 주세요."))
        if not self._allow_switch():
            return
        metadata_path = self.current_path.with_suffix(".json")
        def done(result):
            _path, metadata = result
            self.current_metadata = metadata
            self.selected_phase = "finish"
            self._load_phase("finish")
            journey = metadata.get("journey") or {}
            if journey.get("status") == "completed":
                try:
                    self._forget_active_journey(metadata_path)
                except OSError:
                    pass
            if journey.get("status") == "needs_attention":
                self.status.set("도착 화면은 저장됐습니다. GPS 종료가 확인되지 않으면 휴대폰 알림에서 중지한 뒤 종료를 다시 시도하세요.")
            elif journey.get("gps_status") in ("no_fix", "not_started", "error"):
                self.status.set("도착 화면은 저장됐습니다. GPS 기록 상태를 확인하세요.")
            elif journey.get("app_match") != "confirmed":
                self.status.set("출발·도착 화면을 저장했습니다. 두 화면의 지도 앱을 확인하세요.")
            else:
                self.status.set("출발과 도착 화면을 저장했습니다.")
        self._run("도착 화면을 저장하고 GPS 기록을 확인하고 있습니다…",
                  lambda: finish_journey(self.adb, serial, metadata_path, write_metadata, recognizer=recognize), done)

    def sync_journey_gps(self):
        from gps_tracker import GpsTracker
        serial = self.selected_serial()
        metadata = copy.deepcopy(self.current_metadata or {})
        journey, track = metadata.get("journey") or {}, metadata.get("track") or {}
        session = track.get("session_id")
        if not serial or not self.current_path or not session:
            return self._input_error(ValueError("GPS 기록이 있는 여정과 출발에 사용한 기기를 선택하세요."))
        path = self.current_path.with_suffix(".json")
        tracker = GpsTracker(self.adb)
        def work():
            identity = tracker.device_identity(serial)
            if journey.get("device_identity") and identity != journey["device_identity"]:
                raise ValueError("출발할 때 연결한 휴대폰을 선택하세요.")
            if not journey.get("device_identity") and journey.get("device_serial") != serial:
                raise ValueError("출발할 때 사용한 기기 연결을 선택하세요.")
            result = tracker.read(serial, session)
            old_points, new_points = track.get("points") or [], result.get("points") or []
            if len(new_points) < len(old_points) or new_points[:len(old_points)] != old_points:
                raise ValueError("휴대폰의 GPS 기록이 기존 기록과 달라 저장하지 않았습니다.")
            result['device_serial'], result['device_identity'] = serial, identity
            metadata['track'] = result
            state = result.get('status')
            if state in ('pending', 'awaiting_confirmation', 'awaiting_permission'):
                journey['gps_status'] = 'pending'
            elif state in ('recording', 'waiting_for_gps'):
                journey['gps_status'] = state
            elif state == 'stopped':
                journey['gps_status'] = 'stopped' if new_points else ('no_fix' if result.get('started_at') else 'not_started')
            else:
                journey['gps_status'] = 'error'
            metadata['journey'] = journey
            write_metadata(path, metadata)
            return metadata
        def done(updated):
            self.current_metadata = updated
            self._refresh_journey_view()
            self.status.set("GPS 상태와 기록을 동기화했습니다.")
        self._run("GPS 상태를 확인하고 있습니다…", work, done)

    def _phase(self, phase):
        if phase == "finish":
            journey = (self.current_metadata or {}).get("journey") or {}
            info = journey.get("finish") or {}
            name = info.get("screenshot")
            if not isinstance(name, str) or not name or Path(name).name != name or not self.current_path:
                raise ValueError("저장된 도착 화면이 없습니다.")
            return self.current_path.parent / name, info
        if not self.current_path or not self.current_metadata:
            raise ValueError("저장된 출발 화면이 없습니다.")
        return self.current_path, self.current_metadata

    def _load_phase(self, phase):
        path, info = self._phase(phase)
        review, ocr = info.get("review") or {}, info.get("ocr") or {}
        if not isinstance(review, dict) or not isinstance(ocr, dict):
            raise ValueError("저장된 인식 결과의 형식이 올바르지 않습니다.")
        self._display_image(path, info)
        self.selected_phase = phase
        self._set_review_values(effective_metrics(review, ocr), self._saved_eta_override(review))
        self.baseline = self._values()
        if review.get("status") == "reviewed":
            self.ocr_status.set("저장한 검토 결과입니다.")
        elif ocr.get("status") == "failed":
            self.ocr_status.set("인식 실패: " + ocr.get("error", "알 수 없는 오류") + "\n직접 입력할 수 있습니다.")
        else:
            self.ocr_status.set("인식값을 원본 화면과 비교해 확인하세요.")
        self._refresh_journey_view()

    def select_journey_phase(self, phase):
        if self.busy or phase not in ("start", "finish"):
            return
        if phase == self.selected_phase and self.preview_path and self.preview_path.is_file():
            return
        if not self._allow_switch():
            return
        try:
            self._load_phase(phase)
            self.status.set("출발 화면을 표시합니다." if phase == "start" else "도착 화면을 표시합니다.")
        except (OSError, ValueError, tk.TclError) as exc:
            self._input_error(ValueError(str(exc)))

    def capture(self):
        serial = self.selected_serial()
        if not serial:
            return self._input_error(ValueError("먼저 연결된 기기를 선택하세요."))
        if (self.current_metadata or {}).get("journey", {}).get("status") in ("in_progress", "needs_attention"):
            return self._input_error(ValueError("진행 중인 여정을 먼저 종료하세요."))
        folder = self.output_dir.get().strip()
        if not folder:
            return self._input_error(ValueError("스크린샷을 저장할 폴더를 지정하세요."))
        if not self._allow_switch():
            return
        def work():
            directory = Path(folder).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            try:
                before = self.adb.identify_app(serial)
            except AdbError:
                before = (None, None)
            now = datetime.now().astimezone()
            filename = now.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
            path = directory / (filename + ".png")
            self.adb.capture_screenshot(serial, path)
            try:
                after = self.adb.identify_app(serial)
            except AdbError:
                after = (None, None)
            consistent = bool(before[0] and before[1] and before == after)
            metadata = {
                "captured_at": now.isoformat(), "device_serial": serial,
                "app": before[0] if consistent else None,
                "package": before[1] if consistent else None,
                "app_before": {"name": before[0], "package": before[1]},
                "app_after": {"name": after[0], "package": after[1]},
                "app_detection": "confirmed" if consistent else "uncertain",
                "screenshot": path.name,
                "ocr": {"status": "pending"},
                "review": {"status": "needs_review", "screen_time": "", "distance": "", "duration": "",
                           "toll": "", "estimated_arrival": ""},
            }
            meta_path = path.with_suffix(".json")
            try:
                write_metadata(meta_path, metadata)
            except OSError as exc:
                raise OSError(f"이미지는 {path}에 저장했지만 앱 정보 저장에 실패했습니다: {exc}") from exc
            return path, metadata
        self._run("스크린샷을 수집하고 있습니다…", work, self._show_capture)

    def _show_capture(self, result):
        path, meta = result
        self.current_path, self.current_metadata = path, meta
        self.selected_phase = "start"
        self._set_review_values({})
        self.baseline = self._values()
        self.ocr_status.set("문자를 인식하고 있습니다…")
        self._display_image(path, meta)
        self._start_ocr(path, meta)

    def _display_image(self, path, meta):
        source = load_preview(path, self)
        old_source, old_path, old_photo = self.preview_source, self.preview_path, self.photo
        old_large_photo = self.large_photo
        old_cache = dict(self.preview_render_cache)
        old_text = self.preview.cget('text')
        try:
            self.preview_source = source
            self.preview_path = Path(path)
            self.preview_render_cache.pop('photo', None)
            self.preview_render_cache.pop('large_photo', None)
            self.preview.configure(image='', text='이미지를 준비하고 있습니다.')
            self._resize_preview()
        except Exception:
            self.preview_source, self.preview_path, self.photo = old_source, old_path, old_photo
            self.large_photo = old_large_photo
            self.preview_render_cache = old_cache
            try:
                self.preview.configure(image=old_photo if old_photo else '', text=old_text)
                self.large_preview.configure(image=old_large_photo if old_large_photo else '', text=old_text)
            except tk.TclError:
                pass
            raise
        name = meta.get("app") or "확인 불가 (캡처 전후 앱 확인 필요)"
        self.app_detail.set(f"캡처된 화면 앱: {name}" + (f" ({meta['package']})" if meta.get("package") else ""))
        info_path = self.current_path.with_suffix('.json') if self.current_path else path.with_suffix('.json')
        self.last_saved.set(f"저장됨: {path}\n앱 정보: {info_path}")

    def _schedule_preview_resize(self, _event=None):
        if self.preview_resize_job is not None:
            self.after_cancel(self.preview_resize_job)
        self.preview_resize_job = self.after(120, self._run_preview_resize)

    def _run_preview_resize(self):
        self.preview_resize_job = None
        self._resize_preview()

    def _resize_preview(self):
        for source, widget, panel, attribute in (
            (self.preview_source, self.preview, self.preview_container, 'photo'),
            (self.preview_source, self.large_preview, self.large_preview_container, 'large_photo'),
            (self.start_thumb_source, self.start_thumbnail, self.start_tab, 'start_thumb_photo'),
            (self.finish_thumb_source, self.finish_thumbnail, self.finish_tab, 'finish_thumb_photo'),
        ):
            if source is None:
                continue
            width = min(widget.winfo_width() - 8, panel.winfo_width() - 24)
            height = widget.winfo_height() - 8
            if width < 1 or height < 1:
                continue
            size = preview_size(source, width, height)
            previous = self.preview_render_cache.get(attribute)
            if previous and previous[0] is source and previous[1] == size:
                continue
            photo = render_preview(source, size, self)
            setattr(self, attribute, photo)
            self.preview_render_cache[attribute] = (source, size)
            widget.configure(image=photo, text="")

    def show_original(self):
        path = self.preview_path
        if path is None:
            return self._input_error(ValueError("먼저 스크린샷을 열거나 캡처하세요."))
        try:
            image = tk.PhotoImage(file=str(path))
        except (OSError, tk.TclError) as exc:
            return self._input_error(ValueError(f"원본 스크린샷을 열 수 없습니다: {exc}"))
        viewer = tk.Toplevel(self)
        viewer.title("스크린샷 원본 보기")
        viewer.geometry(f"{min(900, self.winfo_screenwidth() - 80)}x{min(700, self.winfo_screenheight() - 80)}")
        viewer.rowconfigure(1, weight=1)
        viewer.columnconfigure(0, weight=1)
        ttk.Label(viewer, text="원본 크기 · 스크롤하여 화면 전체를 확인하세요.", padding=6).grid(row=0, column=0, sticky="w")
        canvas = tk.Canvas(viewer, highlightthickness=0)
        canvas.grid(row=1, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(viewer, orient="vertical", command=canvas.yview)
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(viewer, orient="horizontal", command=canvas.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set,
                         scrollregion=(0, 0, image.width(), image.height()))
        canvas.create_image(0, 0, image=image, anchor="nw")
        canvas.image = image

        def scroll(event):
            steps = int(event.delta / 120) if abs(event.delta) >= 120 else event.delta
            canvas.yview_scroll(-steps, "units")
        canvas.bind("<MouseWheel>", scroll)
        canvas.bind("<Button-4>", lambda event: canvas.yview_scroll(-1, "units"))
        canvas.bind("<Button-5>", lambda event: canvas.yview_scroll(1, "units"))
        return viewer

    def open_data(self):
        if self.current_path is None or self.current_metadata is None:
            return self._input_error(ValueError("먼저 스크린샷을 캡처하거나 기존 캡처를 열어 주세요."))
        if self._values() != self.baseline:
            if not messagebox.askyesno("수정 사항 저장", "수정한 인식 결과를 저장한 뒤 GPS·엑셀 화면을 열까요?", parent=self):
                return
            if not self.save_review():
                return
        from data_window import DataWindow
        return DataWindow(self, write_metadata)

    def _start_ocr(self, path, meta, phase="start"):
        metadata_path = self.current_path.with_suffix(".json")
        def work():
            try:
                ocr = {"status": "completed", **recognize(path)}
                if phase == "finish":
                    ocr["estimated_arrival"] = ""
            except OCRError as exc:
                ocr = {"status": "failed", "error": str(exc), "raw_text": "", "screen_time": "",
                       "distance": "", "duration": "", "toll": "", "estimated_arrival": ""}
            updated = copy.deepcopy(meta)
            target = updated if phase == "start" else updated["journey"]["finish"]
            target["ocr"] = ocr
            write_metadata(metadata_path, updated)
            return updated
        self._run("이미지는 저장되었습니다. 화면의 글자를 인식하고 있습니다…", work, self._show_ocr)

    def recognize_again(self):
        if self.current_path is None:
            return self._input_error(ValueError("먼저 스크린샷을 열거나 캡처하세요."))
        if self._allow_switch():
            self.ocr_status.set("문자를 다시 인식하고 있습니다…")
            self._start_ocr(self.preview_path or self.current_path, self.current_metadata, self.selected_phase)

    def _show_ocr(self, metadata):
        self.current_metadata = metadata
        target = metadata if self.selected_phase == "start" else metadata["journey"]["finish"]
        ocr = target["ocr"]
        if ocr["status"] == "failed":
            self.ocr_status.set("인식 실패: " + ocr.get("error", "알 수 없는 오류") + "\n직접 입력할 수 있습니다.")
            self.status.set("이미지는 저장되었습니다. 인식 오류를 확인해 주세요.")
        else:
            review = target.get("review") or {}
            override = self._saved_eta_override(review)
            self._set_review_values(ocr, override)
            stored = effective_metrics(review, ocr)
            self.baseline = (*[stored[key] for key in self.review_variables], override)
            self.ocr_status.set("자동 인식값은 확정되지 않았습니다. 원본과 비교해 수정해 주세요.")
            self.status.set("인식 완료. 결과를 확인한 후 저장해 주세요.")

    def save_review(self):
        if self.current_path is None or self.current_metadata is None:
            self._input_error(ValueError("먼저 스크린샷을 캡처하세요."))
            return False
        metadata = copy.deepcopy(self.current_metadata)
        target = metadata if self.selected_phase == "start" else metadata["journey"]["finish"]
        self._sync_eta()
        values = {key: var.get().strip() for key, var in self.review_variables.items()}
        target["review"] = {
            "status": "reviewed", "reviewed_at": datetime.now().astimezone().isoformat(),
            **values, "estimated_arrival": self.estimated_arrival.get().strip(),
        }
        if self._eta_manual and self.selected_phase == 'start':
            target["review"]["estimated_arrival_manual"] = True
        try:
            write_metadata(self.current_path.with_suffix(".json"), metadata)
        except OSError as exc:
            self._input_error(ValueError(f"검토 결과를 저장하지 못했습니다: {exc}"))
            return False
        self.current_metadata = metadata
        self.baseline = self._values()
        self._schedule_sheet_preview()
        self.ocr_status.set("검토 결과를 저장했습니다. 다시 수정하고 저장할 수 있습니다.")
        self.status.set("검토 결과를 이미지와 함께 저장했습니다.")
        return True

    def _values(self):
        values = tuple(var.get().strip() for var in self.review_variables.values())
        return (*values, self.estimated_arrival.get().strip() if self._eta_manual else "")

    def _saved_eta_override(self, review):
        if self.selected_phase == 'start' and review.get("status") == 'reviewed' and review.get("estimated_arrival_manual") is True:
            return str(review.get("estimated_arrival") or "").strip()
        return ""

    def _set_review_values(self, values, override=""):
        self._eta_manual = False
        for key, var in self.review_variables.items():
            var.set(values.get(key, ""))
        if override:
            self._set_eta(override, manual=True)

    def _allow_switch(self):
        if self.current_path is None or self._values() == self.baseline:
            return True
        choice = messagebox.askyesnocancel("수정 사항 저장", "수정한 값을 저장할까요?\n저장하지 않으면 변경 사항이 사라집니다.", parent=self)
        if choice is None:
            return False
        return self.save_review() if choice else True

    def open_capture(self):
        if (self.current_metadata or {}).get("journey", {}).get("status") in ("in_progress", "needs_attention"):
            return self._input_error(ValueError("진행 중인 여정을 먼저 종료하세요. 종료 후 다른 기록을 열 수 있습니다."))
        if not self._allow_switch():
            return
        selected = filedialog.askopenfilename(parent=self, title="기존 캡처 열기",
            filetypes=[("스크린샷 또는 앱 정보", "*.png *.json"), ("모든 파일", "*")])
        if not selected:
            return
        return self.open_capture_file(selected)

    def open_capture_file(self, selected):
        chosen = Path(selected)
        meta_path = chosen.with_suffix(".json")
        phase = "start"
        if chosen.suffix.lower() == ".png" and chosen.name.endswith("_finish.png") and not meta_path.exists():
            meta_path = chosen.with_name(chosen.name[:-len("_finish.png")] + "_start.json")
            phase = "finish"
        previous = (self.current_path, self.current_metadata, self.preview_path, self.selected_phase)
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or not all(isinstance(metadata.get(key, {}), dict) for key in ("ocr", "review")):
                raise ValueError("앱 정보 파일의 형식이 올바르지 않습니다.")
            screenshot = metadata.get("screenshot", "")
            if not isinstance(screenshot, str) or Path(screenshot).name != screenshot:
                raise ValueError("올바른 스크린샷 파일 이름이 없습니다.")
            path = meta_path.parent / screenshot
            if path.with_suffix(".json") != meta_path or path.suffix.lower() != ".png":
                raise ValueError("스크린샷과 앱 정보 파일이 일치하지 않습니다.")
            if phase == "finish" and ((metadata.get("journey") or {}).get("finish") or {}).get("screenshot") != chosen.name:
                raise ValueError("도착 화면과 출발 기록이 일치하지 않습니다.")
            self.current_path, self.current_metadata = path, metadata
            self._load_phase(phase)
        except (OSError, ValueError, KeyError, tk.TclError) as exc:
            self.current_path, self.current_metadata, self.preview_path, self.selected_phase = previous
            return self._input_error(ValueError(f"기존 캡처를 열 수 없습니다: {exc}"))
        self.status.set("기존 캡처를 열었습니다.")

    def _close(self):
        if self.busy:
            if messagebox.askyesno("작업 진행 중", "작업이 완료된 후 프로그램을 종료할까요?\n진행 중인 작업은 계속됩니다.", parent=self):
                self.close_when_done = True
                self.status.set("진행 중인 작업이 완료되면 종료합니다.")
            return False
        if not self._allow_switch():
            return False
        self.destroy()
        return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument('--open', metavar='PATH', help='기존 캡처 PNG 또는 JSON 열기')
    parser.add_argument('--gps', action='store_true', help='기존 캡처의 GPS·엑셀 화면 열기')
    parser.add_argument('--geometry', metavar='WIDTHxHEIGHT', help='초기 창 크기')
    args = parser.parse_args()
    if args.gps and not args.open:
        parser.error('--gps는 --open과 함께 사용하세요.')
    window = ScreenshotApp()
    if args.geometry:
        window.geometry(args.geometry)
    if args.open:
        window.open_capture_file(args.open)
    if args.gps and window.current_path is not None:
        window.after(1000, window.open_data)
    window.mainloop()


if __name__ == "__main__":
    main()
