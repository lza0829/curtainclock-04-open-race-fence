import { useEffect, useState } from "react";
import { api } from "../api";

const kindLabel = { room: "算力槽", teacher: "执行器", session: "批窗", ready: "就绪" };

export default function Conflicts() {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .conflicts()
      .then(setRows)
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="msg err">{err}</div>;

  return (
    <section className="panel">
      <h2>冲突桶</h2>
      <p className="hint">
        开窗在写锁内串行判定：先提交者占位成功，后到者整笔回滚并在此留痕。
        回执序号即取锁顺序，失败方的「争用序号」就是胜方的「回执序号」。
      </p>
      <table>
        <thead>
          <tr>
            <th>类型</th>
            <th>批窗</th>
            <th>资源</th>
            <th>原因</th>
            <th>争用回执</th>
            <th>对手回执</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={7}>暂无冲突</td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{kindLabel[r.kind] || r.kind}</td>
              <td>{r.session_code}</td>
              <td>{r.resource_code}</td>
              <td>{r.reason}</td>
              <td>
                {r.attempt_seq ? (
                  <span className="tag bad">#{r.attempt_seq}</span>
                ) : (
                  "—"
                )}
              </td>
              <td>
                {r.peer_seq ? (
                  <span className="tag live">
                    #{r.peer_seq} {r.peer_session_code}
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td>{r.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
