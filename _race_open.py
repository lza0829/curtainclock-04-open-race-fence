# -*- coding: utf-8 -*-
"""同毫秒双开夹具：验证「串行第一胜」臂。

用法（服务需已起在 11100，且用干净库）：
    python -I _race_open.py

核对项：
  1. 同一算力槽/执行器同时段，库内至多一笔 open；
  2. 第一胜：恰好一路 200，另一路 409，且失败方整笔不留 open；
  3. 在途（dashboard）与 leases 表开放行数一致；
  4. 争用桶里能查到双方回执序号（attempt_seq / peer_seq）。
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:11100/api"


def call(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw}
        return e.code, payload


def mk(code: str, room: str, teacher: str, start: int, end: int) -> None:
    st, body = call(
        "POST",
        "/sessions",
        {
            "code": code,
            "title": code,
            "subject": "ETL",
            "room_code": room,
            "teacher_code": teacher,
            "day": "2026-11-20",
            "start_min": start,
            "end_min": end,
        },
    )
    assert st == 200, (code, st, body)


def race(codes: list[str]) -> list[tuple[int, dict]]:
    """两路在 barrier 处对齐后同时发请求，尽量落在同一毫秒。"""
    barrier = threading.Barrier(len(codes))
    out: list[tuple[int, dict]] = [None] * len(codes)  # type: ignore[list-item]

    def go(i: int, code: str) -> None:
        barrier.wait()
        out[i] = call("POST", "/leases/open", {"session_code": code})

    ts = [threading.Thread(target=go, args=(i, c)) for i, c in enumerate(codes)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def open_count(session_codes: list[str]) -> int:
    _, leases = call("GET", "/leases")
    return sum(
        1 for r in leases if r["status"] == "open" and r["session_code"] in session_codes
    )


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    # 场景 1：同算力槽（不同执行器）同窗口，两路同时开
    mk("RACE-A", "S-ETL-01", "E-PIPE-01", 540, 630)
    mk("RACE-B", "S-ETL-01", "E-PIPE-02", 540, 630)
    print("场景 1 · 同算力槽双开")
    res = race(["RACE-A", "RACE-B"])
    ok_n = sum(1 for st, _ in res if st == 200)
    print("   responses:", res)
    check(ok_n == 1, f"恰好一路成功（实际 {ok_n}）")
    check(open_count(["RACE-A", "RACE-B"]) == 1, "同资源库内至多一笔 open")

    win = next(((st, b) for st, b in res if st == 200), None)
    lose = next(((st, b) for st, b in res if st == 409), None)
    check(win is not None and lose is not None, "另一路以 409 整笔失败")
    if win and lose:
        win_seq = win[1]["receipt_seq"]
        lose_detail = lose[1].get("detail") or {}
        check(
            lose_detail.get("peer_seq") == win_seq,
            f"失败方回执 peer_seq({lose_detail.get('peer_seq')}) == 胜方 seq({win_seq})",
        )
        check(
            lose_detail.get("peer_session_code") == win[1]["session_code"],
            "失败方回执指向胜方批窗",
        )

    # 场景 2：不同算力槽、同执行器，同窗口
    mk("RACE-C", "S-ETL-02", "E-BI-01", 780, 840)
    mk("RACE-D", "S-BI-01", "E-BI-01", 780, 840)
    print("场景 2 · 同执行器双开")
    res2 = race(["RACE-C", "RACE-D"])
    print("   responses:", res2)
    check(sum(1 for st, _ in res2 if st == 200) == 1, "恰好一路成功")
    check(open_count(["RACE-C", "RACE-D"]) == 1, "同资源库内至多一笔 open")

    # 场景 3：同一批窗被并发开两次
    mk("RACE-E", "S-BI-02", "E-BI-02", 900, 960)
    print("场景 3 · 同一批窗双开")
    res3 = race(["RACE-E", "RACE-E"])
    print("   responses:", res3)
    check(sum(1 for st, _ in res3 if st == 200) == 1, "同一批窗只开出一笔")

    # 对账：在途与租约一致、争用记录可查双方序号
    st, dash = call("GET", "/dashboard")
    check(st == 200, "dashboard 可读")
    _, leases = call("GET", "/leases")
    open_rows = [r for r in leases if r["status"] == "open"]
    check(
        dash["open_leases"] == len(open_rows),
        f"在途({dash['open_leases']}) == 开放租约({len(open_rows)})",
    )
    check(dash["resource_locks"] == len(open_rows) * 2, "占用索引 = 在途 × 2")
    check(dash["consistent"] is True, "dashboard.consistent 为真")

    _, conflicts = call("GET", "/conflicts")
    traced = [c for c in conflicts if c.get("attempt_seq") and c.get("peer_seq")]
    check(len(traced) >= 2, f"争用记录带双方回执序号（{len(traced)} 条）")
    print("   conflicts:", json.dumps(traced[:4], ensure_ascii=False, indent=2))

    # 无痕失败排查：每一笔 409（除资源未启用外）都应能对上一条 conflict
    _, receipts = call("GET", "/receipts")
    check(len(receipts) >= 3, f"回执表已记账（{len(receipts)} 条）")

    print()
    if failures:
        print("RACE_FAIL:", len(failures))
        for f in failures:
            print(" -", f)
        return 1
    print("RACE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
