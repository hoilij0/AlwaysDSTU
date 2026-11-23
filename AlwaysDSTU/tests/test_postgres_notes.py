"""Интеграционные проверки PostgreSQL; запускать только с ALWAYS_DSTU_TEST_DB=1."""

import os
import threading
import unittest
from datetime import date, time
from unittest.mock import patch
from uuid import uuid4

from database import DatabaseUnavailable, PostgresRepository, SuspiciousScheduleUpdate
from schedule_analysis import change_details, compare_schedules
from schedule_api import Lesson


@unittest.skipUnless(os.environ.get("ALWAYS_DSTU_TEST_DB") == "1", "PostgreSQL test disabled")
class PostgresNotesTests(unittest.TestCase):
    def setUp(self):
        self.repo = PostgresRepository()
        self.repo.initialize_schema()
        self.group = "test_notes_" + uuid4().hex

    def tearDown(self):
        with self.repo._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM groups WHERE group_name = %s", (self.group,))

    def test_notes_survive_month_changes_and_return_to_matching_lesson(self):
        november = Lesson(date(2026, 11, 23), time(9), time(10, 30), "Math", "Teacher", "101")
        december = Lesson(date(2026, 12, 2), time(9), time(10, 30), "Physics", "Teacher", "201")
        self.repo.save_schedule(self.group, [november])
        self.repo.save_note(self.repo.current_schedule(self.group)[0].lesson_id, "Remember this")

        self.repo.save_schedule(self.group, [december])
        self.assertEqual([note.note_text for note in self.repo.detached_notes(self.group)], ["Remember this"])
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "")

        self.repo.save_schedule(self.group, [november])
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "Remember this")
        self.assertEqual(self.repo.detached_notes(self.group), [])

    def test_unmatched_notes_survive_and_explicit_clear_removes_them(self):
        day = date(2026, 11, 23)
        old = Lesson(day, time(9), time(10), "Old")
        new = Lesson(day, time(10), time(11), "New")
        self.repo.save_schedule(self.group, [old])
        self.repo.save_note(self.repo.current_schedule(self.group)[0].lesson_id, "Do not lose")
        self.repo.save_schedule(self.group, [new])

        self.assertEqual([note.note_text for note in self.repo.detached_notes(self.group)], ["Do not lose"])
        self.repo.clear_group_cache(self.group)
        self.assertEqual(self.repo.current_schedule(self.group), [])
        self.assertEqual(self.repo.detached_notes(self.group), [])

    def test_edit_and_delete_detached_note(self):
        old = Lesson(date(2026, 11, 23), time(9), time(10), "Old")
        new = Lesson(date(2026, 12, 2), time(9), time(10), "New")
        self.repo.save_schedule(self.group, [old])
        self.repo.save_note(self.repo.current_schedule(self.group)[0].lesson_id, "Before")
        self.repo.save_schedule(self.group, [new])
        detached = self.repo.detached_notes(self.group)[0]
        with self.assertRaises(ValueError):
            self.repo.save_detached_note(detached.note_id, "   ")
        self.assertTrue(self.repo.save_detached_note(detached.note_id, "After"))
        self.repo.save_schedule(self.group, [old])
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "After")

        self.repo.save_schedule(self.group, [new])
        detached = self.repo.detached_notes(self.group)[0]
        self.assertTrue(self.repo.delete_detached_note(detached.note_id))
        self.assertFalse(self.repo.delete_detached_note(detached.note_id))
        self.repo.save_schedule(self.group, [old])
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "")

    def test_reordered_identical_schedule_does_not_create_a_snapshot(self):
        day = date(2026, 11, 23)
        lessons = [
            Lesson(day, time(9), time(10, 30), "Язык"),
            Lesson(day, time(9), time(10, 30), "Алгебра"),
        ]
        first = self.repo.save_schedule(self.group, lessons)
        second = self.repo.save_schedule(self.group, list(reversed(lessons)))
        self.assertEqual(first, second)
        self.assertEqual(len(self.repo.latest_schedule_versions(self.group)), 1)

    def test_saved_snapshots_compare_swapped_lesson_times(self):
        day = date(2026, 11, 23)
        before = [
            Lesson(day, time(9), time(10, 30), "Math", "Ivanov"),
            Lesson(day, time(10, 40), time(12, 10), "Physics", "Petrov"),
        ]
        after = [
            Lesson(day, time(9), time(10, 30), "Physics", "Petrov"),
            Lesson(day, time(10, 40), time(12, 10), "Math", "Ivanov"),
        ]
        self.repo.save_schedule(self.group, before)
        self.repo.save_schedule(self.group, after)
        current, previous = self.repo.latest_schedule_versions(self.group)

        changes = compare_schedules(previous.entries, current.entries)

        self.assertEqual(len(changes), 2)
        self.assertTrue(all(change.old.subject == change.new.subject for change in changes))
        self.assertTrue(all("время" in change_details(change) for change in changes))

    def test_sync_time_tracks_current_snapshot_and_clears_with_cache(self):
        lesson = Lesson(date(2026, 11, 23), time(9), time(10, 30), "Math")
        self.repo.save_schedule(self.group, [lesson])
        last_success = self.repo.last_successful_sync(self.group)
        self.assertIsNotNone(last_success)
        self.repo.record_sync_failure(self.group, "offline")
        self.assertEqual(self.repo.last_successful_sync(self.group), last_success)

        self.repo.clear_group_cache(self.group)
        self.assertIsNone(self.repo.last_successful_sync(self.group))
        with self.repo._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM sync_log s JOIN groups g ON g.group_id = s.group_id "
                    "WHERE g.group_name = %s",
                    (self.group,),
                )
                self.assertEqual(cursor.fetchone()[0], 2)

    def test_large_same_month_drop_requires_confirmation_and_preserves_cache(self):
        lessons = [
            Lesson(date(2026, 11, day), time(9), time(10, 30), "Math")
            for day in range(1, 9)
        ]
        old_snapshot = self.repo.save_schedule(self.group, lessons)
        first = self.repo.current_schedule(self.group)[0]
        self.repo.save_note(first.lesson_id, "Keep note")

        with self.assertRaises(SuspiciousScheduleUpdate) as error:
            self.repo.save_schedule(self.group, lessons[:2])
        self.assertEqual((error.exception.old_count, error.exception.new_count), (8, 2))
        self.assertEqual(len(self.repo.current_schedule(self.group)), 8)
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "Keep note")
        self.assertEqual(self.repo.latest_schedule_versions(self.group)[0].snapshot_id, old_snapshot)

        new_snapshot = self.repo.save_schedule(self.group, lessons[:2], allow_large_decrease=True)
        self.assertNotEqual(new_snapshot, old_snapshot)
        self.assertEqual(len(self.repo.current_schedule(self.group)), 2)
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "Keep note")

    def test_different_month_is_not_treated_as_a_large_drop(self):
        lessons = [
            Lesson(date(2026, 11, day), time(9), time(10, 30), "Math")
            for day in range(1, 9)
        ]
        self.repo.save_schedule(self.group, lessons)
        next_month = Lesson(date(2026, 12, 1), time(9), time(10, 30), "Math")
        self.repo.save_schedule(self.group, [next_month])
        self.assertEqual(self.repo.current_schedule(self.group)[0].lesson_date, date(2026, 12, 1))

    def test_database_query_error_is_reported_as_database_unavailable(self):
        with self.assertRaises(DatabaseUnavailable):
            with self.repo._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 / 0")

    def test_failed_month_replacement_rolls_back_old_schedule_and_notes(self):
        old = Lesson(date(2026, 11, 23), time(9), time(10, 30), "Math")
        invalid = Lesson(date(2026, 12, 2), time(9), time(10, 30), "X" * 151)
        self.repo.save_schedule(self.group, [old])
        original = self.repo.current_schedule(self.group)[0]
        self.repo.save_note(original.lesson_id, "Keep after error")

        with self.assertRaises(DatabaseUnavailable):
            self.repo.save_schedule(self.group, [invalid])

        remaining = self.repo.current_schedule(self.group)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].lesson_id, original.lesson_id)
        self.assertEqual(remaining[0].note_text, "Keep after error")
        self.assertEqual(self.repo.detached_notes(self.group), [])

    def test_unique_lesson_note_follows_changed_time(self):
        day = date(2026, 11, 23)
        before = Lesson(day, time(9), time(10, 30), "Math", "Teacher", "101")
        after = Lesson(day, time(11), time(12, 30), "Math", "Teacher", "101")
        self.repo.save_schedule(self.group, [before])
        self.repo.save_note(self.repo.current_schedule(self.group)[0].lesson_id, "Original")

        self.repo.save_schedule(self.group, [after])

        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "Original")
        self.assertEqual(self.repo.detached_notes(self.group), [])

    def test_note_operations_reject_lesson_from_previous_snapshot(self):
        day = date(2026, 11, 23)
        old = Lesson(day, time(9), time(10, 30), "Math")
        new = Lesson(day, time(11), time(12, 30), "Math")
        self.repo.save_schedule(self.group, [old])
        old_id = self.repo.current_schedule(self.group)[0].lesson_id
        old_note_id = self.repo.save_note(old_id, "Original")
        self.repo.save_schedule(self.group, [new])

        with self.assertRaisesRegex(ValueError, "Расписание изменилось"):
            self.repo.save_note(old_id, "Late edit")
        with self.assertRaisesRegex(ValueError, "Расписание изменилось"):
            self.repo.delete_note(old_note_id)
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "Original")

    def test_concurrent_sync_does_not_silently_write_note_to_old_snapshot(self):
        day = date(2026, 11, 23)
        old = Lesson(day, time(9), time(10, 30), "Math")
        new = Lesson(day, time(11), time(12, 30), "Math")
        self.repo.save_schedule(self.group, [old])
        old_id = self.repo.current_schedule(self.group)[0].lesson_id
        self.repo.save_note(old_id, "Original")
        archived = threading.Event()
        release = threading.Event()
        edited = threading.Event()
        errors = []
        original_archive = PostgresRepository._archive_snapshot_notes

        def archive_and_wait(cursor, group_id, snapshot_id):
            original_archive(cursor, group_id, snapshot_id)
            archived.set()
            if not release.wait(5):
                raise RuntimeError("Timed out waiting for concurrent test")

        def sync():
            try:
                self.repo.save_schedule(self.group, [new])
            except Exception as exc:
                errors.append(("sync", exc))

        def edit():
            try:
                self.repo.save_note(old_id, "Late edit")
            except Exception as exc:
                errors.append(("edit", exc))
            finally:
                edited.set()

        with patch.object(self.repo, "_archive_snapshot_notes", side_effect=archive_and_wait):
            sync_thread = threading.Thread(target=sync, daemon=True)
            edit_thread = threading.Thread(target=edit, daemon=True)
            sync_thread.start()
            try:
                self.assertTrue(archived.wait(5))
                edit_thread.start()
                self.assertFalse(edited.wait(0.3), "Concurrent edit did not wait for synchronization")
            finally:
                release.set()
                sync_thread.join(5)
                if edit_thread.ident is not None:
                    edit_thread.join(5)
        self.assertFalse(sync_thread.is_alive())
        self.assertFalse(edit_thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], "edit")
        self.assertIsInstance(errors[0][1], ValueError)
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "Original")

    def test_sync_waits_for_note_edit_and_copies_committed_text(self):
        day = date(2026, 11, 23)
        old = Lesson(day, time(9), time(10, 30), "Math")
        new = Lesson(day, time(11), time(12, 30), "Math")
        self.repo.save_schedule(self.group, [old])
        old_id = self.repo.current_schedule(self.group)[0].lesson_id
        self.repo.save_note(old_id, "Original")
        started = threading.Event()
        finished = threading.Event()
        errors = []

        def sync():
            started.set()
            try:
                self.repo.save_schedule(self.group, [new])
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        with self.repo._connect() as connection:
            with connection.cursor() as cursor:
                self.repo._lock_current_lesson(cursor, old_id)
                cursor.execute("UPDATE notes SET note_text = %s WHERE lesson_id = %s", ("Edited", old_id))
                thread = threading.Thread(target=sync, daemon=True)
                thread.start()
                self.assertTrue(started.wait(5))
                self.assertFalse(finished.wait(0.3), "Synchronization did not wait for note edit")
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "Edited")

    def test_ambiguous_same_subject_does_not_attach_note_to_wrong_lesson(self):
        day = date(2026, 11, 23)
        first = Lesson(day, time(9), time(10, 30), "Math", "Teacher A", "101")
        second = Lesson(day, time(11), time(12, 30), "Math", "Teacher B", "202")
        replacement = Lesson(day, time(13), time(14, 30), "Math", "Teacher B", "202")
        self.repo.save_schedule(self.group, [first, second])
        self.repo.save_note(self.repo.current_schedule(self.group)[0].lesson_id, "First subgroup")

        self.repo.save_schedule(self.group, [replacement])

        self.assertEqual(self.repo.current_schedule(self.group)[0].note_text, "")
        self.assertEqual(
            [note.note_text for note in self.repo.detached_notes(self.group)], ["First subgroup"]
        )


if __name__ == "__main__":
    unittest.main()
