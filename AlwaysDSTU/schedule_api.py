"""Загрузка учебных групп и расписания из JSON API сайта ДГТУ."""

from __future__ import annotations

import json
import ssl
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE_URL = "https://edu.donstu.ru/api/"


class ScheduleApiError(RuntimeError):
    """Ошибка запроса или структуры ответа API расписания."""


@dataclass(frozen=True, slots=True)
class Group:
    """Учебная группа из удалённого справочника ДГТУ."""

    remote_id: int
    name: str


@dataclass(frozen=True, slots=True)
class Lesson:
    """Нормализованные данные одной пары."""

    lesson_date: date
    start_time: time
    end_time: time
    subject: str
    teacher: str = ""
    room: str = ""
    pair_number: int | None = None


class ScheduleApiClient:
    """Клиент API, по которому расписание получает приложение IKMS."""

    def __init__(self, base_url: str = API_BASE_URL, timeout: float = 12.0):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def _get_json(self, path: str) -> dict:
        request = Request(
            self.base_url + path,
            headers={
                "Accept": "application/json",
                "User-Agent": "AlwaysDSTU/0.1 (student project)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout, context=_tls_context()) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ScheduleApiError(f"Не удалось получить данные ДГТУ: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScheduleApiError("Сайт ДГТУ вернул ответ не в формате JSON.") from exc

        if not isinstance(payload, dict):
            raise ScheduleApiError("Неожиданный формат ответа API ДГТУ.")
        state = payload.get("state")
        if isinstance(state, int) and state < 0:
            raise ScheduleApiError("API ДГТУ сообщил об ошибке. Сохранённая версия не заменена.")
        return payload

    def fetch_groups(self) -> list[Group]:
        """Получает справочник групп из `raspGrouplist`."""
        payload = self._get_json("raspGrouplist")
        raw_groups = payload.get("data")
        if not isinstance(raw_groups, list):
            raise ScheduleApiError("В ответе API не найден список учебных групп.")

        groups: list[Group] = []
        for item in raw_groups:
            if not isinstance(item, dict):
                continue
            try:
                remote_id = int(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                groups.append(Group(remote_id=remote_id, name=name))

        if not groups:
            raise ScheduleApiError("Список групп от API ДГТУ пуст или повреждён.")
        return sorted(groups, key=lambda group: group.name.casefold())

    def fetch_schedule(self, remote_group_id: int) -> list[Lesson]:
        """Получает и преобразует занятия группы по её идентификатору API."""
        query = urlencode({"idGroup": int(remote_group_id)})
        payload = self._get_json(f"Rasp?{query}")
        data = payload.get("data")
        raw_lessons = data.get("rasp") if isinstance(data, dict) else None
        if not isinstance(raw_lessons, list):
            raise ScheduleApiError("В ответе API не найден список занятий группы.")
        if not raw_lessons:
            raise ScheduleApiError(
                "API вернуло пустое расписание. Сохранённая версия не заменена."
            )

        lessons: list[Lesson] = []
        invalid_rows = 0
        for item in raw_lessons:
            if not isinstance(item, dict):
                invalid_rows += 1
                continue
            try:
                lesson_date = self._parse_date(item.get("дата"))
                start_time = self._parse_time(item.get("начало"))
                end_time = self._parse_time(item.get("конец"))
                subject = str(item.get("дисциплина") or "").strip()
                if not subject or end_time <= start_time:
                    raise ValueError("не заполнен предмет или неверно указано время")
                pair_value = item.get("номерЗанятия")
                pair_number = int(pair_value) if pair_value is not None else None
                lessons.append(
                    Lesson(
                        lesson_date=lesson_date,
                        start_time=start_time,
                        end_time=end_time,
                        subject=subject,
                        teacher=str(item.get("преподаватель") or "").strip(),
                        room=str(item.get("аудитория") or "").strip(),
                        pair_number=pair_number,
                    )
                )
            except (TypeError, ValueError, OverflowError):
                invalid_rows += 1

        if invalid_rows:
            raise ScheduleApiError(
                "Часть расписания пришла с неполными данными. "
                "Сохранённая версия не заменена."
            )
        if not lessons:
            raise ScheduleApiError("В ответе API нет корректных занятий.")

        return sorted(
            lessons,
            key=lambda lesson: (lesson.lesson_date, lesson.start_time, lesson.subject),
        )

    @staticmethod
    def _parse_date(raw_value: object) -> date:
        value = str(raw_value or "").strip()
        if not value:
            raise ValueError("не указана дата занятия")
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            return date.fromisoformat(value[:10])

    @staticmethod
    def _parse_time(raw_value: object) -> time:
        value = str(raw_value or "").strip()
        if not value:
            raise ValueError("не указано время занятия")
        return time.fromisoformat(value)


def filter_schedule_month(lessons: list[Lesson], month: str) -> list[Lesson]:
    """Оставляет только занятия выбранного месяца (`YYYY-MM`)."""
    try:
        first_day = date.fromisoformat(month + "-01")
    except ValueError as exc:
        raise ValueError("Месяц должен быть указан в формате ГГГГ-ММ.") from exc
    if first_day.strftime("%Y-%m") != month:
        raise ValueError("Месяц должен быть указан в формате ГГГГ-ММ.")
    return [lesson for lesson in lessons if lesson.lesson_date.strftime("%Y-%m") == month]


def _tls_context() -> ssl.SSLContext:
    """Проверяет сертификат сервера через доверенное хранилище Windows."""
    if sys.platform == "win32":
        try:
            import truststore
        except ImportError:
            pass
        else:
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return ssl.create_default_context()
