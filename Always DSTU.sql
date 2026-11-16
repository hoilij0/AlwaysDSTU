
-- =========================================
-- База данных Always_DSTU
-- PostgreSQL
-- =========================================

-- 1. Таблица учебных групп
CREATE TABLE groups (
    group_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_name VARCHAR(50) NOT NULL UNIQUE
);

-- 2. Таблица снимков расписания
CREATE TABLE schedule_snapshots (
    snapshot_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_id INTEGER NOT NULL,
    downloaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,

    FOREIGN KEY (group_id)
        REFERENCES groups(group_id)
        ON DELETE CASCADE
);

-- Только один актуальный снимок для каждой группы
CREATE UNIQUE INDEX one_current_snapshot_per_group
ON schedule_snapshots(group_id)
WHERE is_current = TRUE;

-- 3. Таблица занятий
CREATE TABLE lessons (
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

-- 4. Таблица заметок
CREATE TABLE notes (
    note_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lesson_id INTEGER NOT NULL,
    note_text TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (lesson_id)
        REFERENCES lessons(lesson_id)
        ON DELETE CASCADE
);

-- 5. Журнал синхронизации
CREATE TABLE sync_log (
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
