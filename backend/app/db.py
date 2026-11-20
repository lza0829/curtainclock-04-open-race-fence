from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  capacity INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS teachers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  subject TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  subject TEXT NOT NULL,
  room_code TEXT NOT NULL,
  teacher_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS leases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lease_no TEXT NOT NULL UNIQUE,
  session_code TEXT NOT NULL,
  room_code TEXT NOT NULL,
  teacher_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  status TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  receipt_seq INTEGER
);
-- 回执序号：在写锁内分配，序号即取锁顺序；争用双方都可在冲突桶里查到
CREATE TABLE IF NOT EXISTS receipts (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_code TEXT NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL
);
-- 双资源占用索引：算力槽与执行器共用一张占用表，(kind, resource, day, 时段) 唯一
-- 是应用层互斥之外的数据库级兜底，任何路径都无法插出第二笔同资源同窗口占用
CREATE TABLE IF NOT EXISTS resource_locks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lease_no TEXT NOT NULL,
  session_code TEXT NOT NULL,
  receipt_seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  resource_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  UNIQUE (kind, resource_code, day, start_min, end_min)
);
CREATE INDEX IF NOT EXISTS ix_resource_locks_lookup
  ON resource_locks(kind, resource_code, day, start_min, end_min);
CREATE INDEX IF NOT EXISTS ix_leases_open
  ON leases(status, day, start_min, end_min);
CREATE TABLE IF NOT EXISTS conflicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  session_code TEXT NOT NULL,
  resource_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  attempt_seq INTEGER,
  peer_seq INTEGER,
  peer_session_code TEXT
);
CREATE TABLE IF NOT EXISTS watermarks (
  key TEXT PRIMARY KEY,
  value INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# CREATE TABLE IF NOT EXISTS 不会给既有表补列，旧库走这里
_COLUMN_MIGRATIONS = (
    ("leases", "receipt_seq", "INTEGER"),
    ("conflicts", "attempt_seq", "INTEGER"),
    ("conflicts", "peer_seq", "INTEGER"),
    ("conflicts", "peer_session_code", "TEXT"),
)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None：事务由 transaction() 显式管理，避免隐式 BEGIN 与
    # 手动 BEGIN IMMEDIATE 混用
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """写锁事务。

    BEGIN IMMEDIATE 在进入时立刻取得数据库写锁，双资源检查、占用插入、
    租约插入、批窗状态与水位都在同一把锁里完成；并发的第二笔只能等
    第一笔提交后再进来，届时看到已占用并整笔失败。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _COLUMN_MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _backfill_locks(conn: sqlite3.Connection) -> None:
    """旧库若已有 open 租约，补齐占用索引行，保证索引与实际占用同源。"""
    rows = conn.execute("SELECT * FROM leases WHERE status='open'").fetchall()
    for r in rows:
        for kind, col in (("room", "room_code"), ("teacher", "teacher_code")):
            conn.execute(
                "INSERT OR IGNORE INTO resource_locks"
                "(lease_no, session_code, receipt_seq, kind, resource_code, day, start_min, end_min) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    r["lease_no"],
                    r["session_code"],
                    r["receipt_seq"] or 0,
                    kind,
                    r[col],
                    r["day"],
                    r["start_min"],
                    r["end_min"],
                ),
            )


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _backfill_locks(conn)
    finally:
        conn.close()
