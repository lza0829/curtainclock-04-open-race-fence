import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

function byResource(locks, kind) {
  const map = new Map();
  for (const l of locks) {
    if (l.kind !== kind) continue;
    if (!map.has(l.resource_code)) map.set(l.resource_code, []);
    map.get(l.resource_code).push(l);
  }
  return map;
}

function SlotTable({ rows, kind, extraKey, extraLabel, locks }) {
  const busy = byResource(locks, kind);
  return (
    <table>
      <thead>
        <tr>
          <th>编码</th>
          <th>名称</th>
          <th>{extraLabel}</th>
          <th>状态</th>
          <th>当前占用</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const holds = busy.get(r.code) || [];
          return (
            <tr key={r.code}>
              <td>{r.code}</td>
              <td>{r.name}</td>
              <td>{r[extraKey]}</td>
              <td>{r.active ? "启用" : "停用"}</td>
              <td>
                {holds.length === 0 ? (
                  <span className="tag">空闲</span>
                ) : (
                  holds.map((h) => (
                    <div key={h.id}>
                      <span className="tag bad">
                        {h.session_code} {fmtMin(h.start_min)}–{fmtMin(h.end_min)}
                      </span>{" "}
                      <small>
                        {h.lease_no} · 回执 #{h.receipt_seq}
                      </small>
                    </div>
                  ))
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function Resources() {
  const [rooms, setRooms] = useState([]);
  const [teachers, setTeachers] = useState([]);
  const [locks, setLocks] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([api.rooms(), api.teachers(), api.occupancy()])
      .then(([r, t, l]) => {
        setRooms(r);
        setTeachers(t);
        setLocks(l);
      })
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="msg err">{err}</div>;

  return (
    <>
      <section className="panel">
        <h2>算力槽</h2>
        <p className="hint">
          占用来自身兼双资源的占用索引，与总览在途同源；空窗只在关窗释放后消失。
        </p>
        <SlotTable
          rows={rooms}
          kind="room"
          extraKey="capacity"
          extraLabel="容量"
          locks={locks}
        />
      </section>
      <section className="panel">
        <h2>执行器</h2>
        <SlotTable
          rows={teachers}
          kind="teacher"
          extraKey="subject"
          extraLabel="作业类型"
          locks={locks}
        />
      </section>
    </>
  );
}
