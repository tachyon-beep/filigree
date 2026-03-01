"""Database schema definitions for the filigree issue tracker.

Contains the canonical SQL schema, the legacy V1 schema (for migration tests),
and the current schema version constant.
"""

from __future__ import annotations

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS issues (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    priority    INTEGER NOT NULL DEFAULT 2,
    type        TEXT NOT NULL DEFAULT 'task',
    parent_id   TEXT REFERENCES issues(id) ON DELETE SET NULL,
    assignee    TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT,
    description TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    fields      TEXT DEFAULT '{}',

    CHECK (priority BETWEEN 0 AND 4)
);

CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
CREATE INDEX IF NOT EXISTS idx_issues_type ON issues(type);
CREATE INDEX IF NOT EXISTS idx_issues_parent ON issues(parent_id);
CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues(priority);
CREATE INDEX IF NOT EXISTS idx_issues_status_priority ON issues(status, priority, created_at);

CREATE TABLE IF NOT EXISTS dependencies (
    issue_id       TEXT NOT NULL REFERENCES issues(id),
    depends_on_id  TEXT NOT NULL REFERENCES issues(id),
    type           TEXT NOT NULL DEFAULT 'blocks',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (issue_id, depends_on_id)
);

CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON dependencies(depends_on_id);
CREATE INDEX IF NOT EXISTS idx_deps_issue_depends ON dependencies(issue_id, depends_on_id);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    event_type TEXT NOT NULL,
    actor      TEXT DEFAULT '',
    old_value  TEXT,
    new_value  TEXT,
    comment    TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_issue ON events(issue_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_issue_time ON events(issue_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup
  ON events(issue_id, event_type, actor,
    coalesce(old_value,''), coalesce(new_value,''), created_at);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    author     TEXT DEFAULT '',
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id, created_at);

CREATE TABLE IF NOT EXISTS labels (
    issue_id TEXT NOT NULL REFERENCES issues(id),
    label    TEXT NOT NULL,
    PRIMARY KEY (issue_id, label)
);

CREATE TABLE IF NOT EXISTS type_templates (
    type          TEXT PRIMARY KEY,
    pack          TEXT NOT NULL DEFAULT 'core',
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packs (
    name          TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT 1
);

-- FTS5 full-text search with sync triggers
CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING fts5(
    title, description, content='issues', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS issues_fts_insert AFTER INSERT ON issues BEGIN
    INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS issues_fts_update AFTER UPDATE OF title, description ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, description)
        VALUES('delete', old.rowid, old.title, old.description);
    INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS issues_fts_delete AFTER DELETE ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, description)
        VALUES('delete', old.rowid, old.title, old.description);
END;

-- ---- File records & scan findings (v2) -----------------------------------

CREATE TABLE IF NOT EXISTS file_records (
    id          TEXT PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    language    TEXT DEFAULT '',
    file_type   TEXT DEFAULT '',
    first_seen  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    metadata    TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_file_records_path ON file_records(path);
CREATE INDEX IF NOT EXISTS idx_file_records_language ON file_records(language);

CREATE TABLE IF NOT EXISTS scan_findings (
    id            TEXT PRIMARY KEY,
    file_id       TEXT NOT NULL REFERENCES file_records(id),
    issue_id      TEXT REFERENCES issues(id) ON DELETE SET NULL,
    scan_source   TEXT NOT NULL DEFAULT '',
    rule_id       TEXT DEFAULT '',
    severity      TEXT NOT NULL DEFAULT 'info',
    status        TEXT NOT NULL DEFAULT 'open',
    message       TEXT DEFAULT '',
    suggestion    TEXT DEFAULT '',
    scan_run_id   TEXT DEFAULT '',
    line_start    INTEGER,
    line_end      INTEGER,
    seen_count    INTEGER DEFAULT 1,
    first_seen    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_seen_at  TEXT,
    metadata      TEXT DEFAULT '{}',
    CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    CHECK (status IN ('open', 'acknowledged', 'fixed', 'false_positive', 'unseen_in_latest'))
);

CREATE INDEX IF NOT EXISTS idx_scan_findings_file ON scan_findings(file_id);
CREATE INDEX IF NOT EXISTS idx_scan_findings_issue ON scan_findings(issue_id);
CREATE INDEX IF NOT EXISTS idx_scan_findings_severity ON scan_findings(severity);
CREATE INDEX IF NOT EXISTS idx_scan_findings_status ON scan_findings(status);
CREATE INDEX IF NOT EXISTS idx_scan_findings_run ON scan_findings(scan_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_findings_dedup
  ON scan_findings(file_id, scan_source, rule_id, coalesce(line_start, -1));

CREATE TABLE IF NOT EXISTS file_associations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     TEXT NOT NULL REFERENCES file_records(id),
    issue_id    TEXT NOT NULL REFERENCES issues(id),
    assoc_type  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(file_id, issue_id, assoc_type),
    CHECK (assoc_type IN ('bug_in', 'task_for', 'scan_finding', 'mentioned_in'))
);

CREATE INDEX IF NOT EXISTS idx_file_assoc_file ON file_associations(file_id);
CREATE INDEX IF NOT EXISTS idx_file_assoc_issue ON file_associations(issue_id);

CREATE TABLE IF NOT EXISTS file_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     TEXT NOT NULL REFERENCES file_records(id),
    event_type  TEXT NOT NULL DEFAULT 'file_metadata_update',
    field       TEXT NOT NULL,
    old_value   TEXT DEFAULT '',
    new_value   TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_events_file ON file_events(file_id);
"""

# V1 schema (without file tables) — kept for migration tests.
# Defined as a standalone constant to avoid brittle string-split coupling.
SCHEMA_V1_SQL = """\
CREATE TABLE IF NOT EXISTS issues (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    priority    INTEGER NOT NULL DEFAULT 2,
    type        TEXT NOT NULL DEFAULT 'task',
    parent_id   TEXT REFERENCES issues(id) ON DELETE SET NULL,
    assignee    TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT,
    description TEXT DEFAULT '',
    notes       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS dependencies (
    issue_id       TEXT NOT NULL REFERENCES issues(id),
    depends_on_id  TEXT NOT NULL REFERENCES issues(id),
    type           TEXT NOT NULL DEFAULT 'blocks',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (issue_id, depends_on_id)
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    event_type TEXT NOT NULL,
    actor      TEXT DEFAULT '',
    old_value  TEXT,
    new_value  TEXT,
    comment    TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_issue ON events(issue_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_issue_time ON events(issue_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup
  ON events(issue_id, event_type, actor,
    coalesce(old_value,''), coalesce(new_value,''), created_at);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    author     TEXT DEFAULT '',
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id, created_at);

CREATE TABLE IF NOT EXISTS labels (
    issue_id TEXT NOT NULL REFERENCES issues(id),
    label    TEXT NOT NULL,
    PRIMARY KEY (issue_id, label)
);

CREATE TABLE IF NOT EXISTS type_templates (
    type          TEXT PRIMARY KEY,
    pack          TEXT NOT NULL DEFAULT 'core',
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packs (
    name          TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT 1
);

-- FTS5 full-text search with sync triggers
CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING fts5(
    title, description, content='issues', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS issues_fts_insert AFTER INSERT ON issues BEGIN
    INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS issues_fts_update AFTER UPDATE OF title, description ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, description)
        VALUES('delete', old.rowid, old.title, old.description);
    INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS issues_fts_delete AFTER DELETE ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, description)
        VALUES('delete', old.rowid, old.title, old.description);
END;
"""

CURRENT_SCHEMA_VERSION = 5
