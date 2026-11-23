
-- =========================================
-- База данных Always_DSTU
-- PostgreSQL
-- =========================================

-- 1. Таблица учебных групп
CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_name VARCHAR(50) NOT NULL UNIQUE
);

-- Локальные настройки настольного приложения
CREATE TABLE IF NOT EXISTS app_settings (
    setting_key VARCHAR(50) PRIMARY KEY,
    setting_value TEXT NOT NULL
);

-- 2. Таблица снимков расписания
CREATE TABLE IF NOT EXISTS schedule_snapshots (
    snapshot_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_id INTEGER NOT NULL,
    downloaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,

    FOREIGN KEY (group_id)
        REFERENCES groups(group_id)
        ON DELETE CASCADE
);

-- Только один актуальный снимок для каждой группы
CREATE UNIQUE INDEX IF NOT EXISTS one_current_snapshot_per_group
ON schedule_snapshots(group_id)
WHERE is_current = TRUE;

-- 3. Таблица занятий
CREATE TABLE IF NOT EXISTS lessons (
    lesson_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_id INTEGER NOT NULL,
    lesson_date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    subject VARCHAR(150) NOT NULL,
    teacher VARCHAR(100),
    room VARCHAR(30),

    FOREIGN KEY (snapshot_id)
        REFERENCES schedule_snapshots(snapshot_id)
        ON DELETE CASCADE,

    CHECK (end_time > start_time)
);

-- Не сохранять одну и ту же пару дважды в одном снимке
CREATE UNIQUE INDEX IF NOT EXISTS one_lesson_per_snapshot
ON lessons (
    snapshot_id,
    lesson_date,
    start_time,
    end_time,
    subject,
    (COALESCE(teacher, '')),
    (COALESCE(room, ''))
);

-- 4. Таблица заметок
CREATE TABLE IF NOT EXISTS notes (
    note_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lesson_id INTEGER NOT NULL,
    note_text TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (lesson_id)
        REFERENCES lessons(lesson_id)
        ON DELETE CASCADE
);

-- Для одной пары хранится одна актуальная заметка
CREATE UNIQUE INDEX IF NOT EXISTS one_note_per_lesson
ON notes(lesson_id);

-- Старые заметки сохраняются без вымышленной подписи.
ALTER TABLE notes ADD COLUMN IF NOT EXISTS author_name TEXT NOT NULL DEFAULT '';

-- Заметки, временно не привязанные к актуальному снимку расписания
CREATE TABLE IF NOT EXISTS detached_notes (
    detached_note_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    lesson_date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    subject VARCHAR(150) NOT NULL,
    teacher VARCHAR(100) NOT NULL DEFAULT '',
    room VARCHAR(30) NOT NULL DEFAULT '',
    note_text TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    matching_unique BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (group_id, lesson_date, start_time, end_time, subject, teacher, room)
);

ALTER TABLE detached_notes
ADD COLUMN IF NOT EXISTS matching_unique BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE detached_notes ADD COLUMN IF NOT EXISTS author_name TEXT NOT NULL DEFAULT '';

-- Только дни соседних месяцев, необходимые для граничных недель.
CREATE TABLE IF NOT EXISTS boundary_lessons (
    snapshot_id INTEGER NOT NULL REFERENCES schedule_snapshots(snapshot_id) ON DELETE CASCADE,
    lesson_date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    subject VARCHAR(150) NOT NULL,
    teacher VARCHAR(100) NOT NULL DEFAULT '',
    room VARCHAR(30) NOT NULL DEFAULT '',
    PRIMARY KEY (snapshot_id, lesson_date, start_time, end_time, subject, teacher, room),
    CHECK (end_time > start_time)
);

CREATE TABLE IF NOT EXISTS boundary_cache_state (
    snapshot_id INTEGER PRIMARY KEY REFERENCES schedule_snapshots(snapshot_id) ON DELETE CASCADE,
    response_first_date DATE NOT NULL,
    response_last_date DATE NOT NULL
);

-- 5. Журнал синхронизации
CREATE TABLE IF NOT EXISTS sync_log (
    sync_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_id INTEGER NOT NULL,
    snapshot_id INTEGER,
    sync_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL,
    details TEXT,

    FOREIGN KEY (group_id)
        REFERENCES groups(group_id)
        ON DELETE CASCADE,

    FOREIGN KEY (snapshot_id)
        REFERENCES schedule_snapshots(snapshot_id)
        ON DELETE SET NULL,

    CHECK (status IN ('Успешно', 'Ошибка'))
);
