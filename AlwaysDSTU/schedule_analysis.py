"""Аналитика расписания и сравнение двух сохранённых снимков."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Iterable, Literal, Protocol

from database import ScheduleEntry


Period = Literal["day", "week", "month"]


class TimedLesson(Protocol):
    lesson_date: date
    start_time: time
    end_time: time


@dataclass(frozen=True, slots=True)
class PeriodSummary:
    start_date: date
    end_date: date
    lesson_count: int
    total_minutes: int


@dataclass(frozen=True, slots=True)
class ScheduleChange:
    kind: Literal["added", "cancelled", "changed"]
    old: ScheduleEntry | None
    new: ScheduleEntry | None


def period_bounds(period: Period, anchor: date) -> tuple[date, date]:
    """Возвращает включительные границы дня, недели или месяца."""
    if period == "day":
        return anchor, anchor
    if period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        start = anchor.replace(day=1)
        if start.month == 12:
            next_month = date(start.year + 1, 1, 1)
        else:
            next_month = date(start.year, start.month + 1, 1)
        return start, next_month - timedelta(days=1)
    raise ValueError(f"Неизвестный период аналитики: {period}")


def summarize_schedule(
    entries: Iterable[TimedLesson], period: Period, anchor: date
) -> PeriodSummary:
    start, end = period_bounds(period, anchor)
    selected = [entry for entry in entries if start <= entry.lesson_date <= end]
    total_minutes = sum(
        int((entry.end_time.hour * 60 + entry.end_time.minute)
            - (entry.start_time.hour * 60 + entry.start_time.minute))
        for entry in selected
    )
    return PeriodSummary(start, end, len(selected), total_minutes)


def compare_schedules(
    previous: Iterable[ScheduleEntry], current: Iterable[ScheduleEntry]
) -> list[ScheduleChange]:
    """Сопоставляет пары по дате и дисциплине, учитывая повторы занятий.

    Сначала удаляются полностью совпавшие занятия, затем изменения времени,
    преподавателя или аудитории связываются по уникальным признакам. Если
    неоднозначно определить пару невозможно, она отображается как отменённая
    и добавленная, а не как предположительное изменение.
    """
    old_entries = sorted(previous, key=_sort_key)
    new_entries = sorted(current, key=_sort_key)
    old_used = [False] * len(old_entries)
    new_used = [False] * len(new_entries)
    changes: list[ScheduleChange] = []

    _pair_by_key(
        old_entries,
        new_entries,
        old_used,
        new_used,
        lambda item: _full_key(item),
        changes,
        changed=False,
    )
    for key_function in (
        _teacher_key,
        _room_key,
        _date_subject_key,
        _date_start_key,
    ):
        _pair_by_key(
            old_entries,
            new_entries,
            old_used,
            new_used,
            key_function,
            changes,
            changed=True,
            only_unique=True,
        )

    changes.extend(
        ScheduleChange("cancelled", old_entries[index], None)
        for index, used in enumerate(old_used)
        if not used
    )
    changes.extend(
        ScheduleChange("added", None, new_entries[index])
        for index, used in enumerate(new_used)
        if not used
    )
    return sorted(
        changes,
        key=lambda change: _sort_key(change.new or change.old),
    )


def change_details(change: ScheduleChange) -> str:
    """Формирует краткое описание различий для таблицы интерфейса."""
    if change.kind == "added" and change.new:
        return "Добавлено"
    if change.kind == "cancelled" and change.old:
        return "Отменено"
    if not change.old or not change.new:
        return "Изменено"

    fields = (
        ("время", _time_range(change.old), _time_range(change.new)),
        ("преподаватель", change.old.teacher or "—", change.new.teacher or "—"),
        ("аудитория", change.old.room or "—", change.new.room or "—"),
        ("дисциплина", change.old.subject, change.new.subject),
    )
    return "; ".join(
        f"{label}: {before} → {after}"
        for label, before, after in fields
        if before != after
    ) or "Изменены данные занятия"


def _pair_by_key(
    old_entries: list[ScheduleEntry],
    new_entries: list[ScheduleEntry],
    old_used: list[bool],
    new_used: list[bool],
    key_function,
    changes: list[ScheduleChange],
    *,
    changed: bool,
    only_unique: bool = False,
) -> None:
    old_groups: dict[tuple, deque[int]] = defaultdict(deque)
    new_groups: dict[tuple, deque[int]] = defaultdict(deque)
    for index, entry in enumerate(old_entries):
        if not old_used[index]:
            old_groups[key_function(entry)].append(index)
    for index, entry in enumerate(new_entries):
        if not new_used[index]:
            new_groups[key_function(entry)].append(index)

    for key in old_groups.keys() & new_groups.keys():
        old_indices = old_groups[key]
        new_indices = new_groups[key]
        if only_unique and (len(old_indices) != 1 or len(new_indices) != 1):
            continue
        pair_count = min(len(old_indices), len(new_indices))
        for _ in range(pair_count):
            old_index = old_indices.popleft()
            new_index = new_indices.popleft()
            old_used[old_index] = True
            new_used[new_index] = True
            if changed:
                changes.append(
                    ScheduleChange("changed", old_entries[old_index], new_entries[new_index])
                )


def _full_key(entry: ScheduleEntry) -> tuple:
    return (
        entry.lesson_date,
        entry.start_time,
        entry.end_time,
        _normalized(entry.subject),
        _normalized(entry.teacher),
        _normalized(entry.room),
    )


def _teacher_key(entry: ScheduleEntry) -> tuple:
    return entry.lesson_date, _normalized(entry.subject), _normalized(entry.teacher)


def _date_start_key(entry: ScheduleEntry) -> tuple:
    return entry.lesson_date, entry.start_time


def _room_key(entry: ScheduleEntry) -> tuple:
    return entry.lesson_date, _normalized(entry.subject), _normalized(entry.room)


def _date_subject_key(entry: ScheduleEntry) -> tuple:
    return entry.lesson_date, _normalized(entry.subject)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _sort_key(entry: ScheduleEntry | None) -> tuple:
    if entry is None:
        return date.min, 0, "", "", ""
    return (
        entry.lesson_date,
        entry.start_time.hour * 60 + entry.start_time.minute,
        _normalized(entry.subject),
        _normalized(entry.teacher),
        _normalized(entry.room),
    )


def _time_range(entry: ScheduleEntry) -> str:
    return f"{entry.start_time:%H:%M}–{entry.end_time:%H:%M}"
