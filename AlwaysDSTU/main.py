"""Минимальный каркас приложения Always DSTU для лабораторной работы № 5."""

import tkinter as tk
from tkinter import ttk


def main() -> None:
    root = tk.Tk()
    root.title("Always DSTU")
    root.geometry("760x420")
    root.minsize(600, 340)

    content = ttk.Frame(root, padding=32)
    content.pack(expand=True)

    ttk.Label(
        content,
        text=(
            "Always DSTU\n"
            "Офлайн-просмотр расписания занятий ДГТУ\n"
            "Разработчики: Дылевский, Мещеряков, Яблоновский, Таран"
        ),
        justify="center",
        anchor="center",
    ).pack(pady=(0, 24))

    # Кнопка-заглушка: действие будет добавлено на следующем этапе проекта.
    ttk.Button(content, text="Открыть расписание").pack()

    root.mainloop()


if __name__ == "__main__":
    main()
