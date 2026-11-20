from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.services.ready import occupancy, resource_active

# 争用口径（全局唯一）：串行第一胜
#   开窗必须在写锁（BEGIN IMMEDIATE，见 app.db.transaction）内完成双资源检查与插入。
#   第一笔提交者落 open、批窗 live、在途 +1；
#   后到者整笔失败（不留 open、不动批窗与在途），并在同一事务里写争用记录。
CONFLICT_MODE = "reject"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sync_watermark(conn: sqlite3.Connection, key: str, value: int) -> int:
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, _now()),
    )
    return value


def _open_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM leases WHERE status='open'"
    ).fetchone()["c"]


def sync_open_leases(conn: sqlite3.Connection) -> int:
    """在途数一律由租约表重算，水位只是它的缓存，杜绝 ±1 漂移。"""
    return _sync_watermark(conn, "open_leases", _open_count(conn))


def sync_conflict_count(conn: sqlite3.Connection) -> int:
    return _sync_watermark(
        conn,
        "conflict_count",
        conn.execute("SELECT COUNT(*) c FROM conflicts").fetchone()["c"],
    )


def _conflict_mode() -> str:
    """全局唯一口径：串行第一胜。设置页不可切换到「双方均败」。"""
    return CONFLICT_MODE


def _receipt(conn: sqlite3.Connection, session_code: str, action: str) -> int:
    """在写锁内分配回执序号：seq 的先后就是拿到锁的先后。"""
    cur = conn.execute(
        "INSERT INTO receipts(session_code, action, created_at) VALUES(?,?,?)",
        (session_code, action, _now()),
    )
    return int(cur.lastrowid)


def _record_conflict(
    conn: sqlite3.Connection,
    *,
    kind: str,
    session: sqlite3.Row,
    resource_code: str,
    reason: str,
    attempt_seq: int,
    peer: sqlite3.Row | None,
) -> None:
    conn.execute(
        "INSERT INTO conflicts(kind, session_code, resource_code, day, start_min, end_min, "
        "reason, created_at, attempt_seq, peer_seq, peer_session_code) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            kind,
            session["code"],
            resource_code,
            session["day"],
            session["start_min"],
            session["end_min"],
            reason,
            _now(),
            attempt_seq,
            peer["receipt_seq"] if peer else None,
            peer["session_code"] if peer else None,
        ),
    )


