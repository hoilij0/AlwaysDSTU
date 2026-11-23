"""Репозиторий PostgreSQL для расписания и заметок Always DSTU."""

from __future__ import annotations

import os
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from schedule_api import Lesson


_POSTGRES_DLL_HANDLES: dict[str, object] = {}


class DatabaseUnavailable(RuntimeError):
    """База данных недоступна или драйвер PostgreSQL не установлен."""


class SuspiciousScheduleUpdate(ValueError):
    """Ответ содержит значительно меньше занятий, чем сохранённая версия."""

    def __init__(self, old_count: int, new_count: int):
        self.old_count = old_count
        self.new_count = new_count
        super().__init__(
            f"На сайте получено {new_count} занятий вместо {old_count} в кэше."
        )


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    """Занятие текущего снимка вместе с последней заметкой."""

    lesson_id: int
    lesson_date: date
    start_time: time
    end_time: time
    subject: str
    teacher: str
    room: str
    note_id: int | None
    note_text: str
    author_name: str = ""


@dataclass(frozen=True, slots=True)
class ScheduleVersion:
    """Сохранённый снимок расписания вместе с занятиями."""

    snapshot_id: int
    downloaded_at: datetime
    entries: list[ScheduleEntry]


@dataclass(frozen=True, slots=True)
class DetachedNote:
    note_id: int
    lesson_date: date
    start_time: time
    subject: str
    note_text: str
    author_name: str = ""


def connection_params(database: str | None = None) -> dict[str, object]:
    """Параметры PostgreSQL берутся из стандартных переменных PG* среды."""
    try:
        port = int(os.environ.get("PGPORT", "5432"))
    except ValueError as exc:
        raise DatabaseUnavailable("Переменная PGPORT должна быть числом.") from exc

    params: dict[str, object] = {
        "host": os.environ.get("PGHOST", "127.0.0.1"),
        "port": port,
        "dbname": database or os.environ.get("PGDATABASE", "always_dstu"),
        "user": os.environ.get("PGUSER", "postgres"),
        "connect_timeout": 4,
    }
    password = os.environ.get("PGPASSWORD")
    if password:
        params["password"] = password
    return params


