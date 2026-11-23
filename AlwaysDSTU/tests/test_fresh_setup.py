"""Проверка установки в отдельной временной схеме PostgreSQL."""

import os
import re
import subprocess
import sys
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from database import DatabaseUnavailable, PostgresRepository
from schedule_analysis import summarize_schedule
from schedule_api import Lesson


@unittest.skipUnless(os.environ.get("ALWAYS_DSTU_TEST_DB") == "1", "PostgreSQL test disabled")
class FreshSetupTests(unittest.TestCase):
    def test_first_setup_and_repeated_setup_preserve_data(self):
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
        with patch.dict(os.environ, {"PGOPTIONS": options.strip()}):
            project_dir = Path(__file__).resolve().parents[1]
            for attempt in range(2):
                process = subprocess.run(
                    [sys.executable, "setup_database.py"],
                    cwd=project_dir,
                    env=os.environ.copy(),
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
                with repo._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_schema()")
                        self.assertEqual(cursor.fetchone()[0], schema)
                if attempt == 0:
                    lesson = Lesson(date(2026, 11, 23), time(9), time(10, 30), "Math")
                    repo.save_schedule("FreshGroup", [lesson])
                    entry = repo.current_schedule("FreshGroup")[0]
                    repo.save_note(entry.lesson_id, "Fresh setup note")
                    repo.save_selected_group("FreshGroup")

            self.assertEqual(repo.selected_group(), "FreshGroup")
            self.assertEqual(repo.current_schedule("FreshGroup")[0].note_text, "Fresh setup note")
            # Старая заметка не получает выдуманного автора после миграции.
            entry = repo.current_schedule("FreshGroup")[0]
            self.assertEqual(entry.author_name, "")
            repo.save_note(entry.lesson_id, entry.note_text, "Student")
            self.assertEqual(repo.local_nickname(), "Student")
            repo.save_local_nickname("New nickname")
            self.assertEqual(repo.local_nickname(), "New nickname")
            self.assertEqual(repo.current_schedule("FreshGroup")[0].author_name, "Student")

            def lesson(day, subject):
                return Lesson(date.fromisoformat(day), time(9), time(10, 30), subject)

            november = [lesson("2026-11-01", "November start"),
                        lesson("2026-11-23", "Math"), lesson("2026-11-30", "November end")]
            response = [lesson("2026-09-01", "Outside"), lesson("2026-10-26", "October"),
                        *november, lesson("2026-12-01", "December"),
                        lesson("2026-12-06", "Sunday"), lesson("2026-12-21", "Outside")]
            snapshot = repo.save_schedule("FreshGroup", november, full_response=response)
            cached, coverage = repo.boundary_schedule("FreshGroup")
            self.assertEqual([x.subject for x in cached], ["October", "December", "Sunday"])
            self.assertEqual(coverage, (date(2026, 9, 1), date(2026, 12, 21)))
            summary = summarize_schedule(repo.current_schedule("FreshGroup") + cached,
                                         "week", date(2026, 11, 30))
            self.assertEqual(summary.lesson_count, 3)
            self.assertEqual(repo.current_schedule("FreshGroup")[1].author_name, "Student")

            refreshed = [x for x in response if x.subject != "December"]
            refreshed.append(lesson("2026-12-02", "Changed boundary"))
            self.assertEqual(repo.save_schedule("FreshGroup", november, full_response=refreshed), snapshot)
            self.assertIn("Changed boundary", [x.subject for x in repo.boundary_schedule("FreshGroup")[0]])
            self.assertEqual(len(repo.latest_schedule_versions("FreshGroup")), 2)

            invalid = [*refreshed, Lesson(date(2026, 12, 3), time(10), time(9), "Invalid")]
            with self.assertRaises(DatabaseUnavailable):
                repo.save_schedule("FreshGroup", november, full_response=invalid)
            self.assertIn("Changed boundary", [x.subject for x in repo.boundary_schedule("FreshGroup")[0]])

            december = [lesson("2026-12-02", "Changed boundary")]
            repo.save_schedule("FreshGroup", december, full_response=refreshed)
            self.assertEqual(repo.detached_notes("FreshGroup")[0].author_name, "Student")
            repo.save_schedule("FreshGroup", november, full_response=response)
            self.assertEqual(repo.current_schedule("FreshGroup")[1].author_name, "Student")
            self.assertEqual(repo.ui_theme(), "light")
            repo.save_profile_settings("Alice", "FreshGroup", "dark")
            repo.initialize_schema()
            self.assertEqual(repo.ui_theme(), "dark")
            self.assertEqual(repo.local_nickname(), "Alice")
            self.assertEqual(repo.selected_group(), "FreshGroup")
            self.assertEqual(repo.current_schedule("FreshGroup")[1].author_name, "Student")
            with self.assertRaises(ValueError):
                repo.save_profile_settings("Changed", "FreshGroup", "unexpected")
            self.assertEqual(repo.local_nickname(), "Alice")
            self.assertEqual(repo.ui_theme(), "dark")


if __name__ == "__main__":
    unittest.main()
