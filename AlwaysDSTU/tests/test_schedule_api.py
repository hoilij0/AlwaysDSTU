"""Проверки преобразования JSON API ДГТУ без сетевого подключения."""

import unittest
import json
from datetime import date, time
from unittest.mock import patch

from schedule_api import Group, Lesson, ScheduleApiClient, ScheduleApiError, filter_schedule_month


class FixtureScheduleApiClient(ScheduleApiClient):
    def __init__(self, payloads: dict[str, dict]):
        super().__init__(base_url="https://example.test/api/")
        self.payloads = payloads

    def _get_json(self, path: str) -> dict:
        return self.payloads[path]


class ScheduleApiTests(unittest.TestCase):
    def test_negative_api_status_rejects_even_if_data_is_present(self) -> None:
        response_payload = {"state": -1, "data": {"rasp": [{"дисциплина": "Partial"}]}}
        with patch("schedule_api.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps(
                response_payload
            ).encode("utf-8")
            with self.assertRaisesRegex(ScheduleApiError, "сообщил об ошибке"):
                ScheduleApiClient()._get_json("Rasp?idGroup=108")

    def test_fetch_groups_maps_remote_ids_and_names(self) -> None:
        client = FixtureScheduleApiClient(
            {"raspGrouplist": {"data": [{"id": 108, "name": "ВИС32"}]}}
        )

        self.assertEqual(client.fetch_groups(), [Group(remote_id=108, name="ВИС32")])

    def test_fetch_schedule_maps_lesson_fields(self) -> None:
        client = FixtureScheduleApiClient(
            {
                "Rasp?idGroup=108": {
                    "state": 0,
                    "data": {
                        "rasp": [
                            {
                                "дата": "2026-11-23T00:00:00",
                                "начало": "10:10",
                                "конец": "11:45",
                                "номерЗанятия": 2,
                                "дисциплина": "Базы данных",
                                "преподаватель": "Иванов И. И.",
                                "аудитория": "3-201",
                            }
                        ]
                    },
                }
            }
        )

        lesson = client.fetch_schedule(108)[0]
        self.assertEqual(lesson.lesson_date, date(2026, 11, 23))
        self.assertEqual(lesson.start_time, time(10, 10))
        self.assertEqual(lesson.end_time, time(11, 45))
        self.assertEqual(lesson.pair_number, 2)
        self.assertEqual(lesson.subject, "Базы данных")
        self.assertEqual(lesson.teacher, "Иванов И. И.")
        self.assertEqual(lesson.room, "3-201")

    def test_empty_schedule_is_rejected(self) -> None:
        client = FixtureScheduleApiClient(
            {"Rasp?idGroup=108": {"data": {"rasp": []}}}
        )

        with self.assertRaises(ScheduleApiError):
            client.fetch_schedule(108)

    def test_incomplete_response_is_rejected_as_a_whole(self) -> None:
        client = FixtureScheduleApiClient(
            {
                "Rasp?idGroup=108": {
                    "data": {
                        "rasp": [
                            {
                                "дата": "2026-11-23T00:00:00",
                                "начало": "10:10",
                                "конец": "11:45",
                                "номерЗанятия": 2,
                                "дисциплина": "Базы данных",
                            },
                            {"дисциплина": "Неполная запись"},
                        ]
                    }
                }
            }
        )

        with self.assertRaises(ScheduleApiError):
            client.fetch_schedule(108)

    def test_filter_schedule_month_discards_other_months(self) -> None:
        lessons = [
            Lesson(date(2026, 10, 31), time(9), time(10), "October"),
            Lesson(date(2026, 11, 1), time(9), time(10), "November"),
            Lesson(date(2026, 11, 30), time(9), time(10), "November 2"),
            Lesson(date(2026, 12, 1), time(9), time(10), "December"),
        ]

        self.assertEqual(
            [lesson.subject for lesson in filter_schedule_month(lessons, "2026-11")],
            ["November", "November 2"],
        )

    def test_filter_schedule_month_rejects_invalid_month(self) -> None:
        with self.assertRaises(ValueError):
            filter_schedule_month([], "2026-13")


if __name__ == "__main__":
    unittest.main()
