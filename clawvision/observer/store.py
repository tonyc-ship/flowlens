"""SQLite-backed durable storage for Observer captures and project memory."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from .paths import ObserverPaths


class ObserverStore:
    """Thin SQLite access layer for Observer."""

    def __init__(self, paths: ObserverPaths):
        self.paths = paths
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.paths.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    app_name TEXT,
                    window_title TEXT,
                    browser_url TEXT,
                    ocr_text TEXT,
                    screenshot_path TEXT,
                    content_summary TEXT,
                    visual_summary TEXT,
                    capture_reason TEXT,
                    is_keyframe INTEGER NOT NULL DEFAULT 0,
                    diff_regions_json TEXT,
                    changed_area_ratio REAL,
                    ocr_scope TEXT,
                    visual_scope TEXT,
                    visual_model TEXT,
                    capture_image_ms REAL,
                    diff_ms REAL,
                    save_ms REAL,
                    ocr_ms REAL,
                    visual_ms REAL,
                    total_ms REAL,
                    source TEXT NOT NULL DEFAULT 'observer',
                    created_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_memories (
                    project_name TEXT PRIMARY KEY,
                    current_json TEXT NOT NULL,
                    history_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_observer_captures_timestamp ON captures(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_observer_captures_app_name ON captures(app_name)")
            self._ensure_capture_columns(conn)

    @staticmethod
    def _ensure_capture_columns(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(captures)").fetchall()
        }
        desired = {
            "content_summary": "TEXT",
            "visual_summary": "TEXT",
            "capture_reason": "TEXT",
            "is_keyframe": "INTEGER NOT NULL DEFAULT 0",
            "diff_regions_json": "TEXT",
            "changed_area_ratio": "REAL",
            "ocr_scope": "TEXT",
            "visual_scope": "TEXT",
            "visual_model": "TEXT",
            "capture_image_ms": "REAL",
            "diff_ms": "REAL",
            "save_ms": "REAL",
            "ocr_ms": "REAL",
            "visual_ms": "REAL",
            "total_ms": "REAL",
            "source": "TEXT NOT NULL DEFAULT 'observer'",
            "created_at": "TEXT",
        }
        for name, col_type in desired.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE captures ADD COLUMN {name} {col_type}")

    def insert_capture(
        self,
        *,
        timestamp: str,
        app_name: str,
        window_title: str,
        browser_url: str,
        ocr_text: str,
        screenshot_path: str | None,
        content_summary: str | None = None,
        visual_summary: str | None = None,
        capture_reason: str,
        is_keyframe: bool,
        diff_regions_json: str | None = None,
        changed_area_ratio: float | None = None,
        ocr_scope: str | None = None,
        visual_scope: str | None = None,
        visual_model: str | None = None,
        capture_image_ms: float | None = None,
        diff_ms: float | None = None,
        save_ms: float | None = None,
        ocr_ms: float | None = None,
        visual_ms: float | None = None,
        total_ms: float | None = None,
        source: str = "observer",
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO captures (
                    timestamp, app_name, window_title, browser_url, ocr_text,
                    screenshot_path, content_summary, visual_summary, capture_reason, is_keyframe,
                    diff_regions_json, changed_area_ratio, ocr_scope, visual_scope, visual_model,
                    capture_image_ms, diff_ms, save_ms, ocr_ms, visual_ms, total_ms,
                    source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    app_name,
                    window_title,
                    browser_url,
                    ocr_text,
                    screenshot_path,
                    content_summary,
                    visual_summary,
                    capture_reason,
                    1 if is_keyframe else 0,
                    diff_regions_json,
                    changed_area_ratio,
                    ocr_scope,
                    visual_scope,
                    visual_model,
                    capture_image_ms,
                    diff_ms,
                    save_ms,
                    ocr_ms,
                    visual_ms,
                    total_ms,
                    source,
                    datetime.now().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def update_capture_summaries(self, capture_id: int, *, content_summary: str | None, visual_summary: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE captures SET content_summary = ?, visual_summary = ? WHERE id = ?",
                (content_summary, visual_summary, capture_id),
            )

    def latest_processed_capture(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT ocr_text, content_summary, visual_summary
                FROM captures
                WHERE content_summary IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def latest_capture(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, timestamp, app_name, window_title, browser_url, ocr_text, screenshot_path,
                       content_summary, visual_summary, diff_regions_json, changed_area_ratio,
                       ocr_scope, visual_scope, visual_model,
                       capture_image_ms, diff_ms, save_ms, ocr_ms, visual_ms, total_ms,
                       capture_reason, is_keyframe
                FROM captures
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def pending_extractions(self, *, limit: int | None = None, include_visual: bool = False) -> list[dict]:
        query = [
            "SELECT id, ocr_text, screenshot_path, content_summary, visual_summary",
            "FROM captures",
        ]
        if include_visual:
            query.append(
                "WHERE content_summary IS NULL OR (visual_summary IS NULL AND screenshot_path IS NOT NULL)"
            )
        else:
            query.append("WHERE content_summary IS NULL")
        query.append("ORDER BY id ASC")
        params: list[object] = []
        if limit:
            query.append("LIMIT ?")
            params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute("\n".join(query), params).fetchall()
        return [dict(row) for row in rows]

    def get_timeline(self, hours: int | None = None) -> list[dict]:
        query = [
            "SELECT id, timestamp, app_name, window_title, browser_url, ocr_text,",
            "       content_summary, visual_summary, screenshot_path, diff_regions_json,",
            "       changed_area_ratio, ocr_scope, visual_scope, visual_model,",
            "       capture_image_ms, diff_ms, save_ms, ocr_ms, visual_ms, total_ms",
            "FROM captures",
        ]
        params: list[object] = []
        if hours:
            since = (datetime.now() - timedelta(hours=hours)).isoformat()
            query.append("WHERE timestamp > ?")
            params.append(since)
        query.append("ORDER BY timestamp ASC")
        with self.connect() as conn:
            rows = conn.execute("\n".join(query), params).fetchall()
        return [dict(row) for row in rows]

    def search_captures(
        self,
        keyword: str,
        *,
        app_filter: str | None = None,
        hours: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        conditions = [
            "("
            "app_name LIKE ? OR window_title LIKE ? OR browser_url LIKE ? OR "
            "ocr_text LIKE ? OR content_summary LIKE ? OR visual_summary LIKE ?"
            ")"
        ]
        token = f"%{keyword}%"
        params: list[object] = [token, token, token, token, token, token]
        if app_filter:
            conditions.append("app_name LIKE ?")
            params.append(f"%{app_filter}%")
        if hours:
            since = (datetime.now() - timedelta(hours=hours)).isoformat()
            conditions.append("timestamp > ?")
            params.append(since)
        where = " AND ".join(conditions)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, timestamp, app_name, window_title, browser_url,
                       content_summary, visual_summary, changed_area_ratio,
                       total_ms, ocr_ms, visual_ms,
                       SUBSTR(ocr_text, 1, 300) AS ocr_preview, screenshot_path
                FROM captures
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params + [limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int | float]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS capture_count,
                    COUNT(content_summary) AS content_summary_count,
                    COUNT(visual_summary) AS visual_summary_count,
                    AVG(total_ms) AS avg_total_ms,
                    AVG(ocr_ms) AS avg_ocr_ms,
                    AVG(visual_ms) AS avg_visual_ms,
                    MAX(total_ms) AS max_total_ms
                FROM captures
                """
            ).fetchone()
            memory_count = conn.execute(
                "SELECT COUNT(*) AS count FROM project_memories"
            ).fetchone()["count"]
        return {
            "capture_count": int(row["capture_count"] if row else 0),
            "content_summary_count": int(row["content_summary_count"] if row else 0),
            "visual_summary_count": int(row["visual_summary_count"] if row else 0),
            "project_memory_count": int(memory_count),
            "avg_total_ms": round(float(row["avg_total_ms"] or 0), 1) if row else 0,
            "avg_ocr_ms": round(float(row["avg_ocr_ms"] or 0), 1) if row else 0,
            "avg_visual_ms": round(float(row["avg_visual_ms"] or 0), 1) if row else 0,
            "max_total_ms": round(float(row["max_total_ms"] or 0), 1) if row else 0,
        }

    def upsert_project_memories(self, new_memories: dict) -> dict:
        if not new_memories:
            return {}
        now = datetime.now().isoformat()
        with self.connect() as conn:
            for project_name, current in new_memories.items():
                existing = conn.execute(
                    """
                    SELECT current_json, history_json, created_at, updated_at
                    FROM project_memories
                    WHERE project_name = ?
                    """,
                    (project_name,),
                ).fetchone()
                if existing:
                    history = json.loads(existing["history_json"] or "[]")
                    prior_current = json.loads(existing["current_json"] or "{}")
                    if prior_current:
                        history.append(
                            {
                                "timestamp": existing["updated_at"] or existing["created_at"],
                                "snapshot": prior_current,
                            }
                        )
                        history = history[-5:]
                    conn.execute(
                        """
                        UPDATE project_memories
                        SET current_json = ?, history_json = ?, updated_at = ?
                        WHERE project_name = ?
                        """,
                        (
                            json.dumps(current, ensure_ascii=False),
                            json.dumps(history, ensure_ascii=False),
                            now,
                            project_name,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO project_memories (
                            project_name, current_json, history_json, created_at, updated_at
                        )
                        VALUES (?, ?, '[]', ?, ?)
                        """,
                        (
                            project_name,
                            json.dumps(current, ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
        return self.get_project_memories()

    def get_project_memories(self, project: str | None = None) -> dict:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT project_name, current_json, history_json, created_at, updated_at
                FROM project_memories
                ORDER BY updated_at DESC
                """
            ).fetchall()

        data = {
            row["project_name"]: {
                "created": row["created_at"],
                "updated": row["updated_at"],
                "current": json.loads(row["current_json"] or "{}"),
                "history": json.loads(row["history_json"] or "[]"),
            }
            for row in rows
        }

        if not project:
            return data
        if project in data:
            return {project: data[project]}
        for key in data:
            if project.lower() in key.lower():
                return {key: data[key]}
        return {}
