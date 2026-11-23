"""Сквозная проверка окна и PostgreSQL с имитацией отсутствия сети."""

import os
import re
import tkinter as tk
import unittest
from datetime import date, time, timedelta
from tkinter import ttk
from unittest.mock import patch
from uuid import uuid4

import main
from database import PostgresRepository
from schedule_api import Group, Lesson, ScheduleApiError


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ImmediateThread:
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


@unittest.skipUnless(
    os.environ.get("ALWAYS_DSTU_TEST_DB") == "1"
    and os.environ.get("ALWAYS_DSTU_TEST_GUI") == "1",
    "PostgreSQL and Tkinter GUI tests must both be enabled",
)
class FullScenarioTests(unittest.TestCase):
    def test_note_persists_after_restart_and_offline_sync_failure(self):
        repo = PostgresRepository()
        schema = "alwaysdstu_test_" + uuid4().hex
        self.assertRegex(schema, r"^alwaysdstu_test_[0-9a-f]{32}$")
        with repo._connect() as connection:
            from psycopg import sql
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(schema)))

        def remove_test_schema():
            if not re.fullmatch(r"alwaysdstu_test_[0-9a-f]{32}", schema):
                raise AssertionError("Unexpected schema name")
            with repo._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

        self.addCleanup(remove_test_schema)
        options = os.environ.get("PGOPTIONS", "") + f" -c search_path={schema}"
        with patch.dict(os.environ, {"PGOPTIONS": options.strip()}), \
             patch.object(main.threading, "Thread", ImmediateThread):
            repo.initialize_schema()
            group_name = "ВИС32"
            month_start = date.today().replace(day=1)
            lessons = [
                Lesson(month_start + timedelta(days=offset), time(9), time(10, 30), "Test lesson")
                for offset in range(8)
            ]
            repo.save_schedule(group_name, lessons)
            repo.save_local_nickname("Student")

            root = tk.Tk()
            root.withdraw()
            try:
                with patch.object(root, "after", side_effect=lambda delay, callback=None: callback() if delay == 0 else None):
                    app = main.AlwaysDSTUApp(root)
                    self.assertTrue(app.storage_ready)
                    app._select_date(month_start)
                    app.open_note_editor(app.entries[0])
                    dialog = next(widget for widget in root.winfo_children() if isinstance(widget, tk.Toplevel))
                    text = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
                    save = next(
                        widget for widget in descendants(dialog)
                        if isinstance(widget, ttk.Button) and widget.cget("text") == "Сохранить"
                    )
                    text.insert("1.0", "Offline test note")
                    save.invoke()
                    self.assertEqual(repo.current_schedule(group_name)[0].note_text, "Offline test note")
                    self.assertEqual(repo.current_schedule(group_name)[0].author_name, "Student")
            finally:
                root.update_idletasks()
                root.destroy()

            root = tk.Tk()
            root.withdraw()
            try:
                with patch.object(root, "after", side_effect=lambda delay, callback=None: callback() if delay == 0 else None):
                    app = main.AlwaysDSTUApp(root)
                    with patch.object(app.schedule_api, "fetch_groups", side_effect=ScheduleApiError("offline")):
                        app.synchronize_schedule(silent=True)
                    self.assertFalse(app.busy)
                    self.assertEqual(app.entries[0].note_text, "Offline test note")
                    app._select_date(month_start)
                    labels = [widget.cget("text") for widget in descendants(app.cards_frame)
                              if isinstance(widget, tk.Label)]
                    self.assertIn("● Есть заметка", labels)
                    app.open_note_editor(app.entries[0])
                    dialog = next(widget for widget in root.winfo_children() if isinstance(widget, tk.Toplevel))
                    note = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
                    self.assertEqual(note.get("1.0", "end-1c"), "Offline test note")
                    cancel = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Button)
                                  and widget.cget("text") == "Отмена")
                    cancel.invoke()
                    self.assertIn("кэш не изменён", app.status_var.get())

                    with patch.object(app.schedule_api, "fetch_groups", return_value=[Group(1, group_name)]), \
                         patch.object(app.schedule_api, "fetch_schedule", return_value=lessons[:2]):
                        app.synchronize_schedule(silent=True)
                        self.assertIn("Возможен неполный ответ", app.status_var.get())
                        self.assertEqual(len(repo.current_schedule(group_name)), 8)
                        self.assertEqual(repo.current_schedule(group_name)[0].note_text, "Offline test note")

                        with patch.object(main.messagebox, "askyesno", return_value=False):
                            app.synchronize_schedule()
                        self.assertEqual(len(repo.current_schedule(group_name)), 8)

                        with patch.object(main.messagebox, "askyesno", return_value=True):
                            app.synchronize_schedule()
                        self.assertEqual(len(repo.current_schedule(group_name)), 2)
                        self.assertEqual(repo.current_schedule(group_name)[0].note_text, "Offline test note")
                        self.assertIn("Расписание изменилось", app.status_var.get())
                        self.assertIn("отменено 6", app.status_var.get())
            finally:
                root.update_idletasks()
                root.destroy()


if __name__ == "__main__":
    unittest.main()
