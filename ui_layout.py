"""Native desktop layout for connection, capture, and review."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk

from image_fit import load_preview, render_preview
from ui_surfaces import configure_surfaces


def build_main(app):
    style = ttk.Style(app)
    # Native Aqua ignores most color options; clam provides consistent contrast on both OS appearances.
    style.theme_use('clam')
    background, surface, elevated = '#10171d', '#192128', '#242e38'
    border, text, muted, accent = '#394755', '#f4f7fb', '#b2bfce', '#1765ed'
    field = '#10171e'
    app.configure(background=background)
    app.option_add('*TCombobox*Listbox.background', field)
    app.option_add('*TCombobox*Listbox.foreground', text)
    app.option_add('*TCombobox*Listbox.selectBackground', accent)
    app.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
    style.configure('.', background=surface, foreground=text, font=('TkDefaultFont', 10))
    style.configure('TFrame', background=surface)
    style.configure('Canvas.TFrame', background=background)
    style.configure('TLabel', background=surface, foreground=text)
    style.configure('Muted.TLabel', background=surface, foreground=muted)
    style.configure('CanvasMuted.TLabel', background=background, foreground=muted)
    style.configure('AppTitle.TLabel', background=background, foreground=text, font=('TkDefaultFont', 20, 'bold'))
    style.configure('CardTitle.TLabel', background=surface, foreground=text, font=('TkDefaultFont', 12, 'bold'))
    style.configure('TLabelframe', background=surface, bordercolor=surface, relief='flat', borderwidth=0)
    style.configure('TLabelframe.Label', background=surface, foreground=muted, font=('TkDefaultFont', 10, 'bold'))
    configure_surfaces(app, style, surface=surface, elevated=elevated, border=border,
                       accent=accent, text=text, muted=muted)
    style.configure('TButton', padding=(8, 4), width=0)
    style.configure('Primary.TButton', padding=(9, 4), font=('TkDefaultFont', 10, 'bold'), width=0)
    style.configure('Segment.TButton', padding=(12, 5), font=('TkDefaultFont', 10, 'bold'))
    style.configure('Selected.Segment.TButton', padding=(12, 5), font=('TkDefaultFont', 10, 'bold'))
    style.configure('TEntry', fieldbackground=field, foreground=text, insertcolor=text,
                    bordercolor=border, lightcolor=border, darkcolor=border, padding=(5, 2))
    style.map('TEntry', fieldbackground=[('disabled', surface)], foreground=[('disabled', muted)],
              bordercolor=[('focus', '#689bff')])
    style.configure('Missing.TEntry', fieldbackground='#252215', bordercolor='#d2a441',
                    lightcolor='#d2a441', darkcolor='#d2a441')
    style.map('Missing.TEntry', fieldbackground=[('disabled', surface)],
              bordercolor=[('disabled', border), ('focus', '#ffce63'), ('!focus', '#d2a441')])
    style.configure('TCombobox', fieldbackground=field, background=elevated, foreground=text,
                    arrowcolor=muted, bordercolor=border, lightcolor=border, darkcolor=border, padding=(5, 2))
    style.map('TCombobox', fieldbackground=[('disabled', surface), ('readonly', field)],
              foreground=[('disabled', muted), ('readonly', text)],
              selectbackground=[('readonly', field)], selectforeground=[('readonly', text)],
              bordercolor=[('focus', '#689bff')])

    # Clam's selected indicator is an X, which is easy to mistake for an unchecked state.
    def checkbox_image(checked, disabled=False):
        image = tk.PhotoImage(master=app, width=14, height=14)
        edge = '#8090a3' if not disabled else border
        fill = (accent if not disabled else '#354455') if checked else field
        image.put(edge, to=(1, 1, 13, 13))
        image.put(fill, to=(2, 2, 12, 12))
        if checked:
            ink = text if not disabled else muted
            for x, y in ((4, 7), (5, 8), (6, 9), (7, 8), (8, 7), (9, 6), (10, 5)):
                image.put(ink, to=(x, y, x + 2, y + 2))
        return image
    app._checkbox_images = tuple(checkbox_image(*args) for args in ((False,), (True,), (False, True), (True, True)))
    style.element_create('Premium.indicator', 'image', app._checkbox_images[0],
                         ('disabled', 'selected', app._checkbox_images[3]),
                         ('disabled', app._checkbox_images[2]), ('selected', app._checkbox_images[1]),
                         sticky='w', padding=(0, 0, 5, 0))
    style.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('Premium.indicator', {'side': 'left', 'sticky': ''}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
            ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
    style.configure('TCheckbutton', background=surface, foreground=text, focuscolor=surface)
    style.map('TCheckbutton', background=[('active', surface)], foreground=[('disabled', '#8b919a')])
    style.configure('Toggle.TCheckbutton', background=surface, foreground=text, focuscolor='#689bff')
    for orientation in ('Vertical', 'Horizontal'):
        scrollbar = f'{orientation}.TScrollbar'
        style.configure(scrollbar, background='#3b4b5d', troughcolor=field, bordercolor=field,
                        lightcolor='#3b4b5d', darkcolor='#3b4b5d', arrowcolor=muted,
                        arrowsize=11, gripsize=0)
        style.map(scrollbar, background=[('disabled', field), ('active', '#4b6484')])
    style.configure('Treeview', background=field, fieldbackground=field, foreground=text,
                    bordercolor=border, lightcolor=border, darkcolor=border, rowheight=27)
    style.map('Treeview', background=[('selected', '#203c64')], foreground=[('selected', '#ffffff')])
    style.configure('Treeview.Heading', background=elevated, foreground='#cbd7e9', relief='flat',
                    font=('TkDefaultFont', 10, 'bold'), padding=(5, 4))
    style.map('Treeview.Heading', background=[('active', '#2b3d57')])
    style.configure('TNotebook', background=background, borderwidth=0)
    style.configure('TNotebook.Tab', background=elevated, foreground=muted, padding=(10, 5))
    style.map('TNotebook.Tab', background=[('selected', surface)], foreground=[('selected', text)])
    style.configure('TSeparator', background=border)

    outer = ttk.Frame(app, padding=10, style='Canvas.TFrame')
    outer.pack(fill='both', expand=True)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(1, weight=4, minsize=310)
    outer.rowconfigure(2, weight=1, minsize=115)

    heading = ttk.Frame(outer, style='Canvas.TFrame')
    heading.grid(row=0, column=0, sticky='ew', pady=(0, 10))
    heading.columnconfigure(1, weight=1)
    icon_source = load_preview(Path(__file__).parent / 'assets' / 'adb-screenshot-icon.png', app)
    app.header_icon = render_preview(icon_source, (38, 38), app)
    tk.Label(heading, image=app.header_icon, background=background, borderwidth=0,
             highlightthickness=0, padx=0, pady=0).grid(row=0, column=0, padx=(0, 8))
    ttk.Label(heading, text='지도 경로 수집', style='AppTitle.TLabel').grid(row=0, column=1, sticky='w')
    app.journey_status = tk.StringVar(master=app, value='대기 · 출발 화면을 캡처하세요')
    app.journey_badge = ttk.Label(heading, textvariable=app.journey_status, style='Badge.TLabel',
                                  wraplength=450, justify='right')
    app.journey_badge.grid(row=0, column=2, sticky='e')

    workspace = ttk.Frame(outer, style='Canvas.TFrame')
    workspace.grid(row=1, column=0, sticky='nsew')
    workspace.rowconfigure(0, weight=1)
    workspace.columnconfigure(1, weight=1, minsize=300)
    left = ttk.Frame(workspace, style='Card.TFrame', width=260, padding=14)
    left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
    left.grid_propagate(False)
    left.columnconfigure(0, weight=1)
    app.connection_state = tk.StringVar(master=app, value='미연결')
    left_heading = ttk.Frame(left)
    left_heading.grid(row=0, column=0, sticky='ew', pady=(0, 10))
    ttk.Label(left_heading, text='연결 기기', style='CardTitle.TLabel').pack(side='left')
    app.connection_badge = ttk.Label(left_heading, textvariable=app.connection_state,
                                    style='Neutral.Badge.TLabel')
    app.connection_badge.pack(side='left', padx=(10, 0))
    app.device_box = ttk.Combobox(left, textvariable=app.device, state='readonly', width=15)
    app.device_box.grid(row=1, column=0, sticky='ew')
    app.device_box.bind('<<ComboboxSelected>>', lambda event: app.inspect_device())
    device_detail_label = ttk.Label(left, textvariable=app.device_detail, style='Muted.TLabel', wraplength=225)
    device_detail_label.grid(row=2, column=0, sticky='ew', pady=(5, 8))
    connection_actions = ttk.Frame(left)
    connection_actions.grid(row=3, column=0, sticky='ew')
    connection_actions.columnconfigure((0, 1), weight=1, uniform='connection')
    app.refresh_button = ttk.Button(connection_actions, text='새로고침', command=app.refresh_devices)
    app.refresh_button.grid(row=0, column=0, sticky='ew', padx=(0, 5))
    app.connection_toggle = ttk.Button(connection_actions, text='연결 설정 ▾', command=app.toggle_connection)
    app.connection_toggle.grid(row=0, column=1, sticky='ew', padx=(5, 0))
    separator1 = ttk.Separator(left)
    separator1.grid(row=4, column=0, sticky='ew', pady=12)
    app_info = ttk.Frame(left)
    app_info.grid(row=5, column=0, sticky='ew')
    app_info.columnconfigure(0, weight=1)
    app_info_label = ttk.Label(app_info, textvariable=app.app_detail, style='Muted.TLabel', wraplength=150)
    app_info_label.grid(row=0, column=0, sticky='w')
    app.identify_button = ttk.Button(app_info, text='앱 확인', command=app.inspect_device)
    app.identify_button.grid(row=0, column=1, sticky='e', padx=(5, 0))
    separator2 = ttk.Separator(left)
    separator2.grid(row=6, column=0, sticky='ew', pady=12)
    gps = ttk.Frame(left)
    gps.grid(row=7, column=0, sticky='ew')
    gps.columnconfigure(0, weight=1)
    app.journey_gps = tk.BooleanVar(master=app, value=True)
    app.journey_gps_status = tk.StringVar(master=app, value='GPS · 시작 전')
    app.gps_checkbox = ttk.Checkbutton(gps, text='GPS 기록 함께 시작', variable=app.journey_gps,
                                       style='Toggle.TCheckbutton')
    app.gps_checkbox.grid(row=0, column=0, sticky='w')
    app.gps_status_button = ttk.Button(gps, text='상태 확인', command=app.sync_journey_gps)
    app.gps_status_button.grid(row=0, column=1, sticky='e', padx=(2, 0))
    gps_status_label = ttk.Label(gps, textvariable=app.journey_gps_status, style='Muted.TLabel', wraplength=225)
    gps_status_label.grid(row=1, column=0, columnspan=2, sticky='w', pady=(5, 0))
    separator3 = ttk.Separator(left)
    separator3.grid(row=8, column=0, sticky='ew', pady=16)
    storage_title = ttk.Label(left, text='저장 위치', style='CardTitle.TLabel')
    storage_title.grid(row=9, column=0, sticky='w', pady=(0, 8))
    app.output_entry = ttk.Entry(left, textvariable=app.output_dir, width=15)
    app.output_entry.grid(row=10, column=0, sticky='ew')
    storage_actions = ttk.Frame(left)
    storage_actions.grid(row=11, column=0, sticky='ew', pady=(8, 0))
    storage_actions.columnconfigure((0, 1, 2), weight=1)
    app.folder_button = ttk.Button(storage_actions, text='폴더 선택', command=app.choose_folder)
    app.folder_button.grid(row=0, column=0, sticky='ew', padx=(0, 3))
    app.open_button = ttk.Button(storage_actions, text='기록 열기', command=app.open_capture)
    app.open_button.grid(row=0, column=1, sticky='ew', padx=3)
    app.data_button = ttk.Button(storage_actions, text='GPS·엑셀', command=app.open_data)
    app.data_button.grid(row=0, column=2, sticky='ew', padx=(3, 0))
    app.last_saved_summary = tk.StringVar(master=app)
    def update_saved_summary(*_):
        first = app.last_saved.get().split('\n', 1)[0]
        app.last_saved_summary.set(Path(first[5:].strip()).name if first.startswith('저장됨:') else first)
    app.last_saved.trace_add('write', update_saved_summary)
    update_saved_summary()
    recent_title = ttk.Label(left, text='최근 파일', style='Muted.TLabel')
    recent_title.grid(row=12, column=0, sticky='w', pady=(12, 3))
    recent_file = ttk.Label(left, textvariable=app.last_saved_summary, wraplength=225, style='Muted.TLabel')
    recent_file.grid(row=13, column=0, sticky='ew')

    center = ttk.Frame(workspace, style='Card.TFrame', padding=8)
    center.grid(row=0, column=1, sticky='nsew')
    center.columnconfigure(0, weight=1)
    center.rowconfigure(1, weight=1)
    toolbar = ttk.Frame(center)
    toolbar.grid(row=0, column=0, sticky='ew', pady=(0, 10))
    toolbar.columnconfigure(0, weight=1)
    view_tabs = ttk.Frame(toolbar)
    view_tabs.grid(row=0, column=0, sticky='w')
    tools = ttk.Frame(toolbar)
    tools.grid(row=0, column=1, sticky='e')
    app.compare_view_button = ttk.Button(view_tabs, text='화면 비교', style='Selected.Segment.TButton')
    app.compare_view_button.pack(side='left')
    app.screenshot_view_button = ttk.Button(view_tabs, text='스크린샷', style='Segment.TButton')
    app.screenshot_view_button.pack(side='left', padx=(2, 0))
    app.capture_button = ttk.Button(tools, text='단일 캡처', command=app.capture)
    app.capture_button.pack(side='left', padx=(0, 6))
    app.original_button = ttk.Button(tools, text='원본 보기', command=app.show_original)
    app.original_button.pack(side='left')
    compare = ttk.Frame(center)
    compare.grid(row=1, column=0, sticky='nsew')
    compare.columnconfigure((0, 1), weight=1, uniform='screens')
    compare.rowconfigure(0, weight=1)
    app.start_tab = ttk.Frame(compare, style='Capture.Card.TFrame', padding=10)
    app.finish_tab = ttk.Frame(compare, style='Capture.Card.TFrame', padding=10)
    app.start_tab.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
    app.finish_tab.grid(row=0, column=1, sticky='nsew', padx=(4, 0))

    def preview_label(parent, row, placeholder):
        viewport = ttk.Frame(parent)
        viewport.grid(row=row, column=0, sticky='nsew')
        label = ttk.Label(viewport, text=placeholder, style='Preview.TLabel', anchor='center',
                          justify='center', wraplength=110)
        label.place(relwidth=1, relheight=1)
        return label

    app.start_card_status = tk.StringVar(master=app, value='아직 캡처하지 않았습니다')
    app.finish_card_status = tk.StringVar(master=app, value='아직 캡처하지 않았습니다')
    for card, label, variable, phase, button_name in (
        (app.start_tab, '출발 화면', app.start_card_status, 'start', 'start_select_button'),
        (app.finish_tab, '도착 화면', app.finish_card_status, 'finish', 'finish_select_button'),
    ):
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)
        bar = ttk.Frame(card)
        bar.grid(row=0, column=0, sticky='ew')
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, text=label, style='CardTitle.TLabel').grid(row=0, column=0, sticky='w')
        button = ttk.Button(bar, text='보기', command=lambda selected=phase: app.select_journey_phase(selected))
        button.grid(row=0, column=1, sticky='e')
        setattr(app, button_name, button)
        ttk.Label(card, textvariable=variable, style='Muted.TLabel', wraplength=160).grid(
            row=1, column=0, sticky='w', pady=(3, 7))
    app.start_thumbnail = preview_label(app.start_tab, 2, '출발 화면이 없습니다')
    app.finish_thumbnail = preview_label(app.finish_tab, 2, '도착 화면이 없습니다')
    app.start_thumbnail.bind('<Button-1>', lambda event: app.select_journey_phase('start'))
    app.finish_thumbnail.bind('<Button-1>', lambda event: app.select_journey_phase('finish'))
    app.departure_button = ttk.Button(app.start_tab, text='출발 화면 캡처', command=app.start_journey, style='Primary.TButton')
    app.departure_button.grid(row=3, column=0, sticky='ew', pady=(6, 0))
    app.arrival_button = ttk.Button(app.finish_tab, text='도착 캡처 및 종료', command=app.finish_journey, style='Primary.TButton')
    app.arrival_button.grid(row=3, column=0, sticky='ew', pady=(6, 0))
    app.large_preview_container = ttk.Frame(center)
    app.large_preview_container.grid(row=1, column=0, sticky='nsew')
    app.large_preview_container.columnconfigure(0, weight=1)
    app.large_preview_container.rowconfigure(0, weight=1)
    app.large_preview = preview_label(app.large_preview_container, 0, '출발 또는 도착 화면을 선택하세요')
    app.large_preview_container.grid_remove()
    app.capture_view = tk.StringVar(master=app, value='compare')
    def show_capture_view(view):
        app.capture_view.set(view)
        if view == 'screenshot':
            compare.grid_remove()
            app.large_preview_container.grid()
        else:
            app.large_preview_container.grid_remove()
            compare.grid()
        app.compare_view_button.configure(style='Selected.Segment.TButton' if view == 'compare' else 'Segment.TButton')
        app.screenshot_view_button.configure(style='Selected.Segment.TButton' if view == 'screenshot' else 'Segment.TButton')
    app.show_capture_view = show_capture_view
    app.compare_view_button.configure(command=lambda: show_capture_view('compare'))
    app.screenshot_view_button.configure(command=lambda: show_capture_view('screenshot'))

    review = ttk.Frame(workspace, style='Card.TFrame', padding=14, width=280)
    review.grid(row=0, column=2, sticky='nsew', padx=(10, 0))
    review.grid_propagate(False)
    review.columnconfigure(0, weight=1)
    review.rowconfigure(8, weight=1)
    review_heading = ttk.Label(review, text='인식 결과 검토', style='CardTitle.TLabel')
    review_heading.grid(row=0, column=0, sticky='w', pady=(0, 12))
    app.review_title = tk.StringVar(master=app, value='선택한 화면 · 인식 결과')
    phase_tabs = ttk.Frame(review)
    phase_tabs.grid(row=1, column=0, sticky='ew', pady=(0, 10))
    phase_tabs.columnconfigure((0, 1), weight=1, uniform='phases')
    app.review_start_button = ttk.Button(phase_tabs, text='출발', style='Segment.TButton',
                                         command=lambda: app.select_journey_phase('start'))
    app.review_finish_button = ttk.Button(phase_tabs, text='도착', style='Segment.TButton',
                                          command=lambda: app.select_journey_phase('finish'))
    app.review_start_button.grid(row=0, column=0, sticky='ew', padx=(0, 2))
    app.review_finish_button.grid(row=0, column=1, sticky='ew', padx=(2, 0))
    app.ocr_status_label = ttk.Label(review, textvariable=app.ocr_status, style='Muted.TLabel', wraplength=245)
    app.ocr_status_label.grid(row=2, column=0, sticky='ew', pady=(0, 10))
    form = ttk.Frame(review)
    form.grid(row=3, column=0, sticky='ew')
    form.columnconfigure(1, weight=1)
    app.review_entries = []
    review_labels = []
    hint_labels = []
    review_hints = []
    style.configure('InputHint.TLabel', foreground='#efc873', background=style.lookup('Missing.TEntry', 'fieldbackground') or field,
                    font=('TkDefaultFont', 9))
    def update_hint(entry, variable, hint):
        if not variable.get().strip() and app.focus_get() is not entry and not entry.instate(['disabled']):
            hint.place(x=6, rely=.5, anchor='w')
        else:
            hint.place_forget()
    form_fields = (('화면 표시 시간', app.screen_time), ('경로 거리', app.distance),
                   ('예상 소요시간', app.duration), ('예상 통행료', app.toll), ('도착 예정시간', app.estimated_arrival))
    for row, (label, variable) in enumerate(form_fields):
        field_label = ttk.Label(form, text=label)
        field_label.grid(row=row, column=0, sticky='w', padx=(0, 10))
        review_labels.append(field_label)
        entry = ttk.Entry(form, textvariable=variable, width=10)
        entry.grid(row=row, column=1, sticky='ew', pady=(0, 7))
        app.review_entries.append(entry)
        hint = ttk.Label(entry, text='직접 입력' if variable is app.estimated_arrival else '입력 필요',
                         style='InputHint.TLabel', takefocus=False)
        hint_labels.append(hint)
        refresh = lambda *_, e=entry, v=variable, h=hint: update_hint(e, v, h)
        variable.trace_add('write', refresh)
        entry.bind('<FocusIn>', refresh, add='+')
        entry.bind('<FocusOut>', refresh, add='+')
        hint.bind('<Button-1>', lambda event, e=entry: e.focus_set())
        review_hints.append(refresh)
    app.refresh_review_hints = lambda: [refresh() for refresh in review_hints]
    app.eta_entry = app.review_entries[-1]
    app.eta_entry.grid_configure(pady=(0, 2))
    review_actions = ttk.Frame(review)
    review_actions.grid(row=4, column=0, sticky='ew', pady=(8, 0))
    review_actions.columnconfigure(1, weight=1)
    app.recognize_button = ttk.Button(review_actions, text='다시 인식', command=app.recognize_again)
    app.recognize_button.grid(row=0, column=0, sticky='ew', padx=(0, 6))
    app.review_button = ttk.Button(review_actions, text='검토 완료 및 저장', command=app.save_review, style='Primary.TButton')
    app.review_button.grid(row=0, column=1, sticky='ew')
    review_separator = ttk.Separator(review)
    review_separator.grid(row=5, column=0, sticky='ew', pady=16)
    app.preview_context = tk.StringVar(master=app, value='선택된 화면 없음')
    ttk.Label(review, textvariable=app.preview_context, style='CardTitle.TLabel').grid(
        row=7, column=0, sticky='ew', pady=(0, 6))
    app.screenshot_tab = ttk.Frame(review)
    app.screenshot_tab.grid(row=8, column=0, sticky='nsew')
    app.screenshot_tab.columnconfigure(0, weight=0)
    app.screenshot_tab.columnconfigure(1, weight=1)
    app.screenshot_tab.rowconfigure(0, weight=1)
    thumbnail = ttk.Frame(app.screenshot_tab, width=86)
    thumbnail.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
    app.preview = ttk.Label(thumbnail, text='선택 없음', style='Preview.TLabel', anchor='center', wraplength=75)
    app.preview.place(relwidth=1, relheight=1)
    app.preview_container = app.screenshot_tab
    selected_info = ttk.Frame(app.screenshot_tab)
    selected_info.grid(row=0, column=1, sticky='ew')
    app.selected_filename = tk.StringVar(master=app, value='저장된 화면이 없습니다.')
    filename_label = ttk.Label(selected_info, textvariable=app.selected_filename, wraplength=145,
                               justify='left', font=('TkDefaultFont', 9))
    filename_label.pack(anchor='w', pady=(0, 12))
    app.selected_original_button = ttk.Button(selected_info, text='원본 크게 보기', command=app.show_original)
    app.selected_original_button.pack(anchor='w')
    previous_capture_kind = None
    def refresh_layout_state(phase, has_journey, has_capture):
        nonlocal previous_capture_kind
        at_finish = has_journey and phase == 'finish'
        review_labels[1].configure(text='주행거리' if at_finish else '경로 거리')
        review_labels[2].configure(text='소요시간' if at_finish else '예상 소요시간')
        review_labels[3].configure(text='실 통행료' if at_finish else '예상 통행료')
        hint_labels[3].configure(text='없음' if at_finish else '입력 필요')
        for widget in (review_labels[-1], app.eta_entry):
            if at_finish:
                widget.grid_remove()
            else:
                widget.grid()
        app.start_tab.configure(style='Selected.Card.TFrame' if has_journey and phase == 'start' else 'Capture.Card.TFrame')
        app.finish_tab.configure(style='Selected.Card.TFrame' if has_journey and phase == 'finish' else 'Capture.Card.TFrame')
        app.review_start_button.configure(style='Selected.Segment.TButton' if has_journey and phase == 'start' else 'Segment.TButton')
        app.review_finish_button.configure(style='Selected.Segment.TButton' if has_journey and phase == 'finish' else 'Segment.TButton')
        if has_capture and not has_journey and previous_capture_kind != 'single':
            show_capture_view('screenshot')
        previous_capture_kind = ('journey' if has_journey else 'single') if has_capture else None
    app.refresh_layout_state = refresh_layout_state

    sheet = ttk.Frame(outer, style='Card.TFrame', padding=12)
    app.sheet_panel = sheet
    sheet.grid(row=2, column=0, sticky='nsew', pady=(10, 0))
    sheet.columnconfigure(0, weight=1)
    sheet.rowconfigure(2, weight=1)
    sheet_heading = ttk.Frame(sheet)
    sheet_heading.grid(row=0, column=0, sticky='ew', pady=(0, 8))
    ttk.Label(sheet_heading, text='수집 기록', style='CardTitle.TLabel').pack(side='left')
    app.record_count = tk.StringVar(master=app, value='0건')
    ttk.Label(sheet_heading, textvariable=app.record_count, style='Neutral.Badge.TLabel').pack(side='left', padx=(8, 0))
    sheet_tools = ttk.Frame(sheet)
    sheet_tools.grid(row=1, column=0, sticky='ew', pady=(0, 8))
    app.sheet_choice = tk.StringVar(master=app, value='수집 목록')
    app.sheet_selector = ttk.Combobox(sheet_tools, textvariable=app.sheet_choice, state='readonly', width=18,
        values=('수집 목록', '출발 목적지', '추천 경로 좌표', '실제 이동 궤적'))
    app.sheet_selector.pack(side='left')
    app.sheet_selector.bind('<<ComboboxSelected>>', app.on_sheet_changed)
    search_label = ttk.Label(sheet_tools, text='기록 검색', style='Muted.TLabel')
    search_label.pack(side='left', padx=(12, 6))
    app.sheet_search = tk.StringVar(master=app)
    app.sheet_search_entry = ttk.Entry(sheet_tools, textvariable=app.sheet_search, width=20)
    app.sheet_search_entry.pack(side='left')
    app.sheet_columns_button = ttk.Button(sheet_tools, text='표시 항목', command=app.choose_sheet_columns)
    app.sheet_columns_button.pack(side='left', padx=10)
    app.export_all_button = ttk.Button(sheet_tools, text='전체 내보내기', command=app.export_all_data)
    app.export_all_button.pack(side='right', padx=(8, 0))
    app.export_filtered_button = ttk.Button(sheet_tools, text='선택 내보내기', command=app.export_filtered_data)
    app.export_filtered_button.pack(side='right')
    sheet_body = ttk.Frame(sheet)
    sheet_body.grid(row=2, column=0, sticky='nsew')
    sheet_body.rowconfigure(0, weight=1)
    sheet_body.columnconfigure(0, weight=1)
    app.sheet_table = ttk.Treeview(sheet_body, show='headings', height=4, selectmode='browse')
    app.sheet_table.tag_configure('alternate', background=field)
    app.sheet_table.grid(row=0, column=0, sticky='nsew')
    sheet_y = ttk.Scrollbar(sheet_body, orient='vertical', command=app.sheet_table.yview)
    sheet_y.grid(row=0, column=1, sticky='ns')
    sheet_x = ttk.Scrollbar(sheet_body, orient='horizontal', command=app.sheet_table.xview)
    sheet_x.grid(row=1, column=0, sticky='ew')
    app.sheet_table.configure(yscrollcommand=sheet_y.set, xscrollcommand=sheet_x.set)
    app.sheet_note = tk.StringVar(master=app, value='저장된 수집 기록이 없습니다.')
    sheet_note = ttk.Label(sheet, textvariable=app.sheet_note, width=1, style='Muted.TLabel')
    sheet_note.grid(row=3, column=0, sticky='ew', pady=(5, 0))
    footer = ttk.Frame(outer, style='Card.TFrame', padding=(10, 5))
    footer.grid(row=3, column=0, sticky='ew', pady=(8, 0))
    footer.columnconfigure(1, weight=1)
    ttk.Label(footer, textvariable=app.connection_state, style='Muted.TLabel').grid(row=0, column=0, padx=(0, 15))
    ttk.Label(footer, textvariable=app.status, width=1, style='Muted.TLabel').grid(row=0, column=1, sticky='ew')

    # Keep the capture workspace visible while the user edits wireless settings.
    app.connection_popup = tk.Toplevel(app)
    app.connection_popup.title('무선 연결 및 페어링')
    app.connection_popup.transient(app)
    app.connection_popup.withdraw()
    app.connection_popup.columnconfigure(0, weight=1)
    app.connection_popup.protocol('WM_DELETE_WINDOW', app.toggle_connection)
    app.connection_details = ttk.Frame(app.connection_popup, padding=12)
    app.connection_details.grid(row=0, column=0, sticky='nsew')
    app.connection_details.columnconfigure(1, weight=1)
    app.connection_details.columnconfigure(3, weight=1)
    ttk.Label(app.connection_details, text='IP').grid(row=0, column=0, sticky='w')
    ttk.Entry(app.connection_details, textvariable=app.host, width=13).grid(row=0, column=1, sticky='ew', padx=(4, 6))
    ttk.Label(app.connection_details, text='연결 포트').grid(row=0, column=2, sticky='w')
    ttk.Entry(app.connection_details, textvariable=app.port, width=7).grid(row=0, column=3, sticky='ew', padx=(4, 0))
    ttk.Label(app.connection_details, text='페어링 포트').grid(row=1, column=0, sticky='w', pady=(3, 0))
    ttk.Entry(app.connection_details, textvariable=app.pair_port, width=8).grid(row=1, column=1, sticky='ew', padx=(4, 6), pady=(3, 0))
    ttk.Label(app.connection_details, text='코드').grid(row=1, column=2, sticky='w', pady=(3, 0))
    ttk.Entry(app.connection_details, textvariable=app.pair_code, width=8, show='•').grid(row=1, column=3, sticky='ew', padx=(4, 0), pady=(3, 0))
    connect_actions = ttk.Frame(app.connection_details)
    connect_actions.grid(row=2, column=0, columnspan=4, sticky='w', pady=(4, 0))
    app.connect_button = ttk.Button(connect_actions, text='연결', command=app.connect_device)
    app.connect_button.pack(side='left', padx=(0, 3))
    app.disconnect_button = ttk.Button(connect_actions, text='연결 해제', command=app.disconnect_device)
    app.disconnect_button.pack(side='left', padx=3)
    app.pair_button = ttk.Button(connect_actions, text='페어링', command=app.pair_device)
    app.pair_button.pack(side='left', padx=3)
    discovery = ttk.Frame(app.connection_details)
    discovery.grid(row=3, column=0, columnspan=4, sticky='ew', pady=(6, 0))
    discovery.columnconfigure(1, weight=1)
    app.discover_button = ttk.Button(discovery, text='자동 찾기', command=app.discover_wireless)
    app.discover_button.grid(row=0, column=0, padx=(0, 6))
    app.discovered_choice = tk.StringVar(master=app)
    app.discovered_selector = ttk.Combobox(discovery, textvariable=app.discovered_choice,
                                           state='readonly', width=32)
    app.discovered_selector.grid(row=0, column=1, sticky='ew')
    app.discovered_selector.bind('<<ComboboxSelected>>', app.on_discovery_selected)
    ttk.Label(app.connection_details, text='처음에는 휴대폰의 무선 디버깅 페어링이 필요합니다.',
              wraplength=360).grid(row=4, column=0, columnspan=4, sticky='w', pady=(4, 0))
    app.discovery_status = tk.StringVar(master=app, value='핫스팟에서 검색이 안 되면 IP와 포트를 직접 입력하세요.')
    ttk.Label(app.connection_details, textvariable=app.discovery_status, wraplength=360).grid(
        row=5, column=0, columnspan=4, sticky='w')


    layout_mode = None
    def adapt_layout(event):
        nonlocal layout_mode
        if event.widget is not app:
            return
        compact = event.width < 1150 or event.height < 760
        narrow = event.width < 1000
        if (compact, narrow) == layout_mode:
            return
        layout_mode = (compact, narrow)
        outer.configure(padding=6 if compact else 10)
        outer.rowconfigure(1, minsize=320 if compact else 420)
        outer.rowconfigure(2, minsize=108 if compact else 175)
        heading.grid_configure(pady=(0, 5 if compact else 10))
        app.journey_badge.configure(wraplength=270 if compact else 450)
        left.configure(width=205 if narrow else 250 if compact else 260, padding=7 if compact else 14)
        review.configure(width=225 if narrow else 260 if compact else 280, padding=7 if compact else 14)
        thumbnail.configure(width=64 if narrow else 76 if compact else 86)
        left.grid_configure(padx=(0, 5 if compact else 10))
        review.grid_configure(padx=(5 if compact else 10, 0))
        center.configure(padding=5 if compact else 8)
        for card in (app.start_tab, app.finish_tab):
            card.configure(padding=5 if compact else 10)
        style.configure('.', font=('TkDefaultFont', 9 if compact else 10))
        style.configure('CardTitle.TLabel', font=('TkDefaultFont', 10 if compact else 12, 'bold'))
        style.configure('AppTitle.TLabel', font=('TkDefaultFont', 17 if compact else 20, 'bold'))
        style.configure('TButton', padding=(4, 1) if compact else (8, 4))
        style.configure('Primary.TButton', padding=(4, 1) if compact else (9, 4), font=('TkDefaultFont', 9 if compact else 10, 'bold'))
        style.configure('Segment.TButton', padding=(8, 2) if compact else (16, 5))
        style.configure('Selected.Segment.TButton', padding=(8, 2) if compact else (16, 5))
        style.configure('TEntry', padding=(3, 0) if compact else (5, 2))
        style.configure('TCombobox', padding=(3, 0) if compact else (5, 2))
        style.configure('Treeview', rowheight=23 if compact else 29)
        style.configure('Treeview.Heading', padding=(5, 2) if compact else (5, 4))
        app.start_select_button.configure(text='보기' if compact else '출발 보기')
        app.finish_select_button.configure(text='보기' if compact else '도착 보기')
        for separator in (separator1, separator2, separator3):
            separator.grid_configure(pady=5 if compact else 12)
        left_heading.grid_configure(pady=(0, 4 if compact else 10))
        storage_title.grid_configure(pady=(0, 4 if compact else 8))
        device_detail_label.grid_configure(pady=(3, 4) if compact else (5, 8))
        gps_status_label.grid_configure(pady=(2 if compact else 5, 0))
        recent_title.grid_configure(pady=(5 if compact else 12, 2))
        storage_actions.grid_configure(pady=(4 if compact else 8, 0))
        app.ocr_status_label.configure(wraplength=200 if narrow else 225 if compact else 245)
        app.ocr_status_label.grid_configure(pady=(0, 4 if compact else 6))
        review_heading.grid_configure(pady=(0, 4 if compact else 6))
        phase_tabs.grid_configure(pady=(0, 5 if compact else 6))
        for entry in app.review_entries[:-1]:
            entry.grid_configure(pady=(0, 2 if compact else 4))
        review_actions.grid_configure(pady=(3 if compact else 5, 0))
        review_separator.grid_configure(pady=4 if compact else 7)
        filename_label.configure(wraplength=130 if narrow else 145)
        filename_label.pack_configure(pady=(0, 3 if compact else 6))
        if narrow:
            tools.grid_configure(row=1, column=0, sticky='ew', pady=(4, 0))
            app_info_label.configure(wraplength=125)
            gps_status_label.configure(wraplength=185)
            recent_file.configure(wraplength=185)
            device_detail_label.configure(wraplength=185)
        else:
            tools.grid_configure(row=0, column=1, sticky='e', pady=0)
            app_info_label.configure(wraplength=150)
            gps_status_label.configure(wraplength=225)
            recent_file.configure(wraplength=225)
            device_detail_label.configure(wraplength=225)
        toolbar.grid_configure(pady=(0, 5 if compact else 10))
        sheet.configure(padding=6 if compact else 12)
        sheet.grid_configure(pady=(6 if compact else 10, 0))
        sheet_heading.grid_configure(pady=(0, 3 if compact else 8))
        sheet_tools.grid_configure(pady=(0, 3 if compact else 8))
        app.sheet_selector.configure(width=13 if compact else 18)
        app.sheet_search_entry.configure(width=12 if compact else 20)
        app.sheet_table.configure(height=1 if compact else 4)
        footer.grid_configure(pady=(5 if compact else 8, 0))
    app.bind('<Configure>', adapt_layout, add='+')
    app.buttons = [app.connect_button, app.disconnect_button, app.pair_button, app.refresh_button,
                   app.identify_button, app.connection_toggle, app.folder_button, app.open_button,
                   app.capture_button, app.review_button, app.recognize_button, app.original_button,
                   app.selected_original_button, app.data_button, app.departure_button, app.arrival_button,
                   app.start_select_button, app.finish_select_button, app.review_start_button, app.review_finish_button,
                   app.gps_checkbox, app.gps_status_button, app.discover_button, app.sheet_columns_button,
                   app.export_all_button, app.export_filtered_button]
