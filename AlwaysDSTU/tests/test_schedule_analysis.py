"""Проверки аналитики и сравнения расписаний без PostgreSQL."""

from datetime import date, time
import unittest

from database import ScheduleEntry
from schedule_analysis import (
    change_details,
    compare_schedules,
    period_bounds,
    summarize_schedule,
)


def lesson(
    day: date,
    start: str,
    end: str,
    subject: str = "Математика",
    teacher: str = "Иванов И.И.",
    room: str = "А-101",
    lesson_id: int = 1,
) -> ScheduleEntry:
    return ScheduleEntry(
        lesson_id=lesson_id,
        lesson_date=day,
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        subject=subject,
        teacher=teacher,
        room=room,
        note_id=None,
        note_text="",
    )


class ScheduleAnalysisTests(unittest.TestCase):
    def test_day_summary_counts_lessons_and_duration(self):
        target = date(2026, 11, 23)
        entries = [
            lesson(target, "09:00", "10:30", lesson_id=1),
            lesson(target, "10:40", "12:10", subject="Физика", lesson_id=2),
            lesson(date(2026, 11, 24), "09:00", "10:30", lesson_id=3),
        ]

        result = summarize_schedule(entries, "day", target)

        self.assertEqual((result.lesson_count, result.total_minutes), (2, 180))
        self.assertEqual((result.start_date, result.end_date), (target, target))

    def test_week_starts_on_monday_and_month_handles_year_boundary(self):
        self.assertEqual(
            period_bounds("week", date(2026, 11, 25)),
            (date(2026, 11, 23), date(2026, 11, 29)),
        )
        self.assertEqual(
            period_bounds("month", date(2026, 12, 20)),
            (date(2026, 12, 1), date(2026, 12, 31)),
        )

    def test_changed_time_and_room_are_reported_as_one_change(self):
        day = date(2026, 11, 23)
        before = lesson(day, "09:00", "10:30", lesson_id=1)
        after = lesson(day, "10:00", "11:30", room="Б-202", lesson_id=2)

        changes = compare_schedules([before], [after])

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].kind, "changed")
        self.assertIn("время", change_details(changes[0]))
        self.assertIn("аудитория", change_details(changes[0]))

    def test_changed_subject_with_same_unique_start_is_detected(self):
        day = date(2026, 11, 23)
        before = lesson(day, "09:00", "10:30", subject="Математика")
        after = lesson(day, "09:00", "10:30", subject="Алгебра", lesson_id=2)

        changes = compare_schedules([before], [after])

        self.assertEqual([change.kind for change in changes], ["changed"])
        self.assertIn("дисциплина", change_details(changes[0]))

    def test_two_subjects_swapping_times_are_matched_by_subject(self):
        day = date(2026, 11, 23)
        before = [
            lesson(day, "09:00", "10:30", subject="Математика", teacher="Иванов", lesson_id=1),
            lesson(day, "10:40", "12:10", subject="Физика", teacher="Петров", lesson_id=2),
        ]
        after = [
            lesson(day, "09:00", "10:30", subject="Физика", teacher="Петров", lesson_id=3),
            lesson(day, "10:40", "12:10", subject="Математика", teacher="Иванов", lesson_id=4),
        ]

        changes = compare_schedules(before, after)

        self.assertEqual(len(changes), 2)
        self.assertTrue(all(change.kind == "changed" for change in changes))
        for change in changes:
            self.assertEqual(change.old.subject, change.new.subject)
            self.assertIn("время", change_details(change))

    def test_added_cancelled_and_unchanged_lessons(self):
        day = date(2026, 11, 23)
        unchanged = lesson(day, "09:00", "10:30", lesson_id=1)
        cancelled = lesson(day, "10:40", "12:10", "Физика", lesson_id=2)
        added = lesson(day, "12:20", "13:50", "История", lesson_id=3)

        changes = compare_schedules(
            [unchanged, cancelled],
            [lesson(day, "09:00", "10:30", lesson_id=11), added],
        )

        self.assertCountEqual([change.kind for change in changes], ["cancelled", "added"])
        self.assertEqual(compare_schedules([unchanged], [unchanged]), [])

    def test_ambiguous_duplicates_are_not_guessed_as_modifications(self):
        day = date(2026, 11, 23)
        before = [
            lesson(day, "09:00", "10:30", lesson_id=1),
            lesson(day, "10:40", "12:10", lesson_id=2),
        ]
        after = [
            lesson(day, "11:00", "12:30", lesson_id=3),
            lesson(day, "13:00", "14:30", lesson_id=4),
        ]

        changes = compare_schedules(before, after)

        self.assertEqual(len(changes), 4)
        self.assertCountEqual([change.kind for change in changes], ["cancelled"] * 2 + ["added"] * 2)


if __name__ == "__main__":
    unittest.main()
