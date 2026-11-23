"""Проверка запуска собранного EXE в отдельной тестовой схеме PostgreSQL."""

import ctypes
import os
import re
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from database import PostgresRepository


def child_process_id(parent_id):
    """Находит дочерний процесс Python без WMI, который может быть запрещён."""
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("Не удалось получить список процессов.")
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            if entry.parent == parent_id and entry.name.casefold().startswith("python"):
                return entry.pid
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return None
    finally:
        kernel.CloseHandle(snapshot)


@unittest.skipUnless(
    os.name == "nt" and os.environ.get("ALWAYS_DSTU_TEST_LAUNCHER") == "1"
    and os.environ.get("ALWAYS_DSTU_TEST_DB") == "1",
    "Windows launcher and PostgreSQL tests must be explicitly enabled",
)
class WindowsLauncherTests(unittest.TestCase):
    def test_exe_starts_window_with_isolated_postgres_schema(self):
        exe = Path(__file__).resolve().parents[1] / "Always DSTU.exe"
        self.assertTrue(exe.is_file(), "Сначала соберите Windows-запускатель.")
        repo = PostgresRepository()
        schema = "alwaysdstu_test_" + uuid4().hex
        self.assertRegex(schema, r"^alwaysdstu_test_[0-9a-f]{32}$")
        with repo._connect() as connection:
            from psycopg import sql
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        child_id = None
        child_handle = None
        launcher = None
        try:
            environment = os.environ.copy()
            environment["PGOPTIONS"] = (environment.get("PGOPTIONS", "") +
                                        f" -c search_path={schema}").strip()
            launcher = subprocess.Popen([str(exe)], env=environment, cwd=str(exe.parent))
            deadline = time.monotonic() + 20
            hwnd = None
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
            user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            while time.monotonic() < deadline:
                if child_id is None:
                    child_id = child_process_id(launcher.pid)
                    if child_id:
                        child_handle = kernel32.OpenProcess(0x00100001, False, child_id)
                        self.assertTrue(child_handle, "Не удалось открыть тестовый процесс.")
                if child_id:
                    # В некоторых окружениях venv запускает ещё один процесс интерпретатора.
                    window_process_id = child_process_id(child_id) or child_id
                    windows = []
                    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                    def collect(candidate, _):
                        window_pid = ctypes.c_ulong()
                        user32.GetWindowThreadProcessId(candidate, ctypes.byref(window_pid))
                        if window_pid.value == window_process_id:
                            title = ctypes.create_unicode_buffer(512)
                            user32.GetWindowTextW(candidate, title, len(title))
                            if title.value == "Always DSTU — расписание ДГТУ":
                                windows.append(candidate)
                        return True
                    user32.EnumWindows(callback_type(collect), 0)
                    if windows:
                        hwnd = windows[0]
                        with patch.dict(os.environ, {"PGOPTIONS": environment["PGOPTIONS"]}):
                            with repo._connect() as connection:
                                with connection.cursor() as cursor:
                                    cursor.execute("SELECT to_regclass('groups')")
                                    if cursor.fetchone()[0] is not None:
                                        break
                time.sleep(0.2)
            self.assertIsNotNone(child_id, "Запускатель не создал процесс Python.")
            self.assertIsNotNone(hwnd, "Окно приложения не появилось.")
            with patch.dict(os.environ, {"PGOPTIONS": environment["PGOPTIONS"]}):
                with repo._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT to_regclass('groups')")
                        self.assertIsNotNone(cursor.fetchone()[0], "Схема приложения не создана.")
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        finally:
            if child_id and child_handle:
                if kernel32.WaitForSingleObject(child_handle, 5000) == 0x102:
                    descendant = child_process_id(child_id)
                    descendant_handle = kernel32.OpenProcess(0x00100001, False, descendant) if descendant else None
                    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                    if descendant_handle:
                        kernel32.TerminateProcess(descendant_handle, 1)
                        kernel32.WaitForSingleObject(descendant_handle, 5000)
                        kernel32.CloseHandle(descendant_handle)
                    kernel32.TerminateProcess(child_handle, 1)
                    kernel32.WaitForSingleObject(child_handle, 5000)
                kernel32.CloseHandle(child_handle)
            elif child_id:
                raise RuntimeError("Тестовый процесс ещё может работать; схема оставлена для проверки.")
            if launcher is not None:
                launcher.wait(timeout=5)
            if not re.fullmatch(r"alwaysdstu_test_[0-9a-f]{32}", schema):
                raise AssertionError("Unexpected schema name")
            with repo._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    unittest.main()
