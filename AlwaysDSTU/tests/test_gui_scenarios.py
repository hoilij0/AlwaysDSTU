"""Проверки пользовательских сценариев Tkinter с тестовым хранилищем и API.

Для запуска нужен рабочий графический сеанс и ALWAYS_DSTU_TEST_GUI=1.
"""

import os
import tkinter as tk
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from tkinter import ttk
from unittest.mock import patch

import main
from database import (
    DatabaseUnavailable, DetachedNote, ScheduleEntry, ScheduleVersion, SuspiciousScheduleUpdate,
)
from schedule_api import Group, Lesson, ScheduleApiError


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class FakeRepository:
    def __init__(self):
        day = date.today().replace(day=1)
        self.entries = [ScheduleEntry(1, day, time(9), time(10, 30), "Math", "Teacher", "101", None, "")]
        self.failures = []
        self.other_notes = []
        self.saved_group = "ВИС32"
        self.nickname = "Student"
        self.theme = "light"

    def initialize_schema(self):
        pass

    def list_groups(self):
        return ["ВИС32"]

    def selected_group(self):
        return self.saved_group

    def save_selected_group(self, group):
        self.saved_group = group

    def local_nickname(self):
        return self.nickname

    def save_local_nickname(self, nickname):
        if not nickname.strip():
            raise ValueError("Укажите ник пользователя.")
        self.nickname = nickname.strip()

    def ui_theme(self):
        return self.theme

    def save_profile_settings(self, nickname, group, theme):
        if not nickname.strip() or not group.strip() or theme not in ("light", "dark"):
            raise ValueError("Проверьте настройки профиля.")
        self.nickname = nickname.strip()
        self.saved_group = group.strip()
        self.theme = theme

    def boundary_schedule(self, _group):
        return [], None

    def current_schedule(self, _group):
        return list(self.entries)

    def last_successful_sync(self, _group):
        return datetime(2026, 1, 1, 12)

    def latest_schedule_versions(self, _group):
        return [ScheduleVersion(1, datetime(2026, 1, 1, 12), list(self.entries))]

    def current_snapshot_id(self, _group):
        return 1

    def save_note(self, lesson_id, value, author=""):
        self.nickname = author or self.nickname
        self.entries = [
            replace(entry, note_id=1, note_text=value, author_name=author) if entry.lesson_id == lesson_id else entry
            for entry in self.entries
        ]
        return 1

    def delete_note(self, note_id):
        self.entries = [
            replace(entry, note_id=None, note_text="") if entry.note_id == note_id else entry
            for entry in self.entries
        ]
        return True

    def save_schedule(self, _group, lessons, *, allow_large_decrease=False, full_response=None):
        self.entries = [
            ScheduleEntry(i, lesson.lesson_date, lesson.start_time, lesson.end_time,
                          lesson.subject, lesson.teacher, lesson.room, None, "")
            for i, lesson in enumerate(lessons, 1)
        ]
        return 2

    def record_sync_failure(self, _group, detail):
        self.failures.append(detail)

    def detached_notes(self, _group):
        return list(self.other_notes)

    def save_detached_note(self, note_id, text, author=""):
        if not text.strip():
            raise ValueError("Текст заметки не должен быть пустым.")
        for index, note in enumerate(self.other_notes):
            if note.note_id == note_id:
                self.other_notes[index] = replace(note, note_text=text.strip(), author_name=author)
                return True
        return False

    def delete_detached_note(self, note_id):
        old_count = len(self.other_notes)
        self.other_notes = [note for note in self.other_notes if note.note_id != note_id]
        return len(self.other_notes) < old_count


class ImmediateThread:
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


