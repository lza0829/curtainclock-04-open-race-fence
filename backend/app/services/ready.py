from __future__ import annotations

import sqlite3


def room_active(conn: sqlite3.Connection, room_code: str) -> bool:
    row = conn.execute(
        "SELECT active FROM rooms WHERE code=?", (room_code,)
    ).fetchone()
    return bool(row and row["active"] == 1)


def teacher_active(conn: sqlite3.Connection, teacher_code: str) -> bool:
    row = conn.execute(
        "SELECT active FROM teachers WHERE code=?", (teacher_code,)
    ).fetchone()
    return bool(row and row["active"] == 1)


def resource_active(conn: sqlite3.Connection, kind: str, code: str) -> bool:
    return room_active(conn, code) if kind == "room" else teacher_active(conn, code)


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def occupancy(
    conn: sqlite3.Connection,
    *,
    kind: str,
    resource_code: str,
    day: str,
    start_min: int,
    end_min: int,
    exclude_session: str | None = None,
) -> sqlite3.Row | None:
    """查某资源在给定时段是否已被占用。

    占用的唯一事实源是 resource_locks：开窗在同一把写锁内写入，关窗删除。
    行存在即占用，不存在即空闲，没有 status 可与租约表打架。
    """
    rows = conn.execute(
        "SELECT * FROM resource_locks WHERE kind=? AND resource_code=? AND day=?",
        (kind, resource_code, day),
    ).fetchall()
    for r in rows:
        if exclude_session and r["session_code"] == exclude_session:
            continue
        if overlap(start_min, end_min, r["start_min"], r["end_min"]):
            return r
    return None

def resource_busy(
    conn: sqlite3.Connection,
    *,
    kind: str,
    resource_code: str,
    day: str,
    start_min: int,
    end_min: int,
    exclude_session: str | None = None,
) -> sqlite3.Row | None:
    return occupancy(
        conn,
        kind=kind,
        resource_code=resource_code,
        day=day,
        start_min=start_min,
        end_min=end_min,
        exclude_session=exclude_session,
    )


def readiness(conn: sqlite3.Connection, session: sqlite3.Row) -> dict:
    reasons: list[str] = []
    if not room_active(conn, session["room_code"]):
        reasons.append("算力槽未启用")
    if not teacher_active(conn, session["teacher_code"]):
        reasons.append("执行器未启用")
    busy_room = occupancy(
        conn,
        kind="room",
        resource_code=session["room_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
        exclude_session=session["code"],
    )
    if busy_room:
        reasons.append(f"算力槽已被租约 {busy_room['lease_no']} 占用")
    busy_teacher = occupancy(
        conn,
        kind="teacher",
        resource_code=session["teacher_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
        exclude_session=session["code"],
    )
    if busy_teacher:
        reasons.append(f"执行器已被租约 {busy_teacher['lease_no']} 占用")
    return {"ready": len(reasons) == 0, "reasons": reasons}
