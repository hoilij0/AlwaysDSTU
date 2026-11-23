"""Контрактные проверки чтения последних снимков без внешней БД."""

from datetime import date, datetime, time
from unittest import TestCase
from unittest.mock import MagicMock, patch

from database import PostgresRepository
from schedule_api import Lesson


class ScheduleHistoryTests(TestCase):
    def test_selected_group_round_trip_contract(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = ("ВИС32",)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        with patch.object(PostgresRepository, "_connect", return_value=connection):
            repo = PostgresRepository()
            repo.save_selected_group("ВИС32")
            self.assertEqual(repo.selected_group(), "ВИС32")

        self.assertIn("ON CONFLICT", cursor.execute.call_args_list[0].args[0])
        self.assertEqual(cursor.execute.call_args_list[0].args[1], ("ВИС32",))

    def test_latest_versions_are_mapped_newest_first(self):
        current_time = datetime(2026, 11, 23, 12, 0)
        previous_time = datetime(2026, 11, 20, 12, 0)
        snapshot_rows = [(8, current_time), (7, previous_time)]
        current_lesson = (80, date(2026, 11, 23), time(10), time(11, 30), "Математика", "Иванов", "А-101", None, "")
        previous_lesson = (70, date(2026, 11, 23), time(9), time(10, 30), "Математика", "Иванов", "А-101", None, "")
        cursor = MagicMock()
        cursor.fetchall.side_effect = [snapshot_rows, [current_lesson], [previous_lesson]]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor

        with patch.object(PostgresRepository, "_connect", return_value=connection):
            versions = PostgresRepository().latest_schedule_versions("ВИС32")

        self.assertEqual([version.snapshot_id for version in versions], [8, 7])
        self.assertEqual([version.downloaded_at for version in versions], [current_time, previous_time])
        self.assertEqual(versions[0].entries[0].lesson_id, 80)
        self.assertEqual(versions[1].entries[0].start_time, time(9))
        self.assertEqual(cursor.execute.call_count, 3)

    def test_no_saved_versions_returns_empty_list(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor

        with patch.object(PostgresRepository, "_connect", return_value=connection):
            versions = PostgresRepository().latest_schedule_versions("ВИС32")

        self.assertEqual(versions, [])
        self.assertEqual(cursor.execute.call_count, 1)

    def test_clear_group_cache_deletes_snapshots_and_detached_notes(self):
        cursor = MagicMock()
        cursor.rowcount = 2
        cursor.fetchone.return_value = (23,)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor

        with patch.object(PostgresRepository, "_connect", return_value=connection):
            deleted_count = PostgresRepository().clear_group_cache("ВИС32")

        self.assertEqual(deleted_count, 2)
        self.assertIn("FOR UPDATE", cursor.execute.call_args_list[0].args[0])
        self.assertIn("DELETE FROM detached_notes", cursor.execute.call_args_list[1].args[0])
        sql = cursor.execute.call_args.args[0]
        self.assertIn("DELETE FROM schedule_snapshots", sql)
        self.assertEqual(cursor.execute.call_args.args[1], (23,))

    def test_clear_group_cache_rejects_empty_group(self):
        repository = PostgresRepository()
        with patch.object(repository, "_connect") as connect:
            with self.assertRaises(ValueError):
                repository.clear_group_cache("  ")
        connect.assert_not_called()

    def test_save_schedule_rejects_multiple_months_in_one_snapshot(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        lessons = [
            Lesson(date(2026, 11, 30), time(9), time(10), "Math"),
            Lesson(date(2026, 12, 1), time(9), time(10), "Math"),
        ]

        with patch.object(PostgresRepository, "_connect", return_value=connection):
            with self.assertRaisesRegex(ValueError, "одного месяца"):
                PostgresRepository().save_schedule("TestGroup", lessons)

        self.assertEqual(cursor.execute.call_count, 1)


if __name__ == "__main__":
    import unittest

    unittest.main()