class PostgresRepository:
    """Минимальный слой хранения для первого рабочего сценария ЛР6."""

    @contextmanager
    def _connect(self, database: str | None = None):
        _prepare_libpq_dll_search()
        try:
            import psycopg
        except (ImportError, OSError) as exc:
            raise DatabaseUnavailable(
                "Не удалось загрузить драйвер PostgreSQL или библиотеку libpq. "
                "Установите зависимости, проверьте установку PostgreSQL или задайте "
                "PG_LIBDIR для нестандартного пути. Выполните: "
                "python -m pip install -r requirements.txt"
            ) from exc

        try:
            with psycopg.connect(**connection_params(database)) as connection:
                yield connection
        except psycopg.Error as exc:
            raise DatabaseUnavailable(
                "Ошибка PostgreSQL при подключении или выполнении запроса. "
                "Проверьте доступность службы, настройки подключения и схему базы. "
                "Изменения незавершённой операции отменены."
            ) from exc

    def initialize_schema(self) -> None:
        """Создаёт таблицы по утверждённой схеме, не удаляя существующие данные."""
        schema_path = Path(__file__).resolve().parents[1] / "Always DSTU.sql"
        if not schema_path.exists():
            raise DatabaseUnavailable(f"Не найден файл схемы БД: {schema_path}")
        statements = schema_path.read_text(encoding="utf-8").split(";")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    if statement.strip():
                        cursor.execute(statement)

    def list_groups(self) -> list[str]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT group_name FROM groups ORDER BY group_name")
                return [row[0] for row in cursor.fetchall()]

    def selected_group(self) -> str | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT setting_value FROM app_settings WHERE setting_key = 'selected_group'"
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def local_nickname(self) -> str:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT setting_value FROM app_settings WHERE setting_key = 'nickname'")
                row = cursor.fetchone()
                return row[0] if row else ""

    def ui_theme(self) -> str:
        """Неизвестное или отсутствующее значение безопасно открывает светлую тему."""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT setting_value FROM app_settings WHERE setting_key = 'ui_theme'")
                row = cursor.fetchone()
                return row[0] if row and row[0] in ("light", "dark") else "light"

    def save_profile_settings(self, nickname: str, group_name: str, theme: str) -> None:
        """Обновляет ник, группу и тему вместе, не изменяя расписание и заметки."""
        nickname, group_name = nickname.strip(), group_name.strip()
        if not nickname:
            raise ValueError("Укажите ник пользователя.")
        if len(nickname) > 50:
            raise ValueError("Ник должен содержать не более 50 символов.")
        if not group_name:
            raise ValueError("Выберите учебную группу.")
        if theme not in ("light", "dark"):
            raise ValueError("Выберите светлую или тёмную тему.")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for key, value in (("nickname", nickname), ("selected_group", group_name),
                                   ("ui_theme", theme)):
                    cursor.execute(
                        "INSERT INTO app_settings (setting_key, setting_value) VALUES (%s, %s) "
                        "ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
                        (key, value),
                    )

    def save_local_nickname(self, nickname: str) -> None:
        """Сохраняет локальный профиль без регистрации и изменения старых заметок."""
        nickname = nickname.strip()
        if not nickname:
            raise ValueError("Укажите ник пользователя.")
        if len(nickname) > 50:
            raise ValueError("Ник должен содержать не более 50 символов.")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._save_nickname(cursor, nickname)

    @staticmethod
    def _save_nickname(cursor, nickname: str) -> None:
        cursor.execute(
            "INSERT INTO app_settings (setting_key, setting_value) VALUES ('nickname', %s) "
            "ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
            (nickname,),
        )

    def save_selected_group(self, group_name: str) -> None:
        group_name = group_name.strip()
        if not group_name:
            raise ValueError("Выберите учебную группу.")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO app_settings (setting_key, setting_value)
                    VALUES ('selected_group', %s)
                    ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value
                    """,
                    (group_name,),
                )

    def save_schedule(
        self, group_name: str, lessons: list[Lesson], *, allow_large_decrease: bool = False,
        full_response: list[Lesson] | None = None,
    ) -> int:
        """Сохраняет новую версию атомарно; старый снимок остаётся при ошибке."""
        normalized_name = group_name.strip()
        if not normalized_name:
            raise ValueError("Сначала выберите учебную группу.")
        if not lessons:
            raise ValueError("Пустое расписание нельзя сохранить вместо кэша.")

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO groups (group_name)
                    VALUES (%s)
                    ON CONFLICT (group_name) DO UPDATE
                    SET group_name = EXCLUDED.group_name
                    RETURNING group_id
                    """,
                    (normalized_name,),
                )
                group_id = cursor.fetchone()[0]
                incoming_rows = sorted(
                    {
                        (
                            lesson.lesson_date,
                            lesson.start_time,
                            lesson.end_time,
                            lesson.subject,
                            lesson.teacher or "",
                            lesson.room or "",
                        )
                        for lesson in lessons
                    },
                    key=lambda row: (row[0], row[1], row[2], row[3], row[4], row[5]),
                )
                months = {(row[0].year, row[0].month) for row in incoming_rows}
                if len(months) != 1:
                    raise ValueError("В одном снимке можно хранить расписание только одного месяца.")
                year, month = next(iter(months))
                month_start = date(year, month, 1)
                month_end = (
                    date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
                )

                cursor.execute(
                    "SELECT snapshot_id FROM schedule_snapshots "
                    "WHERE group_id = %s AND is_current = TRUE",
                    (group_id,),
                )
                current_snapshot = cursor.fetchone()
                if current_snapshot:
                    current_snapshot_id = current_snapshot[0]
                    cursor.execute(
                        """
                        SELECT lesson_date, start_time, end_time, subject,
                               COALESCE(teacher, ''), COALESCE(room, '')
                        FROM lessons WHERE snapshot_id = %s
                        ORDER BY lesson_date, start_time, end_time, subject, teacher, room
                        """,
                        (current_snapshot_id,),
                    )
                    # Порядок строк в PostgreSQL зависит от локали сервера, поэтому
                    # сравниваем обе версии с одинаковой сортировкой Python.
                    current_rows = sorted(tuple(row) for row in cursor.fetchall())
                    if current_rows == incoming_rows:
                        if full_response is not None:
                            self._save_boundary_cache(cursor, current_snapshot_id, month_start, month_end, full_response)
                        cursor.execute(
                            """
                            INSERT INTO sync_log (group_id, snapshot_id, status, details)
                            VALUES (%s, %s, 'Успешно', %s)
                            """,
                            (group_id, current_snapshot_id,
                             f"Изменений нет; занятий: {len(incoming_rows)}"),
                        )
                        self._prune_snapshot_history(cursor, group_id)
                        return current_snapshot_id
                    same_month = all(month_start <= row[0] < month_end for row in current_rows)
                    if (
                        not allow_large_decrease
                        and same_month
                        and len(current_rows) >= 6
                        and len(incoming_rows) * 2 < len(current_rows)
                    ):
                        raise SuspiciousScheduleUpdate(len(current_rows), len(incoming_rows))
                    self._archive_snapshot_notes(cursor, group_id, current_snapshot_id)

                # Успешная загрузка нового месяца заменяет кэш старого.
                # Удаление находится в той же транзакции, что и запись нового снимка.
                cursor.execute(
                    """
                    DELETE FROM schedule_snapshots AS s
                    WHERE s.group_id = %s
                      AND (
                        NOT EXISTS (
                            SELECT 1 FROM lessons AS l WHERE l.snapshot_id = s.snapshot_id
                        )
                        OR EXISTS (
                            SELECT 1 FROM lessons AS l
                            WHERE l.snapshot_id = s.snapshot_id
                              AND (l.lesson_date < %s OR l.lesson_date >= %s)
                        )
                      )
                    """,
                    (group_id, month_start, month_end),
                )
                cursor.execute(
                    "UPDATE schedule_snapshots SET is_current = FALSE "
                    "WHERE group_id = %s AND is_current = TRUE",
                    (group_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO schedule_snapshots (group_id, is_current)
                    VALUES (%s, TRUE)
                    RETURNING snapshot_id
                    """,
                    (group_id,),
                )
                snapshot_id = cursor.fetchone()[0]
                new_lessons = []
                for row in incoming_rows:
                    lesson_date, start_time, end_time, subject, teacher, room = row
                    cursor.execute(
                        """
                        INSERT INTO lessons (
                            snapshot_id, lesson_date, start_time, end_time,
                            subject, teacher, room
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING lesson_id
                        """,
                        (
                            snapshot_id,
                            lesson_date,
                            start_time,
                            end_time,
                            subject,
                            teacher or None,
                            room or None,
                        ),
                    )
                    new_lesson_id = cursor.fetchone()[0]
                    new_lessons.append((new_lesson_id, row))
                self._restore_archived_notes(cursor, group_id, month_start, month_end, new_lessons)
                if full_response is not None:
                    self._save_boundary_cache(cursor, snapshot_id, month_start, month_end, full_response)
                cursor.execute(
                    """
                    INSERT INTO sync_log (group_id, snapshot_id, status, details)
                    VALUES (%s, %s, 'Успешно', %s)
                    """,
                    (group_id, snapshot_id, f"Получено занятий: {len(incoming_rows)}"),
                )
                self._prune_snapshot_history(cursor, group_id)
                return snapshot_id

    @staticmethod
    def _save_boundary_cache(cursor, snapshot_id, month_start, month_end, full_response) -> None:
        if not full_response:
            raise ValueError("Пустой ответ не может обновить кэш соседних дней.")
        last_day = month_end - timedelta(days=1)
        first_week_start = month_start - timedelta(days=month_start.weekday())
        last_week_end = last_day + timedelta(days=6 - last_day.weekday())
        rows = {
            (lesson.lesson_date, lesson.start_time, lesson.end_time, lesson.subject,
             lesson.teacher or "", lesson.room or "")
            for lesson in full_response
            if first_week_start <= lesson.lesson_date <= last_week_end
            and not month_start <= lesson.lesson_date < month_end
        }
        cursor.execute("DELETE FROM boundary_lessons WHERE snapshot_id = %s", (snapshot_id,))
        for row in sorted(rows):
            cursor.execute(
                "INSERT INTO boundary_lessons (snapshot_id, lesson_date, start_time, end_time, "
                "subject, teacher, room) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (snapshot_id, *row),
            )
        dates = [lesson.lesson_date for lesson in full_response]
        cursor.execute(
            "INSERT INTO boundary_cache_state (snapshot_id, response_first_date, response_last_date) "
            "VALUES (%s, %s, %s) ON CONFLICT (snapshot_id) DO UPDATE "
            "SET response_first_date = EXCLUDED.response_first_date, "
            "response_last_date = EXCLUDED.response_last_date",
            (snapshot_id, min(dates), max(dates)),
        )

    def boundary_schedule(self, group_name: str):
        """Граничные дни и диапазон дат исходного ответа для текущего снимка."""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT b.response_first_date, b.response_last_date, s.snapshot_id "
                    "FROM groups g JOIN schedule_snapshots s ON s.group_id = g.group_id AND s.is_current "
                    "JOIN boundary_cache_state b ON b.snapshot_id = s.snapshot_id WHERE g.group_name = %s",
                    (group_name.strip(),),
                )
                state = cursor.fetchone()
                if not state:
                    return [], None
                cursor.execute(
                    "SELECT lesson_date, start_time, end_time, subject, teacher, room "
                    "FROM boundary_lessons WHERE snapshot_id = %s ORDER BY lesson_date, start_time",
                    (state[2],),
                )
                return [Lesson(*row, pair_number=None) for row in cursor.fetchall()], (state[0], state[1])

    @staticmethod
    def _archive_snapshot_notes(cursor, group_id: int, snapshot_id: int) -> None:
        cursor.execute(
            """
            SELECT l.lesson_date, l.start_time, l.end_time, l.subject,
                   COALESCE(l.teacher, ''), COALESCE(l.room, ''),
                   n.note_text, n.created_at, n.updated_at, n.author_name
            FROM lessons AS l LEFT JOIN notes AS n ON n.lesson_id = l.lesson_id
            WHERE l.snapshot_id = %s
            """,
            (snapshot_id,),
        )
        lessons = cursor.fetchall()
        counts = Counter((row[0], _normalize_subject(row[3])) for row in lessons)
        for lesson in lessons:
            lesson_date, start, end, subject, teacher, room, text, created, updated, author = lesson
            if text is None:
                continue
            unique = counts[(lesson_date, _normalize_subject(subject))] == 1
            cursor.execute(
                """
                INSERT INTO detached_notes (
                    group_id, lesson_date, start_time, end_time, subject, teacher, room,
                    note_text, created_at, updated_at, matching_unique, author_name
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (group_id, lesson_date, start_time, end_time, subject, teacher, room)
                DO UPDATE SET note_text = EXCLUDED.note_text,
                              created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at,
                              matching_unique = EXCLUDED.matching_unique, author_name = EXCLUDED.author_name
                """,
                (group_id, lesson_date, start, end, subject, teacher, room,
                 text, created, updated, unique, author),
            )

    @staticmethod
    def _restore_archived_notes(cursor, group_id, month_start, month_end, new_lessons) -> None:
        cursor.execute(
            """
            SELECT detached_note_id, lesson_date, start_time, end_time, subject, teacher, room,
                   note_text, created_at, updated_at, matching_unique, author_name
            FROM detached_notes
            WHERE group_id = %s AND lesson_date >= %s AND lesson_date < %s
            """,
            (group_id, month_start, month_end),
        )
        archived = cursor.fetchall()
        archived_counts = Counter((row[1], _normalize_subject(row[4])) for row in archived)
        lesson_counts = Counter((row[0], _normalize_subject(row[3])) for _, row in new_lessons)
        remaining = {lesson_id: tuple(row) for lesson_id, row in new_lessons}

        # Полное совпадение надёжнее эвристики для повторяющихся пар.
        for exact_only in (True, False):
            for note in list(archived):
                (note_id, lesson_date, start, end, subject, teacher, room,
                 text, created, updated, matching_unique, author) = note
                exact = (lesson_date, start, end, subject, teacher, room)
                key = (lesson_date, _normalize_subject(subject))
                if exact_only:
                    candidates = [lesson_id for lesson_id, row in remaining.items() if row == exact]
                elif matching_unique and archived_counts[key] == lesson_counts[key] == 1:
                    candidates = [
                        lesson_id for lesson_id, row in remaining.items()
                        if (row[0], _normalize_subject(row[3])) == key
                    ]
                else:
                    continue
                if len(candidates) != 1:
                    continue
                lesson_id = candidates[0]
                cursor.execute(
                    "INSERT INTO notes (lesson_id, note_text, created_at, updated_at, author_name) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (lesson_id, text, created, updated, author),
                )
                cursor.execute("DELETE FROM detached_notes WHERE detached_note_id = %s", (note_id,))
                del remaining[lesson_id]
                archived.remove(note)

    @staticmethod
    def _prune_snapshot_history(cursor, group_id: int) -> None:
        """Оставляет две последние версии расписания текущего месяца."""
        cursor.execute(
            """
            DELETE FROM schedule_snapshots
            WHERE group_id = %s
              AND snapshot_id NOT IN (
                SELECT snapshot_id
                FROM schedule_snapshots
                WHERE group_id = %s
                ORDER BY downloaded_at DESC, snapshot_id DESC
                LIMIT 2
              )
            """,
            (group_id, group_id),
        )

    def record_sync_failure(self, group_name: str, details: str) -> None:
        """Записывает неудачную попытку, не затрагивая текущий снимок."""
        normalized_name = group_name.strip()
        if not normalized_name:
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO groups (group_name)
                    VALUES (%s)
                    ON CONFLICT (group_name) DO UPDATE
                    SET group_name = EXCLUDED.group_name
                    RETURNING group_id
                    """,
                    (normalized_name,),
                )
                group_id = cursor.fetchone()[0]
                cursor.execute(
                    """
                    INSERT INTO sync_log (group_id, status, details)
                    VALUES (%s, 'Ошибка', %s)
                    """,
                    (group_id, details[:2000]),
                )

    def current_schedule(self, group_name: str) -> list[ScheduleEntry]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT l.lesson_id, l.lesson_date, l.start_time, l.end_time,
                           l.subject, COALESCE(l.teacher, ''), COALESCE(l.room, ''),
                           n.note_id, COALESCE(n.note_text, ''), COALESCE(n.author_name, '')
                    FROM groups AS g
                    JOIN schedule_snapshots AS s
                      ON s.group_id = g.group_id AND s.is_current = TRUE
                    JOIN lessons AS l ON l.snapshot_id = s.snapshot_id
                    LEFT JOIN LATERAL (
                        SELECT note_id, note_text, author_name
                        FROM notes
                        WHERE lesson_id = l.lesson_id
                        ORDER BY updated_at DESC, note_id DESC
                        LIMIT 1
                    ) AS n ON TRUE
                    WHERE g.group_name = %s
                    ORDER BY l.lesson_date, l.start_time, l.lesson_id
                    """,
                    (group_name.strip(),),
                )
                return [ScheduleEntry(*row) for row in cursor.fetchall()]

    def current_snapshot_id(self, group_name: str) -> int | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.snapshot_id FROM schedule_snapshots AS s
                    JOIN groups AS g ON g.group_id = s.group_id
                    WHERE g.group_name = %s AND s.is_current = TRUE
                    """,
                    (group_name.strip(),),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def latest_schedule_versions(self, group_name: str) -> list[ScheduleVersion]:
        """Возвращает текущий и предыдущий сохранённые снимки группы."""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.snapshot_id, s.downloaded_at
                    FROM schedule_snapshots AS s
                    JOIN groups AS g ON g.group_id = s.group_id
                    WHERE g.group_name = %s
                    ORDER BY s.downloaded_at DESC, s.snapshot_id DESC
                    LIMIT 2
                    """,
                    (group_name.strip(),),
                )
                snapshots = cursor.fetchall()
                versions: list[ScheduleVersion] = []
                for snapshot_id, downloaded_at in snapshots:
                    cursor.execute(
                        """
                        SELECT l.lesson_id, l.lesson_date, l.start_time, l.end_time,
                               l.subject, COALESCE(l.teacher, ''), COALESCE(l.room, ''),
                               n.note_id, COALESCE(n.note_text, ''), COALESCE(n.author_name, '')
                        FROM lessons AS l
                        LEFT JOIN LATERAL (
                            SELECT note_id, note_text, author_name
                            FROM notes
                            WHERE lesson_id = l.lesson_id
                            ORDER BY updated_at DESC, note_id DESC
                            LIMIT 1
                        ) AS n ON TRUE
                        WHERE l.snapshot_id = %s
                        ORDER BY l.lesson_date, l.start_time, l.lesson_id
                        """,
                        (snapshot_id,),
                    )
                    versions.append(
                        ScheduleVersion(
                            snapshot_id=snapshot_id,
                            downloaded_at=downloaded_at,
                            entries=[ScheduleEntry(*row) for row in cursor.fetchall()],
                        )
                    )
                return versions

    def last_successful_sync(self, group_name: str) -> datetime | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT sl.sync_time
                    FROM sync_log AS sl
                    JOIN groups AS g ON g.group_id = sl.group_id
                    JOIN schedule_snapshots AS s ON s.snapshot_id = sl.snapshot_id
                        AND s.group_id = g.group_id AND s.is_current = TRUE
                    WHERE g.group_name = %s AND sl.status = 'Успешно'
                    ORDER BY sl.sync_time DESC, sl.sync_id DESC
                    LIMIT 1
                    """,
                    (group_name.strip(),),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def save_note(self, lesson_id: int, note_text: str, author_name: str = "") -> int:
        """Создаёт заметку или обновляет единственную заметку к занятию."""
        normalized_text = note_text.strip()
        if not normalized_text:
            raise ValueError("Текст заметки не должен быть пустым.")

        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._lock_current_lesson(cursor, lesson_id)
                cursor.execute(
                    """
                    INSERT INTO notes (lesson_id, note_text, author_name)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (lesson_id) DO UPDATE
                    SET note_text = EXCLUDED.note_text, author_name = EXCLUDED.author_name,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING note_id
                    """,
                    (lesson_id, normalized_text, author_name.strip()),
                )
                note_id = cursor.fetchone()[0]
                if author_name.strip():
                    self._save_nickname(cursor, author_name.strip())
                return note_id

    def delete_note(self, note_id: int) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT l.lesson_id
                    FROM notes AS n
                    JOIN lessons AS l ON l.lesson_id = n.lesson_id
                    WHERE n.note_id = %s
                    """,
                    (note_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return False
                self._lock_current_lesson(cursor, row[0])
                cursor.execute("DELETE FROM notes WHERE note_id = %s", (note_id,))
                return cursor.rowcount > 0

    @staticmethod
    def _lock_current_lesson(cursor, lesson_id: int) -> None:
        """Сериализует изменение заметки с обновлением/очисткой группы."""
        cursor.execute(
            """
            SELECT g.group_id
            FROM groups AS g
            JOIN schedule_snapshots AS s ON s.group_id = g.group_id
            JOIN lessons AS l ON l.snapshot_id = s.snapshot_id
            WHERE l.lesson_id = %s
            FOR UPDATE OF g
            """,
            (lesson_id,),
        )
        if cursor.fetchone() is None:
            raise ValueError(
                "Занятие больше недоступно. Скопируйте текст заметки, "
                "обновите расписание и откройте занятие заново."
            )
        cursor.execute(
            """
            SELECT l.lesson_id FROM lessons AS l
            JOIN schedule_snapshots AS s ON s.snapshot_id = l.snapshot_id
            WHERE l.lesson_id = %s AND s.is_current = TRUE
            """,
            (lesson_id,),
        )
        if cursor.fetchone() is None:
            raise ValueError(
                "Расписание изменилось в другом окне. Скопируйте текст заметки, "
                "обновите расписание и откройте занятие заново."
            )

    def clear_group_cache(self, group_name: str) -> int:
        """Удаляет снимки и связанные с ними занятия/заметки одной группы."""
        normalized_name = group_name.strip()
        if not normalized_name:
            raise ValueError("Сначала выберите учебную группу.")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT group_id FROM groups WHERE group_name = %s FOR UPDATE", (normalized_name,))
                row = cursor.fetchone()
                if row is None:
                    return 0
                group_id = row[0]
                cursor.execute(
                    "DELETE FROM detached_notes WHERE group_id = %s",
                    (group_id,),
                )
                cursor.execute(
                    "DELETE FROM schedule_snapshots WHERE group_id = %s",
                    (group_id,),
                )
                return cursor.rowcount

    def detached_notes(self, group_name: str) -> list[DetachedNote]:
        """Заметки отсутствующих занятий и заметки других месяцев."""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT n.detached_note_id, n.lesson_date, n.start_time,
                           n.subject, n.note_text, n.author_name
                    FROM detached_notes AS n JOIN groups AS g ON g.group_id = n.group_id
                    WHERE g.group_name = %s
                    ORDER BY n.lesson_date, n.start_time, n.detached_note_id
                    """,
                    (group_name.strip(),),
                )
                return [DetachedNote(*row) for row in cursor.fetchall()]

    def save_detached_note(self, note_id: int, note_text: str, author_name: str = "") -> bool:
        value = note_text.strip()
        if not value:
            raise ValueError("Текст заметки не должен быть пустым.")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                if not self._lock_detached_note_group(cursor, note_id):
                    return False
                cursor.execute(
                    "UPDATE detached_notes SET note_text = %s, author_name = %s, updated_at = CURRENT_TIMESTAMP "
                    "WHERE detached_note_id = %s",
                    (value, author_name.strip(), note_id),
                )
                updated = cursor.rowcount > 0
                if updated and author_name.strip():
                    self._save_nickname(cursor, author_name.strip())
                return updated

    def delete_detached_note(self, note_id: int) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                if not self._lock_detached_note_group(cursor, note_id):
                    return False
                cursor.execute("DELETE FROM detached_notes WHERE detached_note_id = %s", (note_id,))
                return cursor.rowcount > 0

    @staticmethod
    def _lock_detached_note_group(cursor, note_id: int) -> bool:
        cursor.execute(
            """
            SELECT g.group_id FROM groups AS g
            JOIN detached_notes AS n ON n.group_id = g.group_id
            WHERE n.detached_note_id = %s
            FOR UPDATE OF g
            """,
            (note_id,),
        )
        return cursor.fetchone() is not None


def _normalize_subject(subject: str) -> str:
    return " ".join(subject.casefold().split())


def _prepare_libpq_dll_search() -> None:
    """Добавляет стандартную папку PostgreSQL в поиск libpq на Windows."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return

    candidates: list[Path] = []
    configured_directory = os.environ.get("PG_LIBDIR")
    if configured_directory:
        candidates.append(Path(configured_directory))

    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    postgres_directory = program_files / "PostgreSQL"
    if postgres_directory.exists():
        candidates.extend(sorted(postgres_directory.glob("*/bin"), reverse=True))

    for directory in candidates:
        key = str(directory.resolve()).casefold()
        if key in _POSTGRES_DLL_HANDLES:
            return
        if (directory / "libpq.dll").is_file():
            try:
                _POSTGRES_DLL_HANDLES[key] = os.add_dll_directory(str(directory))
            except OSError:
                continue
            current_paths = os.environ.get("PATH", "").split(os.pathsep)
            if all(str(directory).casefold() != path.casefold() for path in current_paths):
                os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
            return
