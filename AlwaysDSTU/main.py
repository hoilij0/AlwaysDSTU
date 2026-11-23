"""Настольный прототип Always DSTU для лабораторной работы № 6."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from database import (
    DatabaseUnavailable,
    PostgresRepository,
    ScheduleEntry,
    SuspiciousScheduleUpdate,
)
from schedule_api import Group, ScheduleApiClient, ScheduleApiError, filter_schedule_month
from schedule_analysis import (
    ScheduleChange,
    change_details,
    compare_schedules,
    period_bounds,
    summarize_schedule,
)


NAVY = "#18365D"
BLUE = "#2869C8"
PALE_BLUE = "#EAF1FC"
BACKGROUND = "#F4F6FA"
TEXT = "#17243A"
MUTED = "#62738D"
GREEN = "#20875B"
ICON_DIR = Path(__file__).resolve().parent / "assets"

THEMES = {
    "light": {
        "background": BACKGROUND, "surface": "white", "text": TEXT, "muted": MUTED,
        "blue": BLUE, "accent": BLUE, "soft": PALE_BLUE, "navy": NAVY,
        "footer": "#E8EDF5", "border": "#D5E1F3", "green": GREEN,
        "hover": "#DCE9FB", "selected": "#D7E6FC", "header_text": "#D7E3F5",
        "load_1": "#008A3C", "load_2": "#718000", "load_3": "#A36B00", "load_4": "#C92727",
        "added_bg": "#EAF7F1", "added_fg": "#20875B", "cancelled_bg": "#FFF0F0",
        "cancelled_fg": "#B52F37", "changed_bg": "#EAF1FC", "changed_fg": NAVY,
    },
    "dark": {
        "background": "#111B2A", "surface": "#1C2A3D", "text": "#ECF2FC",
        "muted": "#ACBCD1", "blue": "#94C2FF", "accent": "#2869C8", "soft": "#263D59",
        "navy": "#0A1423", "footer": "#152235", "border": "#40536B", "green": "#7AD5AA",
        "hover": "#314967", "selected": "#304C70", "header_text": "#CDDCF1",
        "load_1": "#4ADE80", "load_2": "#C6E94D", "load_3": "#FFDC4A", "load_4": "#FF7373",
        "added_bg": "#1C4237", "added_fg": "#9BE7BD", "cancelled_bg": "#502D36",
        "cancelled_fg": "#FFC0C0", "changed_bg": "#293D59", "changed_fg": "#D3E6FF",
    },
}


def _apply_window_icon(root: tk.Tk | tk.Toplevel) -> None:
    """Задаёт иконку всем окнам; отсутствие ресурса не мешает запуску."""
    try:
        if sys.platform == "win32":
            icon = ICON_DIR / "icon.ico"
            if icon.is_file():
                root.iconbitmap(default=str(icon))
                root.iconbitmap(str(icon))
        else:
            icon = ICON_DIR / "icon.png"
            if icon.is_file():
                root._always_dstu_icon = tk.PhotoImage(file=str(icon))
                root.iconphoto(True, root._always_dstu_icon)
    except (tk.TclError, OSError):
        # Иконка необязательна для работы расписания и локальной базы.
        pass


def _configure_windows_taskbar() -> None:
    """Не даёт Windows объединять окно приложения со значком Python."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AlwaysDSTU.Schedule.Desktop")
    except (AttributeError, OSError):
        pass


