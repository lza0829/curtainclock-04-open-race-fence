# -*- coding: utf-8 -*-
"""重复并发压力：每轮四路抢同一算力槽/执行器组合的同一窗口。

不变量：
  * 每轮恰好一路成功；
  * 每个 (资源, 窗口) 至多一笔 open；
  * 每一笔 409 都能在争用桶里找到对应回执（attempt_seq），不存在无痕失败；
  * 在途 == 开放租约数，且 == 占用索引 / 2。
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
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw}


def mk(code, room, teacher, start, end):
    st, body = call("POST", "/sessions", {
        "code": code, "title": code, "subject": "ETL",
        "room_code": room, "teacher_code": teacher,
        "day": "2026-11-20", "start_min": start, "end_min": end,
    })
    assert st == 200, (code, st, body)


def main() -> int:
    ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    bad = 0
    for r in range(ROUNDS):
        start = 1200 + r * 30
        end = start + 30
        codes = [f"P{r}-{i}" for i in range(4)]
        for i, c in enumerate(codes):
            mk(c, "S-ETL-01", "E-PIPE-01", start, end)
        barrier = threading.Barrier(len(codes))
        out = [None] * len(codes)

        def go(i, code):
            barrier.wait()
            out[i] = call("POST", "/leases/open", {"session_code": code})

        ts = [threading.Thread(target=go, args=(i, c)) for i, c in enumerate(codes)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        ok = [b for st, b in out if st == 200]
        lose = [b for st, b in out if st == 409]
        if len(ok) != 1 or len(lose) != len(codes) - 1:
            print(f"round {r}: bad split", out)
            bad += 1
            continue
        win_seq = ok[0]["receipt_seq"]
        win_code = ok[0]["session_code"]
        for b in lose:
            d = b.get("detail") or {}
            if d.get("peer_seq") != win_seq or d.get("peer_session_code") != win_code:
                print(f"round {r}: untraceable 409", d, "win", win_seq, win_code)
                bad += 1
        print(f"round {r}: win={win_code} seq={win_seq} losers={len(lose)}")

    # 全库不变量
    _, leases = call("GET", "/leases")
    _, dash = call("GET", "/dashboard")
    _, conflicts = call("GET", "/conflicts")
    _, receipts = call("GET", "/receipts")
    open_rows = [x for x in leases if x["status"] == "open"]
    seen = {}
    for x in open_rows:
        for res in (("room", x["room_code"]), ("teacher", x["teacher_code"])):
            key = res + (x["day"], x["start_min"], x["end_min"])
            seen[key] = seen.get(key, 0) + 1
    dup = {k: v for k, v in seen.items() if v > 1}
    print("dup resource-window:", dup)
    print("open:", dash["open_leases"], "rows:", len(open_rows),
          "locks:", dash["resource_locks"], "consistent:", dash["consistent"])
    print("conflicts:", len(conflicts), "receipts:", len(receipts))
    if dup or dash["open_leases"] != len(open_rows) or not dash["consistent"]:
        bad += 1
    # 每一笔 409 尝试都应有 conflict 行
    lose_receipts = len(receipts) - len(open_rows)
    if len(conflicts) < lose_receipts:
        print("silent failures:", lose_receipts, "conflicts:", len(conflicts))
        bad += 1
    print("STRESS_OK" if not bad else f"STRESS_FAIL({bad})")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
