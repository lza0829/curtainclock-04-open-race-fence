from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import connect
from app.services.lease import CONFLICT_MODE

router = APIRouter(tags=["settings"])


class ModeBody(BaseModel):
    conflict_mode: str


@router.get("/settings")
def get_settings():
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        data = {r["key"]: r["value"] for r in rows}
        # 争用口径全局只有一支：串行第一胜。库里的值以此为准。
        data["conflict_mode"] = CONFLICT_MODE
        return data
    finally:
        conn.close()


@router.post("/settings/conflict-mode")
def set_mode(body: ModeBody):
    if body.conflict_mode == CONFLICT_MODE:
        return {"ok": True, "conflict_mode": CONFLICT_MODE}
    raise HTTPException(
        409,
        {
            "ok": False,
            "error": f"争用口径固定为「串行第一胜」（{CONFLICT_MODE}），不支持切换",
            "conflict_mode": CONFLICT_MODE,
        },
    )