class AlwaysDSTUApp:
    """Главный экран расписания и форма добавления заметки к паре."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.repository = PostgresRepository()
        self.schedule_api = ScheduleApiClient()
        self.storage_ready = False
        self.busy = False
        self.note_editor_open = False
        self.group_search_open = False
        self.profile_editor_open = False
        self.profile_group_combo = None
        self.profile_group_locked = False
        self.group_search_dialog = None
        self.group_search_load_button = None
        self.group_search_refresh = None
        self.group_values = ["ВИС32"]
        self.theme_name = "light"
        self.palette = THEMES["light"]
        self._theme_applied = False
        self.remote_groups: dict[str, Group] = {}
        self.api_months: dict[str, list[str]] = {}
        self.entries: list[ScheduleEntry] = []
        self.boundary_entries = []
        self.boundary_coverage = None
        self.last_sync_time: datetime | None = None

        self.group_var = tk.StringVar(value="ВИС32")
        self.month_filter_var = tk.StringVar(value=date.today().strftime("%Y-%m"))
        self.date_filter_var = tk.StringVar(value="Все даты")
        self.status_var = tk.StringVar(value="Подключение к PostgreSQL…")
        self.sync_var = tk.StringVar(value="Последняя синхронизация: нет данных")

        self._configure_window()
        self._build_interface()
        self._initialize_storage()

    def _configure_window(self) -> None:
        self.root.title("Always DSTU — расписание ДГТУ")
        _apply_window_icon(self.root)
        self.root.geometry("1160x740")
        self.root.minsize(940, 600)
        self.root.configure(background=BACKGROUND)

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=BACKGROUND)
        style.configure("TLabel", background=BACKGROUND, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Heading.TLabel", font=("Segoe UI", 16, "bold"), foreground=TEXT)
        style.configure("Subheading.TLabel", font=("Segoe UI", 12, "bold"), foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TButton", background=PALE_BLUE, foreground=TEXT, borderwidth=0,
                        relief="flat", padding=(10, 6), font=("Segoe UI", 9))
        style.map("TButton", background=[("disabled", BACKGROUND), ("active", "#DCE9FB")],
                  foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=BLUE, foreground="white", padding=(14, 8),
                        borderwidth=0, relief="flat")
        style.map("Accent.TButton", background=[("active", "#1D56A8"), ("disabled", "#AAB9CF")])
        style.configure("Treeview", rowheight=34, font=("Segoe UI", 10), background="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background=PALE_BLUE)
        style.configure("Calendar.TButton", padding=(3, 1), anchor="center", font=("Segoe UI", 9),
                        background="white", foreground=TEXT, borderwidth=0, relief="flat")
        style.map("Calendar.TButton", background=[("active", PALE_BLUE), ("disabled", "white")],
                  foreground=[("disabled", MUTED)])
        style.configure(
            "CalendarSelected.TButton",
            padding=(3, 0),
            anchor="center",
            font=("Segoe UI", 9),
            background=BLUE,
            foreground="white",
            borderwidth=0,
            relief="flat",
        )
        style.map("CalendarSelected.TButton", background=[("active", "#1D56A8")], foreground=[("active", "white")])
        style.configure("Sidebar.TFrame", background="white")
        style.configure("Sidebar.TLabel", background="white", foreground=TEXT, font=("Segoe UI", 10))
        style.configure("SidebarMuted.TLabel", background="white", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("SidebarHeading.TLabel", background="white", foreground=TEXT, font=("Segoe UI", 11, "bold"))
        style.configure("Sidebar.TButton", padding=(10, 7), anchor="w", background="white",
                        foreground=TEXT, borderwidth=0, relief="flat")
        style.map("Sidebar.TButton", background=[("active", PALE_BLUE), ("disabled", "white")],
                  foreground=[("disabled", MUTED)])
        style.configure("Link.TButton", padding=(8, 4), anchor="center", background=BACKGROUND,
                        foreground=BLUE, borderwidth=0, relief="flat")
        style.configure("WeekNav.TButton", padding=(4, 3), anchor="center", background=BACKGROUND,
                        foreground=BLUE, borderwidth=0, relief="flat", font=("Segoe UI", 14))
        style.map("Treeview", background=[("selected", "#D7E6FC")], foreground=[("selected", TEXT)])

    def _apply_theme(self, theme_name: str) -> None:
        """Применяет сохранённую палитру, не закрывая окна и не меняя данные."""
        if theme_name not in THEMES:
            theme_name = "light"
        if self._theme_applied and theme_name == self.theme_name:
            return
        new = THEMES.get(theme_name, THEMES["light"])
        old = self.palette
        self.palette = new
        self.theme_name = theme_name
        self._theme_applied = True
        style = ttk.Style(self.root)
        style.configure("TFrame", background=new["background"])
        style.configure("TLabel", background=new["background"], foreground=new["text"])
        style.configure("Heading.TLabel", foreground=new["text"])
        style.configure("Subheading.TLabel", foreground=new["text"])
        style.configure("Muted.TLabel", foreground=new["muted"])
        style.configure("TButton", background=new["soft"], foreground=new["text"])
        style.map("TButton", background=[("disabled", new["background"]), ("active", new["hover"])],
                  foreground=[("disabled", new["muted"])])
        style.configure("Accent.TButton", background=new["accent"], foreground="white")
        style.map("Accent.TButton", background=[("disabled", new["border"]), ("active", new["accent"])],
                  foreground=[("disabled", new["muted"])])
        for name in ("Link.TButton", "WeekNav.TButton"):
            style.configure(name, background=new["background"], foreground=new["blue"])
            style.map(name, background=[("disabled", new["background"]), ("active", new["soft"])],
                      foreground=[("disabled", new["muted"])])
        style.configure("Calendar.TButton", background=new["background"], foreground=new["text"])
        style.map("Calendar.TButton", background=[("disabled", new["background"]), ("active", new["soft"])],
                  foreground=[("disabled", new["muted"])])
        style.configure("CalendarSelected.TButton", background=new["accent"], foreground="white")
        style.map("CalendarSelected.TButton", background=[("disabled", new["border"]), ("active", new["accent"])],
                  foreground=[("disabled", new["muted"]), ("active", "white")])
        style.configure("Sidebar.TFrame", background=new["surface"])
        for name in ("Sidebar.TLabel", "SidebarHeading.TLabel"):
            style.configure(name, background=new["surface"], foreground=new["text"])
        style.configure("SidebarMuted.TLabel", background=new["surface"], foreground=new["muted"])
        style.configure("Sidebar.TButton", background=new["surface"], foreground=new["text"])
        style.map("Sidebar.TButton", background=[("disabled", new["surface"]), ("active", new["soft"])],
                  foreground=[("disabled", new["muted"])])
        style.configure("Treeview", background=new["surface"], fieldbackground=new["surface"],
                        foreground=new["text"])
        style.configure("Treeview.Heading", background=new["soft"], foreground=new["text"])
        style.map("Treeview", background=[("selected", new["selected"])],
                  foreground=[("selected", new["text"])])
        style.configure("TCombobox", background=new["surface"], fieldbackground=new["surface"],
                        foreground=new["text"], arrowcolor=new["muted"], bordercolor=new["border"])
        style.map("TCombobox", fieldbackground=[("readonly", new["surface"])],
                  foreground=[("readonly", new["text"])])
        style.configure("TEntry", fieldbackground=new["surface"], foreground=new["text"],
                        insertcolor=new["text"], bordercolor=new["border"])
        style.configure("TScrollbar", background=new["surface"], troughcolor=new["background"],
                        arrowcolor=new["muted"], bordercolor=new["background"],
                        lightcolor=new["surface"], darkcolor=new["surface"])
        style.map("TScrollbar", background=[("active", new["soft"])])
        self.root.option_add("*TCombobox*Listbox.background", new["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", new["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", new["selected"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", new["text"])
        self._retint_open_widgets(old, new)

    def _retint_open_widgets(self, old: dict, new: dict) -> None:
        background = {old[key].lower(): new[key] for key in
                      ("background", "surface", "soft", "navy", "footer", "border", "accent", "selected")}
        foreground = {old[key].lower(): new[key] for key in
                      ("text", "muted", "blue", "green", "header_text",
                       "load_1", "load_2", "load_3", "load_4")}
        background["white"] = new["surface"]

        def visit(widget) -> None:
            if isinstance(widget, (tk.Tk, tk.Toplevel, tk.Frame, tk.Label, tk.Text, tk.Canvas, tk.Listbox)):
                for option, colors in (("background", background), ("foreground", foreground),
                                       ("insertbackground", foreground), ("highlightbackground", background),
                                       ("highlightcolor", foreground), ("selectbackground", background),
                                       ("selectforeground", foreground)):
                    try:
                        value = str(widget.cget(option)).lower()
                        if value in colors:
                            widget.configure(**{option: colors[value]})
                    except tk.TclError:
                        pass
            if isinstance(widget, ttk.Treeview):
                for kind in ("added", "cancelled", "changed"):
                    widget.tag_configure(kind, background=new[kind + "_bg"], foreground=new[kind + "_fg"])
                widget.tag_configure("has_note", foreground=new["green"])
            for child in widget.winfo_children():
                visit(child)

        visit(self.root)

    def _build_interface(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, padx=24, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="Always DSTU", bg=NAVY, fg="white",
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")

        footer = tk.Label(self.root, textvariable=self.status_var, anchor="w", bg="#E8EDF5",
                          fg=MUTED, padx=20, pady=7, font=("Segoe UI", 9))
        footer.pack(fill="x", side="bottom")
        body = ttk.Frame(self.root, padding=(18, 14, 18, 12))
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, style="Sidebar.TFrame", padding=(14, 16))
        sidebar.pack(side="left", fill="y", padx=(0, 18))
        main = ttk.Frame(body)
        main.pack(side="left", fill="both", expand=True)

        ttk.Label(sidebar, text="РАЗДЕЛЫ", style="SidebarMuted.TLabel").pack(anchor="w", pady=(0, 8))
        self.analysis_button = ttk.Button(sidebar, text="Аналитика и изменения",
                                          style="Sidebar.TButton", command=self.open_analysis)
        self.analysis_button.pack(fill="x", pady=2)
        self.detached_notes_button = ttk.Button(sidebar, text="Другие заметки",
                                                style="Sidebar.TButton", command=self.open_detached_notes)
        self.detached_notes_button.pack(fill="x", pady=2)
        self.profile_button = ttk.Button(sidebar, text="Профиль и настройки",
                                         style="Sidebar.TButton", command=self.open_profile)
        self.profile_button.pack(fill="x", pady=2)

        heading = ttk.Frame(main)
        heading.pack(fill="x", pady=(0, 8))
        ttk.Label(heading, text="Расписание", style="Heading.TLabel").pack(side="left")
        self.sync_button = ttk.Button(heading, text="Обновить", style="Link.TButton",
                                      command=self.synchronize_schedule)
        self.sync_button.pack(side="right")
        ttk.Label(main, textvariable=self.sync_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 12))

        navigation = ttk.Frame(main)
        navigation.pack(fill="x", pady=(0, 6))
        self.week_month_var = tk.StringVar()
        ttk.Label(navigation, textvariable=self.week_month_var,
                  style="Subheading.TLabel").pack(side="left")
        self.next_week_button = ttk.Button(navigation, text="›", width=3, style="WeekNav.TButton",
                                           command=lambda: self._move_week(1))
        self.next_week_button.pack(side="right")
        self.previous_week_button = ttk.Button(navigation, text="‹", width=3, style="WeekNav.TButton",
                                               command=lambda: self._move_week(-1))
        self.previous_week_button.pack(side="right", padx=(0, 4))

        days = ttk.Frame(main)
        days.pack(fill="x", pady=(0, 14))
        self.week_day_buttons = []
        self.week_count_labels = []
        self.week_day_dates = []
        for column in range(7):
            days.columnconfigure(column, weight=1)
            day_column = ttk.Frame(days)
            day_column.grid(row=0, column=column, sticky="ew", padx=2)
            button = ttk.Button(day_column, style="Calendar.TButton")
            button.pack(fill="x")
            self.week_day_buttons.append(button)
            count = tk.Label(day_column, text="—", bg=BACKGROUND, fg=MUTED,
                             font=("Segoe UI", 9, "bold"), cursor="hand2")
            count.pack(fill="x", pady=(2, 0))
            self.week_count_labels.append(count)

        self.calendar_tab = ttk.Frame(main)
        self.calendar_tab.pack(fill="both", expand=True)
        self._build_day_view()

    def _build_day_view(self) -> None:
        self.calendar_day_var = tk.StringVar(value="Выберите день")
        ttk.Label(self.calendar_tab, textvariable=self.calendar_day_var,
                  style="Heading.TLabel").pack(anchor="w", pady=(0, 10))
        frame = ttk.Frame(self.calendar_tab)
        frame.pack(fill="both", expand=True)
        self.cards_canvas = tk.Canvas(frame, bg=BACKGROUND, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.cards_canvas.yview)
        self.cards_canvas.configure(yscrollcommand=scrollbar.set)
        self.cards_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.cards_frame = tk.Frame(self.cards_canvas, bg=BACKGROUND)
        self.cards_window = self.cards_canvas.create_window((0, 0), window=self.cards_frame, anchor="nw")
        self.cards_frame.bind("<Configure>", lambda _event: self.cards_canvas.configure(
            scrollregion=self.cards_canvas.bbox("all")))
        self.cards_canvas.bind("<Configure>", lambda event: self.cards_canvas.itemconfigure(
            self.cards_window, width=event.width))
        self.cards_canvas.bind("<MouseWheel>", self._scroll_cards)

    def _scroll_cards(self, event) -> None:
        self.cards_canvas.yview_scroll(-int(event.delta / 120), "units")

    def _initialize_storage(self) -> None:
        try:
            self.repository.initialize_schema()
            self.storage_ready = True
            saved_groups = self.repository.list_groups()
            selected_group = self.repository.selected_group()
            nickname = self.repository.local_nickname()
            theme = self.repository.ui_theme()
            values = list(dict.fromkeys(["ВИС32", *saved_groups, *([selected_group] if selected_group else [])]))
            self.group_values = values
            if selected_group:
                self.group_var.set(selected_group)
            if self.group_var.get() not in values:
                self.group_var.set(values[0])
            self._apply_theme(theme)
            self.status_var.set("PostgreSQL подключён. Можно загрузить расписание или открыть кэш.")
            self._load_schedule()
            self._show_cached_month_if_needed()
            if not nickname:
                self.root.after(250, lambda: self.open_profile(first_run=True))
            self.root.after(500, lambda: self.synchronize_schedule(silent=True))
        except (DatabaseUnavailable, OSError) as exc:
            self.storage_ready = False
            self.status_var.set("PostgreSQL недоступен. Настройте подключение и перезапустите приложение.")
            messagebox.showwarning("Подключение к PostgreSQL", str(exc), parent=self.root)

    def _on_group_selected(self, _event: object = None, *, persist: bool = True) -> None:
        if self.storage_ready and persist:
            try:
                self.repository.save_selected_group(self.group_var.get())
            except (DatabaseUnavailable, ValueError) as exc:
                self.status_var.set(f"Не удалось сохранить выбранную группу: {exc}")
        self._load_schedule()
        self._show_cached_month_if_needed()
        if not self._has_month_cache(self.month_filter_var.get()):
            self.synchronize_schedule(silent=True)

    def _show_cached_month_if_needed(self) -> None:
        """При открытии группы не оставляет экран пустым при наличии кэша."""
        if self.entries and not self._has_month_cache(self.month_filter_var.get()):
            latest_cached = max(entry.lesson_date for entry in self.entries)
            self.month_filter_var.set(latest_cached.strftime("%Y-%m"))
            self.date_filter_var.set("Все даты")
            self._load_schedule()

    def _load_schedule(self) -> None:
        self.boundary_entries = []
        self.boundary_coverage = None
        if not self.storage_ready:
            self.entries = []
            self.last_sync_time = None
            self._update_sync_label()
            self._render_schedule()
            return
        group_name = self.group_var.get().strip()
        try:
            self.entries = self.repository.current_schedule(group_name)
            self.last_sync_time = self.repository.last_successful_sync(group_name)
        except DatabaseUnavailable as exc:
            self.entries = []
            self.last_sync_time = None
            self._update_sync_label()
            self.status_var.set(str(exc))
            self._render_schedule()
            return

        try:
            self.boundary_entries, self.boundary_coverage = self.repository.boundary_schedule(group_name)
        except DatabaseUnavailable:
            # Основной кэш остаётся доступным, даже если граничные дни не прочитались.
            pass
        self._update_date_filter()
        self._update_sync_label()
        self._render_schedule()
        if not self.entries:
            self.status_var.set("В локальном кэше пока нет занятий этой группы.")
        elif not self._has_month_cache(self.month_filter_var.get()):
            self.status_var.set(
                "В кэше сохранён другой месяц. Подключитесь к сети, чтобы загрузить выбранный."
            )
        else:
            month_count = sum(
                entry.lesson_date.strftime("%Y-%m") == self.month_filter_var.get()
                for entry in self.entries
            )
            self.status_var.set(f"Загружено из локального кэша занятий за месяц: {month_count}.")

    def _update_date_filter(self) -> None:
        selected = self.date_filter_var.get()
        if selected == "Все даты":
            return
        try:
            selected_day = datetime.strptime(selected, "%d.%m.%Y").date()
        except ValueError:
            self.date_filter_var.set("Все даты")
            return
        if selected_day.strftime("%Y-%m") != self.month_filter_var.get():
            self.date_filter_var.set("Все даты")

    def _on_month_selected(self, _event: object = None) -> None:
        self._update_date_filter()
        self._update_sync_label()
        self._render_schedule()
        if not self._has_month_cache(self.month_filter_var.get()):
            self.synchronize_schedule(silent=True)

    def _has_month_cache(self, month: str) -> bool:
        return any(entry.lesson_date.strftime("%Y-%m") == month for entry in self.entries)

    def _update_sync_label(self) -> None:
        if self.last_sync_time and self._has_month_cache(self.month_filter_var.get()):
            self.sync_var.set(f"Последняя синхронизация: {self.last_sync_time:%d.%m.%Y %H:%M}")
        else:
            self.sync_var.set("Последняя синхронизация: нет данных для выбранного месяца")

    def _render_schedule(self) -> None:
        self._render_calendar()
        self._render_calendar_day()

    def _render_calendar(self) -> None:
        selected = self._displayed_calendar_day()
        start = selected - timedelta(days=selected.weekday())
        weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        months = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
        self.week_month_var.set(f"{months[selected.month - 1]} {selected.year}")
        self.week_day_dates = [start + timedelta(days=index) for index in range(7)]
        counts = Counter(entry.lesson_date for entry in self.entries)
        boundary_counts = Counter(entry.lesson_date for entry in self.boundary_entries)
        for index, (button, day, label) in enumerate(zip(
                self.week_day_buttons, self.week_day_dates, self.week_count_labels)):
            button.configure(
                text=f"{weekdays[index]}\n{day.day:02d}",
                state="disabled" if self.busy else "normal",
                style="CalendarSelected.TButton" if selected == day else "Calendar.TButton",
                command=lambda chosen=day: self._select_date(chosen),
            )
            count = self._cached_day_count(day, counts, boundary_counts)
            color = self.palette[f"load_{min(count, 4)}"] if count else self.palette["muted"]
            label.configure(text="—" if count is None else str(count), fg=color)
            label.bind("<Button-1>", lambda _event, chosen=day: self._select_date(chosen))

    def _cached_day_count(self, day: date, counts: Counter, boundary_counts: Counter) -> int | None:
        """Количество из кэша или None, если данных о дне нет."""
        if not self.entries:
            return None
        month_start = self.entries[0].lesson_date.replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        if month_start <= day <= month_end:
            return counts[day]
        week_start = month_start - timedelta(days=month_start.weekday())
        week_end = month_end + timedelta(days=6 - month_end.weekday())
        if (self.boundary_coverage and week_start <= day <= week_end
                and self.boundary_coverage[0] <= day <= self.boundary_coverage[1]):
            return boundary_counts[day]
        return None

    def _move_week(self, direction: int) -> None:
        if not self.busy:
            self._select_date(self._displayed_calendar_day() + timedelta(days=7 * direction))

    def _select_date(self, chosen: date) -> None:
        if self.busy:
            return
        previous_month = self.month_filter_var.get()
        self.month_filter_var.set(chosen.strftime("%Y-%m"))
        self.date_filter_var.set(chosen.strftime("%d.%m.%Y"))
        if self.month_filter_var.get() != previous_month:
            self._on_month_selected()
        else:
            self._render_schedule()

    def _select_calendar_day(self, day: int) -> None:
        try:
            year, month = (int(value) for value in self.month_filter_var.get().split("-", 1))
            self._select_date(date(year, month, day))
        except ValueError:
            return

    def _displayed_calendar_day(self) -> date:
        selected_date = self.date_filter_var.get()
        if selected_date != "Все даты":
            try:
                selected = datetime.strptime(selected_date, "%d.%m.%Y").date()
                if selected.strftime("%Y-%m") == self.month_filter_var.get():
                    return selected
            except ValueError:
                pass
        today = date.today()
        if today.strftime("%Y-%m") == self.month_filter_var.get():
            return today
        try:
            return date.fromisoformat(self.month_filter_var.get() + "-01")
        except ValueError:
            return today

    def _render_calendar_day(self) -> None:
        for widget in self.cards_frame.winfo_children():
            widget.destroy()
        self.day_cards = []
        day = self._displayed_calendar_day()
        weekdays = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
        months = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
        day_entries = [entry for entry in self.entries if entry.lesson_date == day]
        self.calendar_day_var.set(f"{weekdays[day.weekday()]}, {day.day} {months[day.month - 1]}")
        p = self.palette
        if not day_entries:
            message = ("На этот день занятий нет." if self._has_month_cache(self.month_filter_var.get())
                       else "Расписание за этот месяц ещё не сохранено. Подключитесь к сети и обновите его.")
            if (not self._has_month_cache(self.month_filter_var.get()) and
                    self._cached_day_count(day, Counter(), Counter(
                        entry.lesson_date for entry in self.boundary_entries)) is not None):
                message += " Число пар сохранено для граничной недели."
            tk.Label(self.cards_frame, text=message, bg=p["background"], fg=p["muted"],
                     font=("Segoe UI", 11), wraplength=480, justify="left").pack(anchor="w", pady=18)
        for entry in day_entries:
            card = tk.Frame(self.cards_frame, bg=p["surface"], highlightbackground=p["surface"],
                            highlightcolor=p["blue"], highlightthickness=1, padx=16, pady=12,
                            cursor="hand2", takefocus=1)
            card.pack(fill="x", pady=(0, 10), padx=(0, 4))
            self.day_cards.append(card)
            chip = tk.Label(card, text=f"{entry.start_time:%H:%M}\n{entry.end_time:%H:%M}",
                            bg=p["soft"], fg=p["blue"], font=("Segoe UI", 10, "bold"), padx=10, pady=8)
            chip.pack(side="left", anchor="n", padx=(0, 16))
            details = tk.Frame(card, bg=p["surface"], cursor="hand2")
            details.pack(side="left", fill="both", expand=True)
            title = tk.Label(details, text=entry.subject, bg=p["surface"], fg=p["text"],
                             font=("Segoe UI", 12, "bold"), anchor="w", justify="left", wraplength=450)
            title.pack(fill="x")
            teacher = tk.Label(details, text=entry.teacher or "Преподаватель не указан", bg=p["surface"], fg=p["muted"],
                               font=("Segoe UI", 10), anchor="w", justify="left", wraplength=450)
            teacher.pack(fill="x", pady=(3, 0))
            room = tk.Label(details, text=f"Аудитория: {entry.room or 'не указана'}", bg=p["surface"], fg=p["muted"],
                            font=("Segoe UI", 10), anchor="w", justify="left", wraplength=450)
            room.pack(fill="x", pady=(2, 0))
            note = tk.Label(details, text="● Есть заметка" if entry.note_text else "Добавить заметку",
                            bg=p["surface"], fg=p["green"] if entry.note_text else p["blue"], font=("Segoe UI", 9), anchor="w")
            note.pack(fill="x", pady=(5, 0))
            details.bind("<Configure>", lambda event, name=title, who=teacher, where=room: (
                name.configure(wraplength=max(120, event.width - 4)),
                who.configure(wraplength=max(120, event.width - 4)),
                where.configure(wraplength=max(120, event.width - 4))))
            for widget in (card, chip, details, title, teacher, room, note):
                widget.bind("<Button-1>", lambda _event, item=entry: self.open_note_editor(item))
                widget.bind("<MouseWheel>", self._scroll_cards)
            for key in ("<Return>", "<space>"):
                card.bind(key, lambda _event, item=entry: self.open_note_editor(item))
        self.cards_canvas.yview_moveto(0)


    def open_profile(self, first_run: bool = False, parent=None) -> None:
        """Локальные настройки ника, группы и темы без регистрации."""
        if first_run and (self.busy or self.note_editor_open or self.group_search_open):
            self.root.after(1000, lambda: self.open_profile(first_run=True))
            return
        if self.busy or self.profile_editor_open or not self.storage_ready:
            return
        try:
            current_nickname = self.repository.local_nickname()
        except DatabaseUnavailable as exc:
            messagebox.showerror("Профиль", str(exc), parent=parent or self.root)
            return
        if first_run and current_nickname:
            return
        p = self.palette
        dialog = tk.Toplevel(self.root)
        _apply_window_icon(dialog)
        dialog.title("Настройте приложение" if first_run else "Профиль и настройки")
        dialog.geometry("500x520")
        dialog.minsize(460, 490)
        dialog.transient(parent or self.root)
        dialog.grab_set()
        self.profile_editor_open = True
        self.profile_group_locked = parent is not None
        header = tk.Frame(dialog, bg=p["navy"], padx=20, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="Настройте приложение" if first_run else "Профиль и настройки",
                 bg=p["navy"], fg="white", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(header, text="Настройки сохраняются на этом компьютере",
                 bg=p["navy"], fg=p["header_text"], font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Ваш ник").pack(anchor="w")
        nickname_var = tk.StringVar(value=current_nickname)
        nickname_entry = ttk.Entry(frame, textvariable=nickname_var)
        nickname_entry.pack(fill="x", pady=(5, 12))

        ttk.Label(frame, text="Учебная группа").pack(anchor="w")
        group_var = tk.StringVar(value=self.group_var.get())
        group_line = ttk.Frame(frame)
        group_line.pack(fill="x", pady=(5, 12))
        group_combo = ttk.Combobox(group_line, textvariable=group_var, values=self.group_values,
                                   state="disabled" if self.profile_group_locked else "readonly")
        group_combo.pack(side="left", fill="x", expand=True)
        self.profile_group_combo = group_combo
        group_search = ttk.Button(
            group_line, text="Найти",
            command=lambda: self.open_group_search(parent=dialog, on_choose=group_var.set),
            state="disabled" if self.profile_group_locked else "normal",
        )
        group_search.pack(side="left", padx=(8, 0))

        ttk.Label(frame, text="Тема оформления").pack(anchor="w")
        theme_var = tk.StringVar(value="Тёмная" if self.theme_name == "dark" else "Светлая")
        theme_combo = ttk.Combobox(frame, textvariable=theme_var, values=("Светлая", "Тёмная"),
                                   state="readonly")
        theme_combo.pack(fill="x", pady=(5, 10))
        ttk.Label(frame, text="Ник не является регистрацией. Заметки хранятся локально.",
                  style="Muted.TLabel", wraplength=420).pack(anchor="w", pady=(0, 8))

        def close() -> None:
            self.profile_editor_open = False
            self.profile_group_combo = None
            self.profile_group_locked = False
            dialog.destroy()
            if parent is not None and parent.winfo_exists():
                parent.grab_set()

        def save() -> None:
            if self.busy:
                return
            nickname = nickname_var.get().strip()
            new_group = group_var.get().strip()
            theme = "dark" if theme_var.get() == "Тёмная" else "light"
            try:
                self.repository.save_profile_settings(nickname, new_group, theme)
            except (DatabaseUnavailable, ValueError) as exc:
                messagebox.showerror("Профиль", str(exc), parent=dialog)
                return
            old_group = self.group_var.get()
            close()
            self._apply_theme(theme)
            self.status_var.set("Локальный профиль сохранён.")
            if new_group != old_group:
                self.group_var.set(new_group)
                self._on_group_selected(persist=False)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(5, 0))
        ttk.Button(buttons, text="Сохранить", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(buttons, text="Позже" if first_run else "Закрыть", command=close).pack(side="right")
        if not first_run and parent is None:
            ttk.Button(frame, text="Очистить кэш выбранной группы…",
                       command=lambda: self.clear_group_cache(parent=dialog)).pack(anchor="w", pady=(12, 0))
        dialog.protocol("WM_DELETE_WINDOW", close)
        nickname_entry.focus_set()

    def _nickname_for_note(self, parent) -> str | None:
        try:
            nickname = self.repository.local_nickname().strip()
        except DatabaseUnavailable as exc:
            messagebox.showerror("Профиль", str(exc), parent=parent)
            return None
        if not nickname:
            messagebox.showwarning("Настройте профиль", "Сначала укажите ник в профиле. "
                                   "Черновик заметки останется открытым.", parent=parent)
            self.open_profile(parent=parent)
            return None
        return nickname

    def open_group_search(self, parent=None, on_choose=None) -> None:
        """Поиск групп в профиле с доступом к локальному и сетевому справочнику."""
        if self.busy or self.group_search_open:
            return
        dialog = tk.Toplevel(self.root)
        _apply_window_icon(dialog)
        dialog.title("Поиск учебной группы")
        dialog.geometry("460x480")
        dialog.minsize(380, 340)
        dialog.transient(parent or self.root)
        dialog.grab_set()
        self.group_search_open = True
        self.group_search_dialog = dialog
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Название группы").pack(anchor="w")
        query = tk.StringVar()
        search = ttk.Entry(frame, textvariable=query)
        search.pack(fill="x", pady=(5, 8))

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True)
        p = self.palette
        group_list = tk.Listbox(list_frame, exportselection=False, font=("Segoe UI", 10),
                                bg=p["surface"], fg=p["text"], selectbackground=p["selected"],
                                selectforeground=p["text"], highlightthickness=0, relief="flat")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=group_list.yview)
        group_list.configure(yscrollcommand=scrollbar.set)
        group_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        hint = tk.StringVar()
        ttk.Label(frame, textvariable=hint, style="Muted.TLabel", wraplength=400).pack(
            anchor="w", pady=(7, 0)
        )

        def render(*_args) -> None:
            groups = {name.casefold(): name for name in self.group_values}
            groups.update({group.name.casefold(): group.name for group in self.remote_groups.values()})
            names = sorted(groups.values(), key=str.casefold)
            text = query.get().strip().casefold()
            matches = [name for name in names if text in name.casefold()]
            group_list.delete(0, "end")
            for name in matches[:300]:
                group_list.insert("end", name)
            if len(matches) > 300:
                hint.set(f"Показаны первые 300 из {len(matches)} групп. Уточните поиск.")
            elif not self.remote_groups:
                hint.set("Показаны локальные группы. Для полного списка нажмите «Загрузить группы».")
            else:
                hint.set(f"Найдено групп: {len(matches)}.")

        def close_dialog() -> None:
            self.group_search_open = False
            self.group_search_dialog = None
            self.group_search_refresh = None
            self.group_search_load_button = None
            dialog.destroy()
            if parent is not None and parent.winfo_exists():
                parent.grab_set()

        def choose(_event=None) -> None:
            selected = group_list.curselection()
            if not selected:
                if group_list.size() == 1:
                    selected = (0,)
                else:
                    return
            chosen = group_list.get(selected[0])
            if chosen not in self.group_values:
                self.group_values.append(chosen)
            if self.profile_group_combo is not None:
                self.profile_group_combo.configure(values=self.group_values)
            close_dialog()
            if on_choose is not None:
                on_choose(chosen)
            elif chosen != self.group_var.get():
                self.group_var.set(chosen)
                self._on_group_selected()

        query.trace_add("write", render)
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        group_list.bind("<Double-Button-1>", choose)
        search.bind("<Return>", choose)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Выбрать", command=choose).pack(side="left")
        self.group_search_load_button = ttk.Button(buttons, text="Загрузить группы",
                                                    command=self.load_remote_groups)
        self.group_search_load_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Отмена", command=close_dialog).pack(side="right")
        self.group_search_refresh = render
        render()
        search.focus_set()

    def load_remote_groups(self) -> None:
        if self.busy:
            return
        self._set_busy(True, "Загружаю список групп с сайта ДГТУ…")

        def worker() -> None:
            try:
                groups = self.schedule_api.fetch_groups()
                self.root.after(0, lambda: self._remote_groups_loaded(groups))
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._remote_groups_failed(error))

        threading.Thread(target=worker, daemon=True).start()

    def _remote_groups_loaded(self, groups: list[Group]) -> None:
        self.remote_groups = {group.name.casefold(): group for group in groups}
        local_groups = []
        if self.storage_ready:
            try:
                local_groups = self.repository.list_groups()
            except DatabaseUnavailable:
                pass
        current = self.group_var.get().strip()
        candidates = [*([current] if current else []), *[group.name for group in groups], *local_groups]
        values = []
        seen = set()
        for name in candidates:
            if name.casefold() not in seen:
                seen.add(name.casefold())
                values.append(name)
        self.group_values = values
        if self.profile_group_combo is not None:
            self.profile_group_combo.configure(values=values)
        if self.group_search_refresh is not None:
            self.group_search_refresh()
        self._set_busy(False, f"Загружен список групп: {len(groups)}.")

    def _remote_groups_failed(self, error: Exception) -> None:
        self._set_busy(False, "Не удалось загрузить список групп. Локальный кэш не изменён.")
        parent = self.group_search_dialog
        if parent is None and self.profile_group_combo is not None:
            parent = self.profile_group_combo.winfo_toplevel()
        messagebox.showwarning("Список групп", str(error), parent=parent or self.root)

    def synchronize_schedule(self, silent: bool = False) -> None:
        if self.busy:
            return
        if self.note_editor_open or self.group_search_open or self.profile_editor_open:
            if silent:
                self.root.after(1000, lambda: self.synchronize_schedule(silent=True))
            return
        if not self.storage_ready:
            messagebox.showerror(
                "PostgreSQL",
                "Сначала настройте PostgreSQL командой python setup_database.py и перезапустите приложение.",
                parent=self.root,
            )
            return

        group_name = self.group_var.get().strip()
        if not group_name:
            messagebox.showwarning("Группа", "Выберите учебную группу.", parent=self.root)
            return
        requested_month = self.month_filter_var.get()
        self._set_busy(True, f"Запрашиваю расписание группы {group_name}…")

        def worker() -> None:
            groups: list[Group] = []
            all_lessons = []
            lessons = []
            previous_snapshot_id = None
            try:
                groups = self.schedule_api.fetch_groups()
                group = next(
                    (item for item in groups if item.name.casefold() == group_name.casefold()),
                    None,
                )
                if group is None:
                    raise ScheduleApiError(
                        f"Группа «{group_name}» не найдена в справочнике ДГТУ. "
                        "Загрузите список групп и выберите нужную."
                    )
                all_lessons = self.schedule_api.fetch_schedule(group.remote_id)
                lessons = filter_schedule_month(all_lessons, requested_month)
                if not lessons:
                    raise ScheduleApiError(
                        f"В API нет занятий группы {group_name} за {requested_month}. "
                        "Сохранённый кэш не заменён."
                    )
                previous_snapshot_id = self.repository.current_snapshot_id(group_name)
                snapshot_id = self.repository.save_schedule(group_name, lessons, full_response=all_lessons)
                result = (
                    groups, all_lessons, lessons, snapshot_id, requested_month, None,
                    previous_snapshot_id,
                )
            except SuspiciousScheduleUpdate as exc:
                result = (groups, all_lessons, lessons, None, requested_month, exc, previous_snapshot_id)
            except Exception as exc:
                try:
                    self.repository.record_sync_failure(group_name, str(exc))
                except Exception:
                    pass
                result = (groups, all_lessons, [], None, requested_month, exc, previous_snapshot_id)
            self.root.after(
                0,
                lambda value=result, should_be_silent=silent: self._synchronization_finished(
                    group_name, value, should_be_silent
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _synchronization_finished(
        self, group_name: str, result: tuple, silent: bool = False
    ) -> None:
        groups, all_lessons, lessons, snapshot_id, requested_month, error, previous_snapshot_id = result
        self._set_busy(False)
        if groups:
            self.remote_groups = {group.name.casefold(): group for group in groups}
        if all_lessons:
            months = sorted({lesson.lesson_date.strftime("%Y-%m") for lesson in all_lessons})
            self.api_months[group_name.casefold()] = months
        if isinstance(error, SuspiciousScheduleUpdate):
            self._load_schedule()
            self.status_var.set(
                f"Возможен неполный ответ: получено {error.new_count} из "
                f"{error.old_count} занятий. Кэш сохранён; проверьте обновление вручную."
            )
            if not silent and messagebox.askyesno(
                "Проверить обновление расписания",
                (
                    f"Сайт вернул {error.new_count} занятий вместо {error.old_count} "
                    "в сохранённой версии за тот же месяц. Это может быть неполный ответ "
                    "или реальное изменение расписания.\n\n"
                    "Заменить сохранённое расписание полученной версией?"
                ),
                parent=self.root,
            ):
                self._save_confirmed_schedule(group_name, groups, all_lessons, lessons, requested_month)
            return
        if error is not None:
            self._load_schedule()
            self.status_var.set(
                f"Синхронизация за {requested_month} не выполнена: {error}. "
                "Последний сохранённый кэш не изменён."
            )
            if not silent:
                messagebox.showwarning("Синхронизация расписания", str(error), parent=self.root)
            return

        self._load_schedule()
        current = self.group_var.get()
        if current.casefold() != group_name.casefold():
            self.group_var.set(group_name)
            self._load_schedule()
        self._show_sync_result(group_name, requested_month, len(lessons), snapshot_id, previous_snapshot_id)

    def _show_sync_result(
        self, group_name: str, requested_month: str, lesson_count: int,
        snapshot_id: int, previous_snapshot_id: int | None,
    ) -> None:
        if snapshot_id == previous_snapshot_id:
            self.status_var.set(f"{requested_month}: обновление проверено, изменений нет.")
            return
        if previous_snapshot_id is not None:
            try:
                versions = self.repository.latest_schedule_versions(group_name)
            except DatabaseUnavailable:
                versions = []
            if (
                len(versions) >= 2
                and versions[0].snapshot_id == snapshot_id
                and versions[1].snapshot_id == previous_snapshot_id
            ):
                changes = compare_schedules(versions[1].entries, versions[0].entries)
                if changes:
                    counts = Counter(change.kind for change in changes)
                    self.status_var.set(
                        f"Расписание изменилось: добавлено {counts['added']}, "
                        f"отменено {counts['cancelled']}, изменено {counts['changed']}. "
                        "Подробности — в аналитике."
                    )
                    return
        self.status_var.set(f"Расписание за {requested_month} загружено: {lesson_count} занятий.")

    def _save_confirmed_schedule(
        self, group_name: str, groups: list[Group], all_lessons: list, lessons: list,
        requested_month: str,
    ) -> None:
        """Сохраняет именно тот подозрительный ответ, который подтвердил пользователь."""
        self._set_busy(True, "Сохраняю подтверждённое расписание…")

        def worker() -> None:
            previous_snapshot_id = None
            try:
                previous_snapshot_id = self.repository.current_snapshot_id(group_name)
                snapshot_id = self.repository.save_schedule(
                    group_name, lessons, allow_large_decrease=True, full_response=all_lessons
                )
                result = (
                    groups, all_lessons, lessons, snapshot_id, requested_month, None,
                    previous_snapshot_id,
                )
            except Exception as exc:
                try:
                    self.repository.record_sync_failure(group_name, str(exc))
                except Exception:
                    pass
                result = (groups, all_lessons, [], None, requested_month, exc, previous_snapshot_id)
            self.root.after(0, lambda: self._synchronization_finished(group_name, result))

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.sync_button.configure(state=state)
        self.detached_notes_button.configure(state=state)
        self.profile_button.configure(state=state)
        self.previous_week_button.configure(state=state)
        self.next_week_button.configure(state=state)
        for button in self.week_day_buttons:
            button.configure(state=state)
        if self.profile_group_combo is not None:
            combo_state = "disabled" if busy or self.profile_group_locked else "readonly"
            self.profile_group_combo.configure(state=combo_state)
        if self.group_search_load_button is not None:
            self.group_search_load_button.configure(state=state)
        if status:
            self.status_var.set(status)

    def open_note_editor(self, entry: ScheduleEntry | None = None) -> None:
        if self.busy:
            return
        if entry is None:
            messagebox.showinfo("Заметка", "Сначала выберите занятие в расписании.", parent=self.root)
            return
        if not self.storage_ready:
            messagebox.showerror("PostgreSQL", "Нет подключения к базе данных.", parent=self.root)
            return

        p = self.palette
        dialog = tk.Toplevel(self.root)
        _apply_window_icon(dialog)
        dialog.title("Заметка к занятию")
        dialog.geometry("560x500")
        dialog.minsize(480, 360)
        dialog.transient(self.root)
        dialog.grab_set()
        self.note_editor_open = True

        def close_dialog(force: bool = False) -> None:
            if not force and note_text.get("1.0", "end-1c").strip() != entry.note_text.strip():
                if not messagebox.askyesno(
                    "Несохранённые изменения",
                    "Отбросить изменения текста заметки?",
                    parent=dialog,
                ):
                    return
            self.note_editor_open = False
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

        header = tk.Frame(dialog, bg=p["navy"], padx=20, pady=14)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Заметка к занятию",
            bg=p["navy"],
            fg="white",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text=(
                f"{entry.subject} · {entry.lesson_date:%d.%m.%Y} · "
                f"{entry.start_time:%H:%M}–{entry.end_time:%H:%M} · ауд. {entry.room or '—'}"
            ),
            bg=p["navy"],
            fg=p["header_text"],
            font=("Segoe UI", 9),
            wraplength=510,
            justify="left",
        ).pack(anchor="w", pady=(5, 0))

        content = ttk.Frame(dialog, padding=20)
        content.pack(fill="both", expand=True)
        if entry.note_id is not None:
            ttk.Label(content, text=f"Автор: {entry.author_name or 'не указан'}",
                      style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(content, text="Текст заметки").pack(anchor="w", pady=(0, 7))
        note_text = tk.Text(
            content,
            height=10,
            wrap="word",
            font=("Segoe UI", 11),
            bg=p["surface"],
            fg=p["text"],
            insertbackground=p["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=p["border"],
            highlightcolor=p["blue"],
            padx=10,
            pady=10,
        )
        note_text.pack(fill="both", expand=True)
        if entry.note_text:
            note_text.insert("1.0", entry.note_text)
        note_text.focus_set()

        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(12, 0))

        def save() -> None:
            value = note_text.get("1.0", "end-1c").strip()
            if not value:
                messagebox.showwarning(
                    "Пустая заметка",
                    "Введите текст заметки перед сохранением.",
                    parent=dialog,
                )
                return
            author = self._nickname_for_note(dialog)
            if not author:
                return
            try:
                self.repository.save_note(entry.lesson_id, value, author)
            except (DatabaseUnavailable, ValueError) as exc:
                messagebox.showerror("Не удалось сохранить заметку", str(exc), parent=dialog)
                return
            close_dialog(force=True)
            self._load_schedule()
            self.status_var.set("Заметка сохранена в PostgreSQL.")

        ttk.Button(buttons, text="Сохранить", style="Accent.TButton", command=save).pack(side="left")

        def delete() -> None:
            if entry.note_id is None:
                return
            if not messagebox.askyesno(
                "Удалить заметку?",
                "Заметка будет удалена из базы данных.",
                parent=dialog,
            ):
                return
            try:
                deleted = self.repository.delete_note(entry.note_id)
            except (DatabaseUnavailable, ValueError) as exc:
                messagebox.showerror("Не удалось удалить заметку", str(exc), parent=dialog)
                return
            if not deleted:
                messagebox.showwarning(
                    "Заметка изменилась", "Заметка уже удалена. Обновите расписание.", parent=dialog
                )
                return
            close_dialog(force=True)
            self._load_schedule()
            self.status_var.set("Заметка удалена.")

        delete_state = "normal" if entry.note_id is not None else "disabled"
        ttk.Button(buttons, text="Удалить", command=delete, state=delete_state).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(buttons, text="Отмена", command=close_dialog).pack(side="right")

    def open_detached_notes(self) -> None:
        """Открывает сохранённые заметки без привязки к текущему снимку."""
        if self.busy:
            return
        if not self.storage_ready:
            messagebox.showerror("PostgreSQL", "Нет подключения к базе данных.", parent=self.root)
            return
        try:
            notes = self.repository.detached_notes(self.group_var.get())
        except DatabaseUnavailable as exc:
            messagebox.showerror("Сохранённые заметки", str(exc), parent=self.root)
            return
        if not notes:
            messagebox.showinfo(
                "Сохранённые заметки", "Других сохранённых заметок нет.", parent=self.root
            )
            return
        dialog = tk.Toplevel(self.root)
        _apply_window_icon(dialog)
        dialog.title("Другие сохранённые заметки")
        dialog.geometry("700x440")
        dialog.transient(self.root)
        dialog.grab_set()
        self.note_editor_open = True
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Заметки других месяцев и отсутствующих занятий",
            style="Heading.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        tree = ttk.Treeview(frame, columns=("date", "time", "subject"), show="headings")
        for name, title, width in (("date", "Дата", 100), ("time", "Время", 80),
                                   ("subject", "Дисциплина", 400)):
            tree.heading(name, text=title)
            tree.column(name, width=width)
        tree.pack(fill="both", expand=True)
        author_var = tk.StringVar(value="Автор: —")
        ttk.Label(frame, textvariable=author_var).pack(anchor="w", pady=(4, 0))
        p = self.palette
        text_box = tk.Text(frame, height=5, wrap="word", state="disabled", bg=p["surface"],
                           fg=p["text"], insertbackground=p["text"], relief="flat")
        text_box.pack(fill="x", pady=(8, 0))
        entries = {}
        for note in notes:
            iid = tree.insert("", "end", values=(
                note.lesson_date.strftime("%d.%m.%Y"),
                note.start_time.strftime("%H:%M"), note.subject,
            ))
            entries[iid] = note

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        selected_iid: str | None = None

        def is_dirty() -> bool:
            return bool(
                selected_iid in entries
                and text_box.get("1.0", "end-1c").strip() != entries[selected_iid].note_text
            )

        def discard_draft() -> bool:
            return not is_dirty() or messagebox.askyesno(
                "Несохранённые изменения",
                "Отбросить изменения текста заметки?",
                parent=dialog,
            )

        def close_dialog() -> None:
            if not discard_draft():
                return
            self.note_editor_open = False
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

        def show_note(_event=None) -> None:
            nonlocal selected_iid
            selected = tree.selection()
            new_iid = selected[0] if selected else None
            if new_iid == selected_iid:
                return
            if not discard_draft():
                if selected_iid:
                    tree.selection_set(selected_iid)
                return
            selected_iid = new_iid
            text_box.configure(state="normal")
            text_box.delete("1.0", "end")
            if new_iid in entries:
                text_box.insert("1.0", entries[new_iid].note_text)
                author_var.set(f"Автор: {entries[new_iid].author_name or 'не указан'}")
                save_button.configure(state="normal")
                delete_button.configure(state="normal")
            else:
                author_var.set("Автор: —")
                text_box.configure(state="disabled")
                save_button.configure(state="disabled")
                delete_button.configure(state="disabled")

        def save() -> None:
            if selected_iid not in entries:
                return
            value = text_box.get("1.0", "end-1c").strip()
            author = self._nickname_for_note(dialog)
            if not author:
                return
            try:
                updated = self.repository.save_detached_note(entries[selected_iid].note_id, value, author)
            except (DatabaseUnavailable, ValueError) as exc:
                messagebox.showerror("Не удалось сохранить заметку", str(exc), parent=dialog)
                return
            if not updated:
                messagebox.showwarning(
                    "Заметка изменилась",
                    "Заметка уже перенесена в расписание или удалена. Откройте список заново.",
                    parent=dialog,
                )
                return
            entries[selected_iid] = replace(entries[selected_iid], note_text=value, author_name=author)
            author_var.set(f"Автор: {author}")
            self.status_var.set("Сохранённая заметка изменена.")

        def delete() -> None:
            nonlocal selected_iid
            if selected_iid not in entries:
                return
            if not messagebox.askyesno(
                "Удалить заметку?", "Сохранённая заметка будет удалена из базы.", parent=dialog
            ):
                return
            try:
                deleted = self.repository.delete_detached_note(entries[selected_iid].note_id)
            except DatabaseUnavailable as exc:
                messagebox.showerror("Не удалось удалить заметку", str(exc), parent=dialog)
                return
            if not deleted:
                messagebox.showwarning(
                    "Заметка изменилась",
                    "Заметка уже перенесена в расписание или удалена. Откройте список заново.",
                    parent=dialog,
                )
                return
            row = selected_iid
            selected_iid = None
            del entries[row]
            tree.delete(row)
            text_box.configure(state="normal")
            text_box.delete("1.0", "end")
            text_box.configure(state="disabled")
            save_button.configure(state="disabled")
            delete_button.configure(state="disabled")
            self.status_var.set("Сохранённая заметка удалена.")

        save_button = ttk.Button(buttons, text="Сохранить", command=save, state="disabled")
        save_button.pack(side="left")
        delete_button = ttk.Button(buttons, text="Удалить", command=delete, state="disabled")
        delete_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="Закрыть", command=close_dialog).pack(side="right")

        tree.bind("<<TreeviewSelect>>", show_note)

    def open_analysis(self) -> None:
        """Показывает сводку по периоду и разницу последних версий расписания."""
        if not self.storage_ready:
            messagebox.showerror("PostgreSQL", "Нет подключения к базе данных.", parent=self.root)
            return
        if not self._has_month_cache(self.month_filter_var.get()):
            messagebox.showinfo(
                "Аналитика расписания",
                "Для выбранного месяца нет сохранённого расписания. Сначала загрузите его.",
                parent=self.root,
            )
            return
        try:
            versions = self.repository.latest_schedule_versions(self.group_var.get())
            boundary_entries, boundary_coverage = self.repository.boundary_schedule(self.group_var.get())
        except DatabaseUnavailable as exc:
            messagebox.showerror("Аналитика", str(exc), parent=self.root)
            return
        if not versions:
            messagebox.showinfo(
                "Аналитика расписания",
                "Сначала загрузите расписание выбранной группы.",
                parent=self.root,
            )
            return

        current = versions[0]
        previous = versions[1] if len(versions) > 1 else None
        p = self.palette
        dialog = tk.Toplevel(self.root)
        _apply_window_icon(dialog)
        dialog.title("Аналитика расписания")
        dialog.geometry("900x590")
        dialog.minsize(760, 500)
        dialog.transient(self.root)

        header = tk.Frame(dialog, bg=p["navy"], padx=20, pady=14)
        header.pack(fill="x")
        tk.Label(
            header,
            text=f"Расписание группы {self.group_var.get()} — аналитика",
            bg=p["navy"],
            fg="white",
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")
        previous_text = (
            f"Предыдущий снимок: {previous.downloaded_at:%d.%m.%Y %H:%M}"
            if previous
            else "Предыдущего снимка пока нет"
        )
        tk.Label(
            header,
            text=(
                f"Текущий снимок: {current.downloaded_at:%d.%m.%Y %H:%M} · "
                f"{previous_text}"
            ),
            bg=p["navy"],
            fg=p["header_text"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 0))

        content = ttk.Frame(dialog, padding=18)
        content.pack(fill="both", expand=True)
        controls = ttk.Frame(content)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Период").pack(side="left", padx=(0, 8))
        period_var = tk.StringVar(value="Месяц")
        period_combo = ttk.Combobox(
            controls,
            textvariable=period_var,
            values=("День", "Неделя", "Месяц"),
            state="readonly",
            width=12,
        )
        period_combo.pack(side="left")
        anchor_day = self._analysis_anchor_date()
        ttk.Label(
            controls,
            text=f"Дата отсчёта: {anchor_day:%d.%m.%Y}",
            style="Muted.TLabel",
        ).pack(side="left", padx=14)
        online_button = ttk.Button(
            controls,
            text="Проверить неделю на сайте",
            command=lambda: load_week_online(),
            state="disabled",
        )
        online_button.pack(side="right")

        summary_var = tk.StringVar()
        ttk.Label(
            content,
            textvariable=summary_var,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(2, 12))
        online_status = tk.StringVar(value="")
        ttk.Label(content, textvariable=online_status, style="Muted.TLabel", wraplength=840).pack(
            anchor="w", pady=(0, 8)
        )
        group_name = self.group_var.get().strip()
        online_lessons = None
        online_busy = False

        changes_header = ttk.Frame(content)
        changes_header.pack(fill="x", pady=(2, 8))
        ttk.Label(changes_header, text="Изменения после синхронизации",
                  style="Heading.TLabel").pack(side="left")
        change_filter = ttk.Combobox(changes_header, values=("Все", "Добавлено", "Отменено", "Изменено"),
                                     state="readonly", width=14)
        change_filter.set("Все")
        change_filter.pack(side="right")
        changes_frame = ttk.Frame(content)
        changes_frame.pack(fill="both", expand=True)
        columns = ("kind", "date", "time", "subject", "details")
        changes_tree = ttk.Treeview(changes_frame, columns=columns, show="headings")
        for kind in ("added", "cancelled", "changed"):
            changes_tree.tag_configure(kind, background=p[kind + "_bg"], foreground=p[kind + "_fg"])
        headers = {
            "kind": ("Событие", 105),
            "date": ("Дата", 100),
            "time": ("Время", 110),
            "subject": ("Дисциплина", 220),
            "details": ("Изменение", 320),
        }
        for column, (label, width) in headers.items():
            changes_tree.heading(column, text=label)
            changes_tree.column(column, width=width, minwidth=70, anchor="w")
        scrollbar = ttk.Scrollbar(changes_frame, orient="vertical", command=changes_tree.yview)
        changes_tree.configure(yscrollcommand=scrollbar.set)
        changes_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def render_period(_event: object = None) -> None:
            period = {"День": "day", "Неделя": "week", "Месяц": "month"}[period_var.get()]
            start, end = period_bounds(period, anchor_day)
            crosses_month = start.strftime("%Y-%m") != end.strftime("%Y-%m")
            use_online = period == "week" and crosses_month and online_lessons is not None
            cached_entries = current.entries + boundary_entries if period == "week" else current.entries
            summary = summarize_schedule(online_lessons if use_online else cached_entries, period, anchor_day)
            online_button.configure(
                state="normal" if period == "week" and crosses_month and not online_busy else "disabled"
            )
            hours, minutes = divmod(summary.total_minutes, 60)
            if summary.start_date == summary.end_date:
                range_text = summary.start_date.strftime("%d.%m.%Y")
            else:
                range_text = (
                    f"{summary.start_date:%d.%m.%Y} – {summary.end_date:%d.%m.%Y}"
                )
            summary_var.set(
                f"{range_text}: {summary.lesson_count} пар · "
                f"{hours} ч {minutes:02d} мин"
                + (
                    " (по данным сайта)" if use_online
                    else " (локальный кэш, включая соседние дни)"
                    if period == "week" and crosses_month and boundary_coverage
                    else " (только сохранённый месяц)"
                    if period == "week" and crosses_month
                    else ""
                )
            )
            if use_online:
                online_status.set(
                    "Временный расчёт по ответу сайта; полнота зависит от опубликованного периода. "
                    "Офлайн-кэш не изменён."
                )
            elif period == "week" and crosses_month and boundary_coverage and not online_busy:
                coverage_start, coverage_end = boundary_coverage
                if coverage_start <= start and coverage_end >= end:
                    online_status.set("Учтены сохранённые соседние дни; полнота зависит от опубликованного сайтом расписания.")
                else:
                    online_status.set("Ответ сайта не покрывал весь диапазон недели: сводка может быть неполной.")
            elif period == "week" and crosses_month and not online_busy:
                online_status.set("Соседние дни ещё не сохранены. Синхронизируйте расписание при подключении к сети.")
            elif not online_busy:
                online_status.set("")

        def load_week_online() -> None:
            nonlocal online_busy
            if online_busy:
                return
            online_busy = True
            online_button.configure(state="disabled")
            online_status.set("Загружаю данные сайта для расчёта недели…")

            def worker() -> None:
                try:
                    groups = self.schedule_api.fetch_groups()
                    group = next(
                        (item for item in groups if item.name.casefold() == group_name.casefold()),
                        None,
                    )
                    if group is None:
                        raise ScheduleApiError("Группа не найдена в справочнике ДГТУ.")
                    lessons = self.schedule_api.fetch_schedule(group.remote_id)
                    unique = {
                        (lesson.lesson_date, lesson.start_time, lesson.end_time,
                         lesson.subject, lesson.teacher, lesson.room): lesson
                        for lesson in lessons
                    }
                    result = (list(unique.values()), None)
                except Exception as exc:
                    result = (None, exc)

                def finish() -> None:
                    nonlocal online_busy, online_lessons
                    if not dialog.winfo_exists():
                        return
                    online_busy = False
                    online_lessons, error = result
                    render_period()
                    if error is not None:
                        online_status.set(
                            "Не удалось получить данные сайта. Остаётся расчёт по локальному кэшу."
                        )

                try:
                    self.root.after(0, finish)
                except (RuntimeError, tk.TclError):
                    pass

            threading.Thread(target=worker, daemon=True).start()

        def render_changes(_event: object = None) -> None:
            changes_tree.delete(*changes_tree.get_children())
            if previous is None:
                return
            changes = compare_schedules(previous.entries, current.entries)
            titles = {"added": "Добавлено", "cancelled": "Отменено", "changed": "Изменено"}
            for change in changes:
                title = titles[change.kind]
                if change_filter.get() in ("Все", title):
                    self._insert_change_row(changes_tree, change, title, change_details(change))

        period_combo.bind("<<ComboboxSelected>>", render_period)
        change_filter.bind("<<ComboboxSelected>>", render_changes)
        render_period()
        render_changes()
        if previous is None:
            ttk.Label(
                content,
                text="После следующей синхронизации здесь появится сравнение двух снимков.",
                style="Muted.TLabel",
            ).pack(anchor="w", pady=(8, 0))
        elif not compare_schedules(previous.entries, current.entries):
            ttk.Label(
                content,
                text="Между последними снимками изменений нет.",
                style="Muted.TLabel",
            ).pack(anchor="w", pady=(8, 0))

        ttk.Button(content, text="Закрыть", command=dialog.destroy).pack(
            anchor="e", pady=(12, 0)
        )

    def clear_group_cache(self, parent=None) -> None:
        """Удаляет выбранную локальную копию расписания с подтверждением."""
        group_name = self.group_var.get().strip()
        if not group_name:
            messagebox.showwarning("Группа", "Сначала выберите учебную группу.", parent=parent or self.root)
            return
        confirmed = messagebox.askyesno(
            "Очистить кэш группы?",
            (
                f"Удалить сохранённые снимки расписания, занятия и заметки группы "
                f"{group_name}? Саму группу и журнал синхронизаций это не удалит."
            ),
            parent=parent or self.root,
        )
        if not confirmed:
            return
        try:
            deleted_snapshots = self.repository.clear_group_cache(group_name)
        except (DatabaseUnavailable, ValueError) as exc:
            messagebox.showerror("Не удалось очистить кэш", str(exc), parent=parent or self.root)
            return
        self._load_schedule()
        self.status_var.set(
            f"Кэш группы очищен: удалено снимков расписания — {deleted_snapshots}. "
            "Журнал синхронизаций сохранён."
        )

    def _analysis_anchor_date(self) -> date:
        """Аналитика начинается с той даты, которая показана на карточках."""
        return self._displayed_calendar_day()

    @staticmethod
    def _insert_change_row(
        tree: ttk.Treeview,
        change: ScheduleChange,
        label: str,
        details: str,
    ) -> None:
        entry = change.new or change.old
        if entry is None:
            return
        tree.insert(
            "",
            "end",
            tags=(change.kind,),
            values=(
                label,
                entry.lesson_date.strftime("%d.%m.%Y"),
                f"{entry.start_time:%H:%M}–{entry.end_time:%H:%M}",
                entry.subject,
                details,
            ),
        )


def main() -> None:
    _configure_windows_taskbar()
    root = tk.Tk()
    AlwaysDSTUApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