@unittest.skipUnless(os.environ.get("ALWAYS_DSTU_TEST_GUI") == "1", "Tkinter GUI test disabled")
class GuiScenarioTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.repo = FakeRepository()
        self.repo_patch = patch.object(main, "PostgresRepository", return_value=self.repo)
        self.repo_patch.start()
        self.after_patch = patch.object(
            self.root, "after", side_effect=lambda delay, callback=None: callback() if delay == 0 else None
        )
        self.after_patch.start()
        self.thread_patch = patch.object(main.threading, "Thread", ImmediateThread)
        self.thread_patch.start()
        self.app = main.AlwaysDSTUApp(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.root.update_idletasks()
        self.root.destroy()
        self.thread_patch.stop()
        self.after_patch.stop()
        self.repo_patch.stop()

    def _open_note(self):
        self.app.open_note_editor(self.app.entries[0])
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        text = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
        save = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Сохранить"
        )
        return dialog, text, save

    def test_note_validation_and_saved_text_are_shown_in_schedule(self):
        dialog, text, save = self._open_note()
        with patch.object(main.messagebox, "showwarning") as warning:
            save.invoke()
        warning.assert_called_once()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(self.repo.entries[0].note_text, "")

        text.insert("1.0", "Bring homework")
        save.invoke()
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(self.repo.entries[0].note_text, "Bring homework")
        self.app._select_date(self.repo.entries[0].lesson_date)
        labels = [widget.cget("text") for widget in descendants(self.app.cards_frame)
                  if isinstance(widget, tk.Label)]
        self.assertIn("● Есть заметка", labels)

    def test_note_requires_profile_and_keeps_draft_while_setting_nickname(self):
        self.repo.nickname = ""
        dialog, text, save = self._open_note()
        text.insert("1.0", "Homework")
        with patch.object(main.messagebox, "showwarning") as warning:
            save.invoke()
        warning.assert_called_once()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(self.repo.entries[0].note_text, "")
        profile = next(widget for widget in self.root.winfo_children()
                       if isinstance(widget, tk.Toplevel) and widget != dialog)
        nickname = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Entry)
                        and not isinstance(widget, ttk.Combobox))
        nickname.insert(0, "Alice")
        theme_combo = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Combobox)
                           and "Тёмная" in widget.cget("values"))
        theme_combo.set("Тёмная")
        profile_save = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Button)
                            and widget.cget("text") == "Сохранить")
        profile_save.invoke()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(text.get("1.0", "end-1c"), "Homework")
        self.assertEqual(text.cget("bg"), main.THEMES["dark"]["surface"])
        self.assertFalse(any(isinstance(widget, ttk.Entry) for widget in descendants(dialog)))
        save.invoke()
        self.assertEqual(self.repo.nickname, "Alice")
        self.assertEqual(self.repo.entries[0].author_name, "Alice")

    def test_first_run_profile_is_saved_and_not_requested_again(self):
        self.repo.nickname = ""
        self.app.open_profile(first_run=True)
        profile = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        nickname = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Entry)
                        and not isinstance(widget, ttk.Combobox))
        nickname.insert(0, "Student")
        save = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Сохранить")
        save.invoke()
        self.assertEqual(self.repo.local_nickname(), "Student")
        self.assertFalse(profile.winfo_exists())
        self.app.open_profile(first_run=True)
        self.assertFalse(any(isinstance(widget, tk.Toplevel) for widget in self.root.winfo_children()))

    def test_profile_theme_is_persisted_and_applies_to_notes(self):
        self.app.open_profile()
        profile = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        theme_combo = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Combobox)
                           and "Тёмная" in widget.cget("values"))
        theme_combo.set("Тёмная")
        save = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Сохранить")
        save.invoke()
        self.assertEqual(self.repo.ui_theme(), "dark")
        self.assertEqual(self.app.theme_name, "dark")
        self.assertEqual(self.app.cards_canvas.cget("bg"), main.THEMES["dark"]["background"])
        self.app._select_date(self.repo.entries[0].lesson_date)
        self.assertEqual(self.app.day_cards[0].cget("bg"), main.THEMES["dark"]["surface"])
        self.app.open_note_editor(self.app.entries[0])
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        text = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
        self.assertEqual(text.cget("bg"), main.THEMES["dark"]["surface"])
        self.assertEqual(text.cget("fg"), main.THEMES["dark"]["text"])
        text.insert("1.0", "Draft survives switching themes")
        self.app._apply_theme("light")
        self.assertEqual(text.cget("bg"), main.THEMES["light"]["surface"])
        self.assertEqual(text.get("1.0", "end-1c"), "Draft survives switching themes")
        self.app._apply_theme("dark")
        self.assertEqual(text.cget("bg"), main.THEMES["dark"]["surface"])
        self.assertEqual(text.get("1.0", "end-1c"), "Draft survives switching themes")
        dialog.destroy()
        self.app.note_editor_open = False
        self.app._apply_theme("light")
        self.app._initialize_storage()
        self.assertEqual(self.app.theme_name, "dark")

    def test_profile_failure_does_not_apply_unsaved_theme(self):
        self.app.open_profile()
        profile = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        theme_combo = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Combobox)
                           and "Тёмная" in widget.cget("values"))
        theme_combo.set("Тёмная")
        save = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Сохранить")
        with patch.object(self.repo, "save_profile_settings", side_effect=DatabaseUnavailable("offline")), \
             patch.object(main.messagebox, "showerror") as error:
            save.invoke()
        error.assert_called_once()
        self.assertTrue(profile.winfo_exists())
        self.assertEqual(self.app.theme_name, "light")
        self.assertEqual(self.repo.ui_theme(), "light")

    def test_group_is_selected_in_profile_without_losing_nickname_draft(self):
        self.app.remote_groups = {"вис33": Group(2, "ВИС33")}
        self.app.open_profile()
        profile = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        nickname = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Entry)
                        and not isinstance(widget, ttk.Combobox))
        nickname.delete(0, "end")
        nickname.insert(0, "Alice")
        find = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Найти")
        find.invoke()
        search_dialog = next(widget for widget in self.root.winfo_children()
                             if isinstance(widget, tk.Toplevel) and widget != profile)
        search = next(widget for widget in descendants(search_dialog) if isinstance(widget, ttk.Entry))
        search.insert(0, "ВИС33")
        choose = next(widget for widget in descendants(search_dialog) if isinstance(widget, ttk.Button)
                      and widget.cget("text") == "Выбрать")
        choose.invoke()
        self.assertEqual(self.app.group_var.get(), "ВИС32")
        self.assertEqual(nickname.get(), "Alice")
        save = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Сохранить")
        save.invoke()
        self.assertEqual(self.app.group_var.get(), "ВИС33")
        self.assertEqual(self.repo.saved_group, "ВИС33")
        self.assertEqual(self.repo.nickname, "Alice")

    def test_group_catalog_loads_inside_profile_and_refreshes_search(self):
        self.app.open_profile()
        profile = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        find = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Найти")
        find.invoke()
        search_dialog = next(widget for widget in self.root.winfo_children()
                             if isinstance(widget, tk.Toplevel) and widget != profile)
        load = next(widget for widget in descendants(search_dialog) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Загрузить группы")
        with patch.object(self.app.schedule_api, "fetch_groups", return_value=[Group(2, "ВИС33")]):
            load.invoke()
        group_list = next(widget for widget in descendants(search_dialog) if isinstance(widget, tk.Listbox))
        self.assertIn("ВИС33", group_list.get(0, "end"))
        self.assertIn("ВИС33", self.app.profile_group_combo.cget("values"))
        self.assertEqual(self.app.group_var.get(), "ВИС32")
        self.assertFalse(self.app.busy)

    def test_closing_group_search_during_loading_keeps_profile_usable(self):
        self.app.open_profile()
        profile = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        find = next(widget for widget in descendants(profile) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Найти")
        find.invoke()
        search_dialog = next(widget for widget in self.root.winfo_children()
                             if isinstance(widget, tk.Toplevel) and widget != profile)
        load = next(widget for widget in descendants(search_dialog) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Загрузить группы")
        cancel = next(widget for widget in descendants(search_dialog) if isinstance(widget, ttk.Button)
                      and widget.cget("text") == "Отмена")
        with patch.object(main.threading, "Thread") as thread, \
             patch.object(self.app.schedule_api, "fetch_groups", return_value=[Group(2, "ВИС33")]):
            load.invoke()
            worker = thread.call_args.kwargs["target"]
            cancel.invoke()
            self.assertTrue(self.app.busy)
            self.assertTrue(profile.winfo_exists())
            worker()
        self.assertFalse(self.app.busy)
        self.assertTrue(self.app.profile_group_combo.instate(["readonly"]))
        self.assertIn("ВИС33", self.app.profile_group_combo.cget("values"))

    def test_day_card_opens_note_and_shows_note_marker_after_save(self):
        self.app._select_calendar_day(self.repo.entries[0].lesson_date.day)
        self.assertEqual(len(self.app.day_cards), 1)
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.root.update()
        card = self.app.day_cards[0]
        self.assertFalse(any(isinstance(widget, ttk.Button) for widget in descendants(card)))
        card.event_generate("<Button-1>", x=5, y=5)
        self.root.update()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        text = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
        text.insert("1.0", "Card note")
        save = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Сохранить")
        save.invoke()
        self.assertEqual(self.repo.entries[0].note_text, "Card note")
        labels = [widget.cget("text") for widget in descendants(self.app.cards_frame)
                  if isinstance(widget, tk.Label)]
        self.assertIn("● Есть заметка", labels)

    def test_day_card_is_accessible_with_keyboard(self):
        self.app._select_calendar_day(self.repo.entries[0].lesson_date.day)
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.root.update()
        card = self.app.day_cards[0]
        card.focus_force()
        self.root.update()
        self.assertEqual(self.root.focus_get(), card)
        card.event_generate("<Return>")
        self.root.update()
        self.assertTrue(self.app.note_editor_open)
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        self.assertEqual(dialog.title(), "Заметка к занятию")

    def test_clean_calendar_sidebar_and_table_styles(self):
        style = ttk.Style(self.root)
        self.assertEqual(str(style.lookup("Calendar.TButton", "relief")), "flat")
        self.assertEqual(str(style.lookup("Sidebar.TButton", "relief")), "flat")
        self.assertEqual(str(style.lookup("TButton", "relief")), "flat")
        self.assertEqual(str(self.app.analysis_button.cget("style")), "Sidebar.TButton")

    def test_profile_controls_fit_its_minimum_size(self):
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.app.open_profile()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        dialog.geometry("460x490")
        self.root.update()
        controls = [widget for widget in descendants(dialog) if isinstance(widget, ttk.Button)]
        self.assertTrue(any("Очистить кэш" in widget.cget("text") for widget in controls))
        for widget in controls:
            self.assertTrue(widget.winfo_ismapped(), f"{widget.cget('text')} is hidden")
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                 dialog.winfo_rooty() + dialog.winfo_height())

    def test_only_day_view_and_week_navigation_are_shown(self):
        self.assertFalse(hasattr(self.app, "schedule_notebook"))
        self.assertFalse(hasattr(self.app, "schedule_tree"))
        self.assertFalse(hasattr(self.app, "group_combo"))
        self.assertEqual(len(self.app.week_day_buttons), 7)
        labels = [str(button.cget("text")) for button in self.app.week_day_buttons]
        self.assertTrue(all("\n" in text for text in labels))
        self.assertTrue(all("•" not in text and "·" not in text for text in labels))

    def test_week_counts_and_colors_follow_load_and_theme(self):
        sample = self.repo.entries[0]
        self.repo.entries = [
            replace(sample, lesson_id=number * 10 + index, lesson_date=date(2026, 11, 23 + number))
            for number in range(1, 6) for index in range(number)
        ]
        self.app.month_filter_var.set("2026-11")
        self.app.date_filter_var.set("23.11.2026")
        self.app._load_schedule()
        self.assertEqual([label.cget("text") for label in self.app.week_count_labels],
                         ["0", "1", "2", "3", "4", "5", "0"])
        for index, key in enumerate(("muted", "load_1", "load_2", "load_3", "load_4", "load_4", "muted")):
            self.assertEqual(self.app.week_count_labels[index].cget("fg"), main.THEMES["light"][key])
        self.app._apply_theme("dark")
        for index, key in enumerate(("muted", "load_1", "load_2", "load_3", "load_4", "load_4", "muted")):
            self.assertEqual(self.app.week_count_labels[index].cget("fg"), main.THEMES["dark"][key])
        self.assertEqual(self.app.week_day_buttons[0].cget("style"), "CalendarSelected.TButton")

    def test_week_counts_use_only_known_boundary_days(self):
        self.repo.entries = [replace(self.repo.entries[0], lesson_date=date(2026, 11, 30))]
        boundary = [Lesson(date(2026, 12, 1), time(9), time(10, 30), "Math", "Teacher", "101", None)]
        self.app.month_filter_var.set("2026-11")
        self.app.date_filter_var.set("30.11.2026")
        with patch.object(self.repo, "boundary_schedule", return_value=(
                boundary, (date(2026, 9, 1), date(2026, 12, 3)))):
            self.app._load_schedule()
            self.assertEqual([label.cget("text") for label in self.app.week_count_labels],
                             ["1", "1", "0", "0", "—", "—", "—"])
            with patch.object(self.app.schedule_api, "fetch_groups", side_effect=ScheduleApiError("offline")):
                self.app._select_date(date(2026, 12, 1))
            messages = [widget.cget("text") for widget in self.app.cards_frame.winfo_children()
                        if isinstance(widget, tk.Label)]
            self.assertTrue(any("Число пар сохранено" in message for message in messages))

        with patch.object(self.repo, "boundary_schedule", return_value=(
                boundary, (date(2026, 9, 1), date(2026, 12, 21)))):
            with patch.object(self.app.schedule_api, "fetch_groups", side_effect=ScheduleApiError("offline")):
                self.app._select_date(date(2026, 12, 14))
            self.assertEqual([label.cget("text") for label in self.app.week_count_labels], ["—"] * 7)

    def test_week_counts_are_unknown_without_cache_or_when_boundary_read_fails(self):
        self.repo.entries = []
        with patch.object(self.app, "synchronize_schedule"):
            self.app._load_schedule()
        self.assertEqual([label.cget("text") for label in self.app.week_count_labels], ["—"] * 7)
        self.repo.entries = [replace(FakeRepository().entries[0], lesson_date=date(2026, 11, 30))]
        self.app.month_filter_var.set("2026-11")
        self.app.date_filter_var.set("30.11.2026")
        with patch.object(self.repo, "boundary_schedule", side_effect=DatabaseUnavailable("offline")):
            self.app._load_schedule()
        self.assertEqual([label.cget("text") for label in self.app.week_count_labels], ["1"] + ["—"] * 6)

    def test_long_note_remains_fully_accessible_from_card(self):
        note = "Очень длинная заметка. " * 30 + "\nВторая строка."
        self.repo.save_note(1, note, "Student")
        self.app._load_schedule()
        self.app.open_note_editor(self.app.entries[0])
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        text = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
        self.assertEqual(text.get("1.0", "end-1c"), note)

    def test_group_search_filters_names_and_selects_group(self):
        self.app.remote_groups = {
            "вис32": Group(1, "ВИС32"),
            "вис33": Group(2, "ВИС33"),
            "эм34": Group(3, "ЭМ34"),
        }
        self.app.open_group_search()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        search = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Entry))
        group_list = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Listbox))
        search.insert(0, "вИс33")
        self.root.update()
        self.assertEqual(group_list.get(0, "end"), ("ВИС33",))
        choose = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Выбрать"
        )
        choose.invoke()
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(self.app.group_var.get(), "ВИС33")
        self.assertEqual(self.repo.saved_group, "ВИС33")
        self.assertFalse(self.app.group_search_open)

    def test_group_search_works_with_only_local_groups(self):
        self.app.group_values = ["ВИС32", "ВИС31"]
        self.app.remote_groups = {}
        self.app.open_group_search()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        search = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Entry))
        group_list = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Listbox))
        search.insert(0, "31")
        self.root.update()
        self.assertEqual(group_list.get(0, "end"), ("ВИС31",))
        self.assertTrue(self.app.group_search_open)
        cancel = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Отмена"
        )
        cancel.invoke()
        self.assertFalse(self.app.group_search_open)
        self.assertEqual(self.app.group_var.get(), "ВИС32")

    def test_group_search_retains_list_if_schedule_request_fails(self):
        groups = [Group(1, "ВИС32"), Group(2, "ВИС33")]
        with patch.object(self.app.schedule_api, "fetch_groups", return_value=groups), \
             patch.object(self.app.schedule_api, "fetch_schedule", side_effect=ScheduleApiError("offline")):
            self.app.synchronize_schedule(silent=True)
        self.assertIn("вис33", self.app.remote_groups)
        self.app.open_group_search()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        group_list = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Listbox))
        self.assertIn("ВИС33", group_list.get(0, "end"))

    def test_loading_groups_keeps_selected_group_if_missing_from_response(self):
        self.app.group_var.set("ВИС99")
        self.app._remote_groups_loaded([Group(2, "ВИС33")])
        self.assertEqual(self.app.group_var.get(), "ВИС99")
        self.assertIn("ВИС99", self.app.group_values)
        self.assertIn("ВИС33", self.app.group_values)

    def test_group_loading_unexpected_error_reenables_controls(self):
        with patch.object(self.app.schedule_api, "fetch_groups", side_effect=RuntimeError("unexpected")), \
             patch.object(main.messagebox, "showwarning") as warning:
            self.app.load_remote_groups()
        warning.assert_called_once()
        self.assertFalse(self.app.busy)
        self.assertTrue(self.app.profile_button.instate(["!disabled"]))

    def test_automatic_sync_waits_while_group_search_is_open(self):
        self.app.open_group_search()
        with patch.object(self.root, "after") as delayed, \
             patch.object(self.app.schedule_api, "fetch_groups") as fetch:
            self.app.synchronize_schedule(silent=True)
        delayed.assert_called_once()
        self.assertEqual(delayed.call_args.args[0], 1000)
        fetch.assert_not_called()

    def test_unavailable_api_keeps_saved_schedule_and_reenables_controls(self):
        previous = list(self.repo.entries)
        with patch.object(self.app.schedule_api, "fetch_groups", side_effect=ScheduleApiError("offline")):
            self.app.synchronize_schedule(silent=True)
        self.assertEqual(self.repo.entries, previous)
        self.assertEqual(self.app.entries, previous)
        self.assertFalse(self.app.busy)
        self.assertTrue(self.app.profile_button.instate(["!disabled"]))
        self.assertTrue(self.repo.failures)

    def test_unchanged_sync_reports_no_changes(self):
        lesson = Lesson(date.today().replace(day=1), time(9), time(10, 30), "Math")
        with patch.object(self.app.schedule_api, "fetch_groups", return_value=[Group(1, "ВИС32")]), \
             patch.object(self.app.schedule_api, "fetch_schedule", return_value=[lesson]), \
             patch.object(self.repo, "save_schedule", return_value=1):
            self.app.synchronize_schedule(silent=True)
        self.assertIn("изменений нет", self.app.status_var.get())

    def test_sync_uses_selected_local_group_name_when_api_case_differs(self):
        self.app.group_var.set("вис32")
        lesson = Lesson(date.today().replace(day=1), time(9), time(10, 30), "Math")
        with patch.object(self.app.schedule_api, "fetch_groups", return_value=[Group(1, "ВИС32")]), \
             patch.object(self.app.schedule_api, "fetch_schedule", return_value=[lesson]), \
             patch.object(self.repo, "save_schedule", return_value=1) as save:
            self.app.synchronize_schedule(silent=True)
        self.assertEqual(save.call_args.args[0], "вис32")
        self.assertEqual(self.app.group_var.get(), "вис32")

    def test_large_drop_needs_explicit_user_confirmation(self):
        lesson = Lesson(date.today().replace(day=1), time(9), time(10, 30), "Updated")
        original = list(self.repo.entries)
        saved_calls = []

        def save(_group, lessons, *, allow_large_decrease=False, full_response=None):
            saved_calls.append(allow_large_decrease)
            if not allow_large_decrease:
                raise SuspiciousScheduleUpdate(8, 1)
            self.repo.entries = [
                ScheduleEntry(2, lessons[0].lesson_date, lessons[0].start_time,
                              lessons[0].end_time, lessons[0].subject, "", "", None, "")
            ]
            return 2

        with patch.object(self.app.schedule_api, "fetch_groups", return_value=[Group(1, "ВИС32")]), \
             patch.object(self.app.schedule_api, "fetch_schedule", return_value=[lesson]), \
             patch.object(self.repo, "save_schedule", side_effect=save):
            with patch.object(main.messagebox, "askyesno") as confirm:
                self.app.synchronize_schedule(silent=True)
                confirm.assert_not_called()
            self.assertEqual(self.repo.entries, original)
            self.assertIn("Кэш сохранён", self.app.status_var.get())

            with patch.object(main.messagebox, "askyesno", return_value=False):
                self.app.synchronize_schedule()
            self.assertEqual(self.repo.entries, original)

            with patch.object(main.messagebox, "askyesno", return_value=True):
                self.app.synchronize_schedule()
            self.assertEqual([entry.subject for entry in self.repo.entries], ["Updated"])
            self.assertEqual(saved_calls, [False, False, False, True])

    def test_month_without_cache_does_not_show_previous_month_sync_time(self):
        self.assertIn("01.01.2026 12:00", self.app.sync_var.get())
        today = date.today()
        target = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
        target_month = target.strftime("%Y-%m")
        self.app.api_months["вис32"] = [target_month]
        self.app.month_filter_var.set(target_month)
        with patch.object(self.app.schedule_api, "fetch_groups", side_effect=ScheduleApiError("offline")):
            self.app._on_month_selected()

        self.assertIn("нет данных для выбранного месяца", self.app.sync_var.get())
        self.assertEqual(len(self.repo.entries), 1)

    def test_opening_with_old_cache_shows_it_when_current_month_is_empty(self):
        current = date.today().replace(day=1)
        previous = date(current.year - 1, 12, 1) if current.month == 1 else date(current.year, current.month - 1, 1)
        self.repo.entries = [replace(self.repo.entries[0], lesson_date=previous)]

        self.app._initialize_storage()

        self.assertEqual(self.app.month_filter_var.get(), previous.strftime("%Y-%m"))
        self.assertEqual(len(self.app.entries), 1)
        with patch.object(self.app.schedule_api, "fetch_groups", side_effect=ScheduleApiError("offline")):
            self.app.synchronize_schedule(silent=True)
        self.assertEqual(self.app.month_filter_var.get(), previous.strftime("%Y-%m"))
        self.assertEqual(len(self.app.entries), 1)

    def test_deletion_requires_confirmation(self):
        self.repo.save_note(1, "Existing note")
        self.app._load_schedule()
        dialog, _text, _save = self._open_note()
        delete = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Удалить"
        )
        with patch.object(main.messagebox, "askyesno", return_value=False):
            delete.invoke()
        self.assertEqual(self.repo.entries[0].note_text, "Existing note")
        self.assertTrue(dialog.winfo_exists())

        with patch.object(main.messagebox, "askyesno", return_value=True):
            delete.invoke()
        self.assertEqual(self.repo.entries[0].note_text, "")
        self.assertFalse(dialog.winfo_exists())

    def test_database_failure_keeps_note_form_open(self):
        dialog, text, save = self._open_note()
        text.insert("1.0", "Keep draft")
        with patch.object(self.repo, "save_note", side_effect=DatabaseUnavailable("database error")), \
             patch.object(main.messagebox, "showerror") as error:
            save.invoke()
        error.assert_called_once()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(text.get("1.0", "end-1c"), "Keep draft")

    def test_stale_lesson_keeps_draft_available_for_copying(self):
        dialog, text, save = self._open_note()
        text.insert("1.0", "Draft after update")
        with patch.object(self.repo, "save_note", side_effect=ValueError("Расписание изменилось")), \
             patch.object(main.messagebox, "showerror") as error:
            save.invoke()
        error.assert_called_once()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(text.get("1.0", "end-1c"), "Draft after update")

    def test_note_draft_is_kept_when_closing_is_cancelled(self):
        dialog, text, _save = self._open_note()
        text.insert("1.0", "Draft note")
        cancel = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Отмена"
        )
        with patch.object(main.messagebox, "askyesno", return_value=False) as confirm:
            cancel.invoke()
        confirm.assert_called_once()
        self.assertTrue(dialog.winfo_exists())
        self.assertTrue(self.app.note_editor_open)
        self.assertEqual(text.get("1.0", "end-1c"), "Draft note")

        with patch.object(main.messagebox, "askyesno", return_value=True):
            cancel.invoke()
        self.assertFalse(dialog.winfo_exists())
        self.assertFalse(self.app.note_editor_open)
        self.assertEqual(self.repo.entries[0].note_text, "")

    def test_detached_note_can_be_edited_and_deleted_with_confirmation(self):
        self.repo.other_notes = [DetachedNote(4, date(2026, 10, 1), time(9), "Math", "Before")]
        self.app.open_detached_notes()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        tree = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Treeview))
        text = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
        row = tree.get_children()[0]
        tree.selection_set(row)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update()
        text.delete("1.0", "end")
        text.insert("1.0", "After")
        buttons = {
            widget.cget("text"): widget for widget in descendants(dialog)
            if isinstance(widget, ttk.Button)
        }
        buttons["Сохранить"].invoke()
        self.assertEqual(self.repo.other_notes[0].note_text, "After")
        with patch.object(main.messagebox, "askyesno", return_value=False):
            buttons["Удалить"].invoke()
        self.assertEqual(len(self.repo.other_notes), 1)
        with patch.object(main.messagebox, "askyesno", return_value=True):
            buttons["Удалить"].invoke()
        self.assertEqual(self.repo.other_notes, [])
        buttons["Закрыть"].invoke()
        self.assertFalse(self.app.note_editor_open)

    def test_detached_note_unsaved_draft_is_not_lost_when_switch_is_cancelled(self):
        self.repo.other_notes = [
            DetachedNote(4, date(2026, 10, 1), time(9), "Math", "First"),
            DetachedNote(5, date(2026, 10, 2), time(9), "Physics", "Second"),
        ]
        self.app.open_detached_notes()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        tree = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Treeview))
        text = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
        first, second = tree.get_children()
        tree.selection_set(first)
        self.root.update()
        text.delete("1.0", "end")
        text.insert("1.0", "Draft")
        with patch.object(main.messagebox, "askyesno", return_value=False) as confirm:
            tree.selection_set(second)
            self.root.update()
        confirm.assert_called_once()
        self.assertEqual(tree.selection(), (first,))
        self.assertEqual(text.get("1.0", "end-1c"), "Draft")
        self.assertEqual(self.repo.other_notes[0].note_text, "First")

    def test_controls_and_calendar_are_visible_at_supported_window_sizes(self):
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.app._select_calendar_day(self.repo.entries[0].lesson_date.day)
        footer = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Label))
        for size in ("1160x740", "940x600"):
            with self.subTest(size=size):
                self.root.geometry(size)
                self.root.update()
                for widget in (self.app.analysis_button, self.app.profile_button,
                               self.app.week_day_buttons[0], self.app.week_count_labels[0],
                               self.app.sync_button, self.app.cards_canvas,
                               self.app.day_cards[0], footer):
                    self.assertTrue(widget.winfo_ismapped(), f"{widget} is hidden at {size}")
                    self.assertGreater(widget.winfo_height(), 1)
                    self.assertLessEqual(
                        widget.winfo_rooty() + widget.winfo_height(),
                        self.root.winfo_rooty() + self.root.winfo_height(),
                    )
                    self.assertLessEqual(
                        widget.winfo_rootx() + widget.winfo_width(),
                        self.root.winfo_rootx() + self.root.winfo_width(),
                    )
                notebook_bottom = self.app.calendar_tab.winfo_rooty() + self.app.calendar_tab.winfo_height()
                self.assertLessEqual(
                    self.app.cards_canvas.winfo_rooty() + self.app.cards_canvas.winfo_height(),
                    notebook_bottom,
                )
                self.assertGreaterEqual(self.app.cards_canvas.winfo_height(), 60)

    def test_week_navigation_preserves_cached_month_on_offline_transition(self):
        november_day = date(2026, 11, 30)
        self.repo.entries = [replace(self.repo.entries[0], lesson_date=november_day,
                                     note_id=1, note_text="Keep me", author_name="Student")]
        self.app.month_filter_var.set("2026-11")
        self.app._load_schedule()
        self.app._select_date(november_day)
        self.assertEqual(self.app.week_day_dates[0], november_day)
        december_day = date(2026, 12, 1)
        index = self.app.week_day_dates.index(december_day)
        with patch.object(self.app.schedule_api, "fetch_groups", side_effect=ScheduleApiError("offline")):
            self.app.week_day_buttons[index].invoke()
        self.assertEqual(self.app._displayed_calendar_day(), december_day)
        self.assertEqual(self.repo.entries[0].note_text, "Keep me")
        self.assertIn("кэш не изменён", self.app.status_var.get())
        self.app._select_date(november_day)
        self.assertEqual(self.app.entries[0].note_text, "Keep me")
        self.assertEqual(len(self.app.day_cards), 1)

    def test_analysis_uses_date_visible_in_day_view(self):
        self.app.date_filter_var.set("Все даты")
        displayed = date(2026, 11, 23)
        with patch.object(self.app, "_displayed_calendar_day", return_value=displayed):
            self.assertEqual(self.app._analysis_anchor_date(), displayed)

    def test_selected_month_loads_only_its_lessons(self):
        today = date.today()
        target = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
        target_month = target.strftime("%Y-%m")
        self.app.api_months["вис32"] = [target_month]
        self.app.month_filter_var.set(target_month)
        old_lesson = Lesson(today.replace(day=1), time(9), time(10), "Previous")
        new_lesson = Lesson(target, time(11), time(12), "Selected")
        with patch.object(self.app.schedule_api, "fetch_groups", return_value=[Group(1, "ВИС32")]), \
             patch.object(self.app.schedule_api, "fetch_schedule", return_value=[old_lesson, new_lesson]):
            self.app._on_month_selected()

        self.assertEqual([entry.subject for entry in self.repo.entries], ["Selected"])
        self.assertEqual(self.app.month_filter_var.get(), target_month)
        self.assertEqual(len(self.app.entries), 1)

    def test_cross_month_week_can_be_checked_online_without_changing_cache(self):
        before = list(self.repo.entries)
        lessons = [
            Lesson(date(2026, 11, 30), time(9), time(10, 30), "Math"),
            Lesson(date(2026, 12, 1), time(9), time(10, 30), "Physics"),
        ]
        with patch.object(self.app, "_analysis_anchor_date", return_value=date(2026, 11, 30)):
            self.app.open_analysis()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        period = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Combobox))
        button = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Проверить неделю на сайте"
        )
        period.set("Неделя")
        period.event_generate("<<ComboboxSelected>>")
        self.root.update()
        self.assertTrue(button.instate(["!disabled"]))
        with patch.object(self.app.schedule_api, "fetch_groups", return_value=[Group(1, "ВИС32")]), \
             patch.object(self.app.schedule_api, "fetch_schedule", return_value=lessons):
            button.invoke()
        self.root.update()
        label_values = [
            self.root.getvar(widget.cget("textvariable"))
            for widget in descendants(dialog)
            if isinstance(widget, ttk.Label) and widget.cget("textvariable")
        ]
        self.assertTrue(any("2 пар" in value and "по данным сайта" in value for value in label_values))
        self.assertTrue(any("Офлайн-кэш не изменён" in value for value in label_values))
        self.assertEqual(self.repo.entries, before)

    def test_change_filter_and_colour_tags_follow_change_type(self):
        current = self.repo.entries[0]
        old = replace(current, lesson_date=current.lesson_date.replace(day=2), subject="Old")
        versions = [ScheduleVersion(2, datetime(2026, 1, 2), [current]),
                    ScheduleVersion(1, datetime(2026, 1, 1), [old])]
        with patch.object(self.repo, "latest_schedule_versions", return_value=versions):
            self.app.open_analysis()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        tree = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Treeview))
        change_filter = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Combobox)
                             and "Отменено" in widget.cget("values"))
        self.assertEqual({tree.item(row, "tags")[0] for row in tree.get_children()}, {"added", "cancelled"})
        change_filter.set("Отменено")
        change_filter.event_generate("<<ComboboxSelected>>")
        self.root.update()
        rows = tree.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(tree.item(rows[0], "tags"), ("cancelled",))

    def test_cross_month_week_offline_keeps_cached_summary(self):
        with patch.object(self.app, "_analysis_anchor_date", return_value=date(2026, 11, 30)):
            self.app.open_analysis()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        period = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Combobox))
        button = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Проверить неделю на сайте"
        )
        period.set("Неделя")
        period.event_generate("<<ComboboxSelected>>")
        self.root.update()
        with patch.object(self.app.schedule_api, "fetch_groups", side_effect=ScheduleApiError("offline")):
            button.invoke()
        self.root.update()
        label_values = [
            self.root.getvar(widget.cget("textvariable"))
            for widget in descendants(dialog)
            if isinstance(widget, ttk.Label) and widget.cget("textvariable")
        ]
        self.assertTrue(any("Не удалось получить данные сайта" in value for value in label_values))
        self.assertTrue(any("только сохранённый месяц" in value for value in label_values))

    def test_cross_month_week_uses_cached_neighbour_days_offline(self):
        self.repo.entries = [ScheduleEntry(1, date(2026, 11, 30), time(9), time(10),
                                           "Monday", "", "", None, "")]
        boundary = [Lesson(date(2026, 12, 1), time(9), time(10), "Tuesday")]
        with patch.object(self.repo, "boundary_schedule", return_value=(
                boundary, (date(2026, 9, 1), date(2026, 12, 21)))), \
             patch.object(self.app, "_analysis_anchor_date", return_value=date(2026, 11, 30)):
            self.app.open_analysis()
        dialog = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        period = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Combobox))
        period.set("Неделя")
        period.event_generate("<<ComboboxSelected>>")
        self.root.update()
        values = [self.root.getvar(widget.cget("textvariable")) for widget in descendants(dialog)
                  if isinstance(widget, ttk.Label) and widget.cget("textvariable")]
        self.assertTrue(any("2 пар" in value and "соседние дни" in value for value in values))
        self.assertTrue(any("полнота зависит" in value for value in values))


if __name__ == "__main__":
    unittest.main()