def open_lease(conn: sqlite3.Connection, session_code: str) -> dict:
    """调用方必须已持有写锁（app.db.transaction 的 BEGIN IMMEDIATE）。"""
    seq = _receipt(conn, session_code, "open")

    session = conn.execute(
        "SELECT * FROM sessions WHERE code=?", (session_code,)
    ).fetchone()
    if session is None:
        raise ValueError(f"批窗不存在：{session_code}")

    if session["status"] == "live":
        # 同一批窗被并发开两次：后到者同样整笔失败，但要留痕可查
        holder = conn.execute(
            "SELECT lease_no, receipt_seq, session_code FROM leases "
            "WHERE session_code=? AND status='open' ORDER BY id DESC LIMIT 1",
            (session_code,),
        ).fetchone()
        _record_conflict(
            conn,
            kind="session",
            session=session,
            resource_code=session_code,
            reason="批窗已在上课，重复开窗",
            attempt_seq=seq,
            peer=holder,
        )
        sync_conflict_count(conn)
        return {
            "ok": False,
            "error": "批窗已在上课，勿重复开窗",
            "conflict_mode": _conflict_mode(),
            "receipt_seq": seq,
            "peer_seq": holder["receipt_seq"] if holder else None,
            "peer_session_code": holder["session_code"] if holder else None,
        }
    if session["status"] == "done":
        raise ValueError("批窗已结束")

    # —— 双资源检查：算力槽与执行器，逐一查占用索引 ——
    losers: list[tuple[str, str, str, sqlite3.Row]] = []
    for kind, col, label in (
        ("room", "room_code", "算力槽"),
        ("teacher", "teacher_code", "执行器"),
    ):
        code = session[col]
        if not resource_active(conn, kind, code):
            losers.append((kind, code, f"{label}未启用", None))
            continue
        held = occupancy(
            conn,
            kind=kind,
            resource_code=code,
            day=session["day"],
            start_min=session["start_min"],
            end_min=session["end_min"],
        )
        if held is not None:
            losers.append(
                (kind, code, f"{label}已被租约 {held['lease_no']} 占用", held)
            )

    if losers:
        for kind, code, reason, held in losers:
            _record_conflict(
                conn,
                kind=kind,
                session=session,
                resource_code=code,
                reason=reason,
                attempt_seq=seq,
                peer=held,
            )
        sync_conflict_count(conn)
        first_peer = next((h for _, _, _, h in losers if h is not None), None)
        return {
            "ok": False,
            "error": "；".join(reason for _, _, reason, _ in losers),
            "conflict_mode": _conflict_mode(),
            "receipt_seq": seq,
            "peer_seq": first_peer["receipt_seq"] if first_peer else None,
            "peer_session_code": first_peer["session_code"] if first_peer else None,
        }

    # —— 插入：先占位（唯一索引兜底），再落租约与批窗状态 ——
    n = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 n FROM leases").fetchone()["n"]
    lease_no = f"L-{session['day'].replace('-', '')}-{n:04d}"

    for kind, col, label in (
        ("room", "room_code", "算力槽"),
        ("teacher", "teacher_code", "执行器"),
    ):
        # 每笔占位单独设 SAVEPOINT：索引拒绝时只回滚占位，保留回执与争用记录
        conn.execute(f"SAVEPOINT lock_{kind}")
        try:
            conn.execute(
                "INSERT INTO resource_locks"
                "(lease_no, session_code, receipt_seq, kind, resource_code, day, start_min, end_min) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    lease_no,
                    session_code,
                    seq,
                    kind,
                    session[col],
                    session["day"],
                    session["start_min"],
                    session["end_min"],
                ),
            )
            conn.execute(f"RELEASE lock_{kind}")
        except sqlite3.IntegrityError:
            # 双资源占用索引兜底：即便写锁之外还有别的写入路径，也不会出现双 open。
            # 索引按「同资源同时段边界完全相同」唯一，重叠窗口由写锁内的检查拦下。
            conn.execute(f"ROLLBACK TO lock_{kind}")
            conn.execute(f"RELEASE lock_{kind}")
            # 本笔此前已占下的另一维度也要撤掉，否则会留下没有租约的占位行
            conn.execute("DELETE FROM resource_locks WHERE lease_no=?", (lease_no,))
            reason = f"双资源占用索引拒绝：{label} {session[col]} 同一时段已有占用"
            _record_conflict(
                conn,
                kind=kind,
                session=session,
                resource_code=session[col],
                reason=reason,
                attempt_seq=seq,
                peer=None,
            )
            sync_conflict_count(conn)
            # 整笔失败：不落租约、不改批窗、不动在途，只留争用记录
            return {
                "ok": False,
                "error": reason,
                "conflict_mode": _conflict_mode(),
                "receipt_seq": seq,
                "peer_seq": None,
                "peer_session_code": None,
                "index_guard": True,
            }

    conn.execute(
        "INSERT INTO leases(lease_no, session_code, room_code, teacher_code, day, start_min, "
        "end_min, status, opened_at, receipt_seq) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            lease_no,
            session_code,
            session["room_code"],
            session["teacher_code"],
            session["day"],
            session["start_min"],
            session["end_min"],
            "open",
            _now(),
            seq,
        ),
    )
    conn.execute(
        "UPDATE sessions SET status='live' WHERE code=?", (session_code,)
    )
    open_n = sync_open_leases(conn)
    return {
        "ok": True,
        "lease_no": lease_no,
        "session_code": session_code,
        "status": "open",
        "receipt_seq": seq,
        "open_leases": open_n,
    }


def close_lease(conn: sqlite3.Connection, lease_no: str) -> dict:
    lease = conn.execute(
        "SELECT * FROM leases WHERE lease_no=?", (lease_no,)
    ).fetchone()
    if lease is None:
        raise ValueError(f"租约不存在：{lease_no}")
    if lease["status"] != "open":
        raise ValueError("租约不在占用态")
    conn.execute(
        "UPDATE leases SET status='closed', closed_at=? WHERE lease_no=?",
        (_now(), lease_no),
    )
    conn.execute("DELETE FROM resource_locks WHERE lease_no=?", (lease_no,))
    conn.execute(
        "UPDATE sessions SET status='done' WHERE code=?",
        (lease["session_code"],),
    )
    open_n = sync_open_leases(conn)
    return {
        "lease_no": lease_no,
        "status": "closed",
        "open_leases": open_n,
    }
