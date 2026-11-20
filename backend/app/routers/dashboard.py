from __future__ import annotations

from fastapi import APIRouter

from app.db import connect

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard():
    conn = connect()
    try:
        day_row = conn.execute(
            "SELECT value FROM settings WHERE key='fixture_day'"
        ).fetchone()
        day = day_row["value"] if day_row else None
        # 在途与冲突一律由事实表重算，水位只作为同源缓存展示
        open_leases = conn.execute(
            "SELECT COUNT(*) c FROM leases WHERE status='open'"
        ).fetchone()["c"]
        conflicts = conn.execute(
            "SELECT COUNT(*) c FROM conflicts"
        ).fetchone()["c"]
        live_n = conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE status='live'"
        ).fetchone()["c"]
        draft_n = conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE status='draft'"
        ).fetchone()["c"]
        lock_n = conn.execute(
            "SELECT COUNT(*) c FROM resource_locks"
        ).fetchone()["c"]
        wm = {
            r["key"]: int(r["value"])
            for r in conn.execute("SELECT key, value FROM watermarks")
        }
        return {
            "fixture_day": day,
            "open_leases": open_leases,
            "conflict_count": conflicts,
            "live_sessions": live_n,
            "draft_sessions": draft_n,
            "resource_locks": lock_n,
            "watermarks": wm,
            # 在途 = 开放租约，且每笔租约占算力槽、执行器各一行；水位缓存也须吻合
            # （旧库刚迁移、尚未开过窗时水位可能还缺键，按事实表算一致）
            "consistent": wm.get("open_leases", open_leases) == open_leases
            and wm.get("conflict_count", conflicts) == conflicts
            and lock_n == open_leases * 2,
        }
    finally:
        conn.close()
