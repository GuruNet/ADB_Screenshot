"""Check that essential actions fit at the supported minimum window size."""
import gc
import tkinter as tk
import unittest

from ui_layout import build_main


class LayoutApp(tk.Tk):
    def __init__(self):
        super().__init__()
        for name in ('host', 'port', 'pair_port', 'pair_code', 'device', 'device_detail', 'app_detail',
                     'status', 'output_dir', 'last_saved', 'screen_time', 'distance', 'duration', 'toll',
                     'estimated_arrival', 'ocr_status'):
            setattr(self, name, tk.StringVar(master=self, value=''))
        self.last_saved.set('저장됨: /Users/test/Pictures/ADB_Screenshots/example.png\n앱 정보: example.json')
        self.ocr_status.set('자동 인식값은 확정되지 않았습니다. 원본과 비교해 수정해 주세요.')
        self.calls = []
        build_main(self)

    def on_sheet_changed(self, _event):
        pass

    def __getattr__(self, name):
        actions = {'toggle_connection', 'inspect_device', 'refresh_devices', 'connect_device',
                   'disconnect_device', 'pair_device', 'select_journey_phase', 'start_journey',
                   'finish_journey', 'sync_journey_gps', 'choose_folder', 'open_capture', 'capture', 'show_original',
                   'open_data', 'save_review', 'recognize_again', 'discover_wireless', 'on_discovery_selected',
                   'choose_sheet_columns', 'export_all_data', 'export_filtered_data'}
        if name in actions:
            return lambda *args: self.calls.append((name, args))
        raise AttributeError(name)


class LayoutTests(unittest.TestCase):
    def test_actions_fit_with_expanded_connection_and_callbacks(self):
        try:
            app = LayoutApp()
        except tk.TclError as exc:
            self.skipTest(f'GUI display unavailable: {exc}')
        try:
            app.geometry('800x580')
            app.selected_filename.set('20000101_120000_01234567_finish.png')
            self.assertEqual(app.review_title.get(), '선택한 화면 · 인식 결과')
            image = tk.PhotoImage(master=app, width=120, height=55)
            app.start_thumbnail.configure(image=image, text='')
            app.finish_thumbnail.configure(image=image, text='')
            for expanded in (False, True):
                if expanded:
                    app.connection_popup.deiconify()
                app.update()
                self.assertTrue(app.ocr_status_label.winfo_ismapped())
                left, top = app.winfo_rootx(), app.winfo_rooty()
                names = ['departure_button', 'gps_status_button', 'arrival_button', 'capture_button', 'selected_original_button',
                         'original_button', 'review_button', 'recognize_button', 'data_button', 'sheet_table',
                         'sheet_search_entry', 'sheet_columns_button', 'export_all_button', 'export_filtered_button']
                for name in names:
                    widget = getattr(app, name)
                    with self.subTest(expanded=expanded, widget=name):
                        self.assertTrue(widget.winfo_ismapped())
                        if name != 'sheet_table':
                            self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                        self.assertLessEqual(widget.winfo_rootx() - left + widget.winfo_width(), app.winfo_width())
                        self.assertLessEqual(widget.winfo_rooty() - top + widget.winfo_height(), app.winfo_height())
                self.assertLess(app.finish_tab.winfo_rootx(), app.review_entries[0].winfo_rootx())
                self.assertLess(app.start_tab.winfo_rootx(), app.finish_tab.winfo_rootx())
                self.assertLess(app.finish_tab.winfo_rootx(), app.screenshot_tab.winfo_rootx())
                self.assertGreater(app.sheet_table.winfo_rooty(), app.preview.winfo_rooty())
                for name in ('data_button', 'review_button', 'recognize_button', 'departure_button',
                             'arrival_button', 'capture_button'):
                    widget = getattr(app, name)
                    with self.subTest(expanded=expanded, overlap=name):
                        self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), app.sheet_panel.winfo_rooty())
                if expanded:
                    self.assertTrue(app.pair_button.winfo_ismapped())
                    self.assertLessEqual(app.pair_button.winfo_rootx() - app.connection_popup.winfo_rootx() + app.pair_button.winfo_width(), app.connection_popup.winfo_width())
                    self.assertLessEqual(app.pair_button.winfo_rooty() - app.connection_popup.winfo_rooty() + app.pair_button.winfo_height(), app.connection_popup.winfo_height())
            app.departure_button.invoke()
            app.arrival_button.invoke()
            app.start_select_button.invoke()
            app.gps_status_button.invoke()
            self.assertEqual(app.calls, [('start_journey', ()), ('finish_journey', ()),
                                         ('select_journey_phase', ('start',)), ('sync_journey_gps', ())])
        finally:
            app.destroy()
            del app
            gc.collect()


if __name__ == '__main__':
    unittest.main()
