"""Accès SQLite. La base (app.db) est créée au démarrage depuis schema.sql."""

import sqlite3
from pathlib import Path

DB_PATH = Path("app.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(Path("schema.sql").read_text(encoding="utf-8"))
