from __future__ import annotations

from fastapi import APIRouter

from app.db import connect

router = APIRouter(tags=["resources"])


@router.get("/rooms")
def list_rooms():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT code, name, capacity, active FROM rooms ORDER BY code"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/teachers")
def list_teachers():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT code, name, subject, active FROM teachers ORDER BY code"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/occupancy")
def list_occupancy():
    """占用索引：算力槽与执行器共用一张表，槽位页/执行器页与总览同源。"""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT l.*, s.title AS session_title "
            "FROM resource_locks l LEFT JOIN sessions s ON s.code=l.session_code "
            "ORDER BY l.day, l.start_min, l.kind, l.resource_code"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
